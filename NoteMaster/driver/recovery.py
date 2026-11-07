"""Проверка, восстановление и резервное копирование JSON-хранилища.

Модуль отвечает на вопрос «что делать, если файл заметок повреждён»:

* файла нет — создаём пустой список;
* файл пуст — считаем, что заметок нет;
* файл содержит BOM или битые байты — читаем максимально терпимо;
* JSON сломан — пытаемся вытащить уцелевшие объекты ``{...}``;
* структура неверная (не список) — пробуем восстановить, иначе начинаем заново;
* перед любым изменением исходник копируется в ``notes_backup.json``.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.errors import StorageError

logger = logging.getLogger(__name__)

#: Имя файла резервной копии (лежит рядом с notes.json).
BACKUP_FILENAME = "notes_backup.json"


# --------------------------------------------------------------------- #
# Отчёт о загрузке
# --------------------------------------------------------------------- #


@dataclass
class RecoveryReport:
    """Что произошло при чтении хранилища."""

    status: str = "ok"
    """``ok`` | ``created`` | ``empty`` | ``recovered`` | ``repaired`` | ``reset``."""

    backup_path: Path | None = None
    dropped: int = 0
    messages: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True, если файл пришлось создать или переписать."""
        return self.status in {"created", "recovered", "repaired", "reset"}

    @property
    def data_loss(self) -> bool:
        """True, если часть данных могла быть потеряна."""
        return self.status == "reset" or self.dropped > 0

    def summary(self) -> str:
        parts = {
            "created": "файл отсутствовал — создан пустой",
            "empty": "файл пуст — заметок нет",
            "recovered": "структура восстановлена из повреждённого JSON",
            "repaired": "удалены некорректные записи",
            "reset": "восстановление невозможно — создан новый файл",
            "ok": "файл в порядке",
        }
        text = parts.get(self.status, self.status)
        if self.dropped:
            text += f", отброшено записей: {self.dropped}"
        if self.backup_path:
            text += f", бэкап: {self.backup_path.name}"
        return text


# --------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------- #


def backup_path_for(path: Path) -> Path:
    """Путь к файлу резервной копии рядом с хранилищем."""
    return path.with_name(BACKUP_FILENAME)


def make_backup(path: Path) -> Path | None:
    """Копирует файл в ``notes_backup.json``. Не бросает исключений."""
    if not path.exists():
        return None
    target = backup_path_for(path)
    try:
        shutil.copy2(path, target)
        logger.warning("Создана резервная копия хранилища: %s", target)
        return target
    except OSError as exc:
        logger.error("Не удалось создать резервную копию %s: %s", path, exc)
        return None


def read_text_safely(path: Path) -> str:
    """Читает файл, не падая на BOM и невалидном UTF-8."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        logger.error("Не удалось прочитать хранилище %s: %s", path, exc)
        raise StorageError(
            f"Не удалось прочитать файл хранилища: {exc}",
            details=str(path),
        ) from exc

    # utf-8-sig снимает BOM; errors="replace" не даёт упасть на битых байтах.
    return raw.decode("utf-8-sig", errors="replace")


def extract_objects(text: str) -> list[dict[str, Any]]:
    """Достаёт уцелевшие объекты ``{...}`` из повреждённого текста.

    Сканирует строку с учётом строковых литералов и вложенности, поэтому
    корректно переживает запятые, кавычки и вложенные объекты.
    """
    objects: list[dict[str, Any]] = []
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                chunk = text[start : index + 1]
                start = None
                try:
                    candidate = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    objects.append(candidate)

    return objects


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    """Атомарно записывает список записей (через временный файл)."""
    import os
    import tempfile

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                json.dump(records, tmp_file, ensure_ascii=False, indent=2)
            os.replace(tmp_name, path)
        except OSError:
            Path(tmp_name).unlink(missing_ok=True)
            raise
    except OSError as exc:
        logger.error("Не удалось записать хранилище %s: %s", path, exc)
        raise StorageError(
            f"Не удалось сохранить хранилище: {exc}",
            details=str(path),
        ) from exc


def _only_dicts(records: list[Any]) -> tuple[list[dict[str, Any]], int]:
    """Оставляет только объекты, возвращая (записи, число отброшенных)."""
    valid = [item for item in records if isinstance(item, dict)]
    return valid, len(records) - len(valid)


# --------------------------------------------------------------------- #
# Основная функция
# --------------------------------------------------------------------- #


def load_records(path: Path) -> tuple[list[dict[str, Any]], RecoveryReport]:
    """Читает записи, при необходимости восстанавливая файл.

    Никогда не бросает исключение из-за содержимого файла: максимум —
    :class:`StorageError` при невозможности обратиться к диску.

    Returns:
        (записи, отчёт). Записи всегда список словарей.
    """
    report = RecoveryReport()

    if not path.exists():
        logger.info("Файл хранилища %s отсутствует — создаётся пустой", path)
        _write_records(path, [])
        report.status = "created"
        return [], report

    text = read_text_safely(path)
    if not text.strip():
        logger.warning("Файл хранилища %s пуст — считаем записей 0", path)
        report.status = "empty"
        return [], report

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("Повреждённый JSON в %s: %s", path, exc)
        report.messages.append(f"JSON повреждён: {exc}")

        salvaged = extract_objects(text)
        report.backup_path = make_backup(path)

        if salvaged:
            _write_records(path, salvaged)
            report.status = "recovered"
            logger.warning("Восстановлено записей из повреждённого файла: %d", len(salvaged))
            return salvaged, report

        _write_records(path, [])
        report.status = "reset"
        logger.error("Восстановить %s не удалось — создан пустой файл", path)
        return [], report

    # --- структура верхнего уровня -----------------------------------
    if isinstance(data, dict):
        # Один объект вместо списка: если это похоже на заметку — заворачиваем.
        if "title" in data:
            logger.warning("Хранилище %s содержит объект вместо списка — исправлено", path)
            report.backup_path = make_backup(path)
            _write_records(path, [data])
            report.status = "recovered"
            return [data], report

        logger.error("Хранилище %s содержит объект неизвестной структуры", path)
        report.backup_path = make_backup(path)
        _write_records(path, [])
        report.status = "reset"
        return [], report

    if not isinstance(data, list):
        logger.error("Хранилище %s: ожидался список, получено %s", path, type(data).__name__)
        report.backup_path = make_backup(path)
        _write_records(path, [])
        report.status = "reset"
        report.messages.append(f"Ожидался список, получено {type(data).__name__}")
        return [], report

    records, dropped = _only_dicts(data)
    if dropped:
        logger.warning("В хранилище %s отброшено некорректных записей: %d", path, dropped)
        report.backup_path = make_backup(path)
        _write_records(path, records)
        report.status = "repaired"
        report.dropped = dropped

    return records, report


__all__ = [
    "BACKUP_FILENAME",
    "RecoveryReport",
    "backup_path_for",
    "extract_objects",
    "load_records",
    "make_backup",
    "read_text_safely",
]
