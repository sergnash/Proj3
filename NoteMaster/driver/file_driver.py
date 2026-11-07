"""Файловый слой (driver): абстракция доступа к постоянному хранилищу.

Хранение — файл JSON со списком объектов. Слой не знает ничего о заметках:
он оперирует «сырыми» словарями, а превращением их в модель Note занимается ядро.

Чтение делегируется :mod:`driver.recovery`, который умеет переживать
отсутствие файла, BOM, битые байты и повреждённый JSON.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from core.errors import StorageError
from driver.recovery import RecoveryReport, load_records, make_backup

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_PATH = Path(__file__).resolve().parents[1] / "data" / "notes.json"

__all__ = [
    "DEFAULT_STORAGE_PATH",
    "BaseDriver",
    "FileDriver",
    "StorageError",
]


class BaseDriver(ABC):
    """Интерфейс драйвера хранилища.

    Абстракция нужна, чтобы ядро не зависело от способа хранения:
    файловый JSON сегодня может быть заменён на БД или REST-хранилище завтра.
    """

    @abstractmethod
    def load(self) -> list[dict[str, Any]]:
        """Возвращает все записи хранилища."""

    @abstractmethod
    def save(self, items: list[dict[str, Any]]) -> None:
        """Полностью перезаписывает хранилище списком записей."""

    def backup(self) -> Path | None:
        """Создаёт резервную копию хранилища (если поддерживается)."""
        return None


class FileDriver(BaseDriver):
    """Драйвер, сохраняющий записи списком JSON-объектов в файле."""

    def __init__(self, path: str | Path = DEFAULT_STORAGE_PATH) -> None:
        self._path = Path(path)
        #: Отчёт последней загрузки: что пришлось восстанавливать.
        self.last_report: RecoveryReport | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[dict[str, Any]]:
        """Читает список записей, восстанавливая файл при повреждении.

        Отсутствие файла не считается ошибкой: создаётся пустое хранилище.
        Битый JSON, неверная структура и BOM обрабатываются в
        :func:`driver.recovery.load_records` — с резервной копией.
        """
        records, report = load_records(self._path)
        self.last_report = report
        if report.changed or report.dropped:
            logger.warning("Хранилище %s: %s", self._path, report.summary())
        return records

    def save(self, items: list[dict[str, Any]]) -> None:
        """Сохраняет весь список записей в файл."""
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise StorageError("Драйвер принимает только список объектов")
        self._write_json(items)
        logger.debug("В хранилище %s записано %d записей", self._path, len(items))

    def backup(self) -> Path | None:
        """Копирует текущий файл в ``notes_backup.json``."""
        return make_backup(self._path)

    # ------------------------------------------------------------------ #

    def _write_json(self, items: list[dict[str, Any]]) -> None:
        """Атомарная запись: во временный файл рядом, затем замена целевого."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                    json.dump(items, tmp_file, ensure_ascii=False, indent=2)
                os.replace(tmp_name, self._path)
            except OSError:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        except OSError as exc:
            logger.error("Не удалось записать файл %s: %s", self._path, exc)
            raise StorageError(
                f"Ошибка записи хранилища: {exc}",
                details=str(self._path),
            ) from exc
