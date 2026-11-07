"""Резолвинг вложений заметки.

Вложение в JSON — просто строка. Она может означать:

* **stored** — файл, загруженный через веб-интерфейс: лежит в
  ``web/static/uploads`` под именем ``<uuid заметки>_<оригинальное имя>``;
* **legacy** — файл из старого хранилища ``data/uploads/<note_id>/<имя>``;
* **local** — абсолютный путь к файлу на диске (CLI, API, ручное добавление);
* **missing** — файла физически нет;
* **invalid** — значение не является корректным путём.

Модуль не знает про Flask: он только отвечает, где файл и доступен ли он.
Интерфейсы сами решают, как показать недоступное вложение.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.errors import AttachmentError

#: Корень проекта — используется интерфейсами для поиска вложений.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
#: Каталог файлов, загруженных через веб-интерфейс.
UPLOAD_ROOT = PROJECT_ROOT / "web" / "static" / "uploads"
#: Старый каталог вложений (совместимость).
LEGACY_ROOT = PROJECT_ROOT / "data" / "uploads"

#: Разделитель пути Windows задаём через chr(92), чтобы не путать экранирование.
_BACKSLASH = chr(92)
_UNSAFE_CHARS = set('/:*?"<>|' + _BACKSLASH) | {chr(code) for code in range(32)}

#: UUID в начале имени файла (36 символов) и старый 8-символьный префикс.
_UUID_PREFIX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}[_-]?"
)
_SHORT_PREFIX = re.compile(r"^[0-9a-fA-F]{8}-")

_EXT_ICONS = {
    ".pdf": "📕", ".txt": "📝", ".md": "📝", ".rtf": "📝",
    ".doc": "📄", ".docx": "📄", ".odt": "📄",
    ".xls": "📊", ".xlsx": "📊", ".csv": "📊",
    ".ppt": "📽", ".pptx": "📽",
    ".png": "🖼", ".jpg": "🖼", ".jpeg": "🖼", ".gif": "🖼",
    ".webp": "🖼", ".svg": "🖼", ".bmp": "🖼", ".ico": "🖼",
    ".mp3": "🎵", ".wav": "🎵", ".ogg": "🎵", ".flac": "🎵",
    ".mp4": "🎬", ".mov": "🎬", ".avi": "🎬", ".mkv": "🎬", ".webm": "🎬",
    ".zip": "🗜", ".rar": "🗜", ".7z": "🗜", ".tar": "🗜", ".gz": "🗜",
    ".json": "🧾", ".html": "🌐", ".htm": "🌐", ".py": "🧾",
}


class AttachmentState(str, Enum):
    """Где находится файл вложения."""

    STORED = "stored"
    LEGACY = "legacy"
    LOCAL = "local"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(frozen=True)
class AttachmentRef:
    """Результат разбора строки вложения."""

    raw: str
    state: AttachmentState
    path: Path | None = None
    name: str = ""
    size: int | None = None

    @property
    def available(self) -> bool:
        """True, если файл существует и его можно открыть."""
        return self.state in {AttachmentState.STORED, AttachmentState.LEGACY, AttachmentState.LOCAL}

    @property
    def error_message(self) -> str | None:
        """Понятный текст проблемы или None, если всё в порядке."""
        if self.available:
            return None
        if self.state is AttachmentState.INVALID:
            return "Некорректный путь к файлу"
        return "Файл не найден на диске"

    @property
    def icon(self) -> str:
        return icon_for(self.name or self.raw)

    @property
    def human_size(self) -> str:
        return human_size(self.size)


# --------------------------------------------------------------------- #
# Имена файлов
# --------------------------------------------------------------------- #


def sanitize_filename(filename: str) -> str:
    """Убирает путь и опасные символы, сохраняя юникод."""
    base = str(filename).replace(_BACKSLASH, "/").rstrip("/").rsplit("/", 1)[-1]
    cleaned = "".join("_" if ch in _UNSAFE_CHARS else ch for ch in base)
    cleaned = cleaned.strip().strip(".")
    return (cleaned or "file")[:120]


def build_stored_name(note_id: str, original: str) -> str:
    """Имя файла в хранилище: ``<uuid заметки>_<оригинальное имя>``."""
    return f"{note_id}_{sanitize_filename(original)}"


def display_name(raw: str, note_id: str | None = None) -> str:
    """Человекочитаемое имя файла без служебных префиксов."""
    base = str(raw).replace(_BACKSLASH, "/").rstrip("/").rsplit("/", 1)[-1]
    if note_id and base.startswith(f"{note_id}_"):
        return base[len(note_id) + 1 :] or base
    without_uuid = _UUID_PREFIX.sub("", base)
    if without_uuid != base:
        return without_uuid or base
    return _SHORT_PREFIX.sub("", base) or base


def icon_for(name: str) -> str:
    """Иконка по расширению файла."""
    return _EXT_ICONS.get(Path(str(name)).suffix.lower(), "📎")


def human_size(size: int | None) -> str:
    """Размер в человекочитаемом виде."""
    if size is None:
        return "—"
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ТБ"


# --------------------------------------------------------------------- #
# Резолвинг
# --------------------------------------------------------------------- #


def _is_inside(path: Path, root: Path) -> bool:
    """Проверяет, что путь не выходит за пределы каталога."""
    try:
        resolved_root = root.resolve()
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == resolved_root or resolved_root in resolved.parents


def resolve(
    raw: object,
    *,
    upload_root: Path | str | None = None,
    legacy_root: Path | str | None = None,
    note_id: str | None = None,
) -> AttachmentRef:
    """Определяет состояние вложения и путь к файлу.

    Args:
        raw: строка из поля ``attachments``.
        upload_root: каталог загруженных файлов (``web/static/uploads``).
        legacy_root: старый каталог вложений (``data/uploads``).
        note_id: id заметки — для отображения имени без префикса.

    Returns:
        :class:`AttachmentRef` — никогда не бросает исключений.
    """
    if not isinstance(raw, str) or not raw.strip():
        return AttachmentRef(raw=str(raw), state=AttachmentState.INVALID, name="")

    text = raw.strip()
    name = display_name(text, note_id)

    # --- абсолютный путь (CLI / API / ручное добавление) --------------
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        if candidate.is_file():
            return AttachmentRef(
                raw=text,
                state=AttachmentState.LOCAL,
                path=candidate,
                name=candidate.name,
                size=_safe_size(candidate),
            )
        return AttachmentRef(raw=text, state=AttachmentState.MISSING, name=candidate.name)

    # --- относительный путь: защита от выхода за каталог --------------
    parts = Path(text.replace(_BACKSLASH, "/")).parts
    if ".." in parts:
        return AttachmentRef(raw=text, state=AttachmentState.INVALID, name=name)

    for root, state in (
        (upload_root, AttachmentState.STORED),
        (legacy_root, AttachmentState.LEGACY),
    ):
        if root is None:
            continue
        root_path = Path(root)
        target = root_path / text
        if target.is_file() and _is_inside(target, root_path):
            return AttachmentRef(
                raw=text,
                state=state,
                path=target,
                name=name,
                size=_safe_size(target),
            )

    return AttachmentRef(raw=text, state=AttachmentState.MISSING, name=name)


def _safe_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def normalize_attachment(raw: object, *, base_dir: Path | str | None = None) -> str:
    """Превращает аргумент интерфейса в значение для поля ``attachments``.

    Если файл существует, сохраняется абсолютный путь (Вариант 1 из ТЗ):
    так вложение остаётся доступным независимо от текущего каталога.

    Raises:
        AttachmentError: путь пустой, некорректный или файла нет на диске.
    """
    text = str(raw).strip() if raw is not None else ""
    if not text:
        raise AttachmentError(
            "Пустой путь к вложению",
            hint="Укажите путь к существующему файлу.",
        )

    path = Path(text).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path

    try:
        is_file = path.is_file()
    except OSError as exc:
        raise AttachmentError(
            f"Не удалось проверить файл «{text}»",
            hint="Проверьте путь и права доступа к файлу.",
            details=str(exc),
        ) from exc

    if not is_file:
        raise AttachmentError(
            f"Файл «{text}» не найден",
            hint="Проверьте путь: файл должен существовать на диске.",
        )

    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def normalize_attachments(
    values: list[object] | None,
    *,
    base_dir: Path | str | None = None,
) -> list[str]:
    """Нормализует список путей, сообщая обо всех проблемах сразу."""
    if not values:
        return []

    normalized: list[str] = []
    problems: list[str] = []
    for value in values:
        try:
            normalized.append(normalize_attachment(value, base_dir=base_dir))
        except AttachmentError as exc:
            problems.append(exc.message)

    if problems:
        raise AttachmentError(
            "Не удалось добавить вложения: " + "; ".join(problems),
            hint="Исправьте пути к файлам и повторите попытку.",
        )
    return normalized


__all__ = [
    "AttachmentRef",
    "AttachmentState",
    "LEGACY_ROOT",
    "PROJECT_ROOT",
    "UPLOAD_ROOT",
    "build_stored_name",
    "display_name",
    "human_size",
    "icon_for",
    "normalize_attachment",
    "normalize_attachments",
    "resolve",
    "sanitize_filename",
]
