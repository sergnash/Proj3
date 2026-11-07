"""Модель данных заметки и валидация полей.

Модель отвечает только за собственную целостность и бросает ``ValueError``
с понятным текстом. Превращением в пользовательские ошибки занимается ядро.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

#: Разумные ограничения, чтобы одна заметка не раздувала хранилище.
MAX_TITLE_LENGTH = 300
MAX_CONTENT_LENGTH = 100_000


def new_id() -> str:
    """Генерирует уникальный идентификатор заметки (UUID4)."""
    return str(uuid4())


def new_timestamp() -> str:
    """Текущие дата и время в ISO 8601 (с таймзоной и микросекундами)."""
    return datetime.now().astimezone().isoformat()


# --------------------------------------------------------------------- #
# Валидаторы
# --------------------------------------------------------------------- #


def validate_title(title: Any) -> None:
    """title обязателен, должен быть непустой строкой разумной длины."""
    if not isinstance(title, str):
        raise ValueError("Поле «title» должно быть строкой")
    if not title.strip():
        raise ValueError("Поле «title» не может быть пустым")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(
            f"Поле «title» слишком длинное: {len(title)} символов "
            f"(максимум {MAX_TITLE_LENGTH})"
        )


def validate_content(content: Any) -> None:
    """content — строка (может быть пустой)."""
    if not isinstance(content, str):
        raise ValueError("Поле «content» должно быть строкой")
    if len(content) > MAX_CONTENT_LENGTH:
        raise ValueError(
            f"Поле «content» слишком длинное: {len(content)} символов "
            f"(максимум {MAX_CONTENT_LENGTH})"
        )


def validate_attachments(attachments: Any) -> None:
    """attachments — список непустых строк (пути или имена файлов)."""
    if not isinstance(attachments, list):
        raise ValueError("Поле «attachments» должно быть списком строк")
    for index, item in enumerate(attachments, start=1):
        if not isinstance(item, str):
            raise ValueError(
                f"Элемент вложений #{index} должен быть строкой, "
                f"получено {type(item).__name__}"
            )
        if not item.strip():
            raise ValueError(f"Элемент вложений #{index} не может быть пустым")


def validate_id(note_id: Any) -> None:
    """id — непустая строка (UUID или автоинкремент)."""
    if not isinstance(note_id, str) or not note_id.strip():
        raise ValueError("Поле «id» должно быть непустой строкой")


def validate_timestamp(timestamp: Any) -> None:
    """timestamp — непустая строка в ISO 8601."""
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise ValueError("Поле «timestamp» должно быть непустой строкой")
    try:
        datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError(
            f"Поле «timestamp» должно быть датой ISO 8601, получено {timestamp!r}"
        ) from exc


# --------------------------------------------------------------------- #
# Модель
# --------------------------------------------------------------------- #


@dataclass
class Note:
    """Одна заметка.

    Атрибуты:
        id: уникальный идентификатор (UUID4), присваивается автоматически.
        title: обязательный заголовок, не может быть пустым.
        content: текстовое содержимое (по умолчанию пустое).
        timestamp: дата/время создания или последнего изменения в ISO 8601.
        attachments: список путей/имён файлов-вложений.
    """

    title: str
    content: str = ""
    id: str = field(default_factory=new_id)
    timestamp: str = field(default_factory=new_timestamp)
    attachments: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate_title(self.title)
        validate_content(self.content)
        validate_id(self.id)
        validate_timestamp(self.timestamp)
        validate_attachments(self.attachments)

    def to_dict(self) -> dict[str, Any]:
        """Представление заметки в виде словаря для записи в хранилище."""
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "timestamp": self.timestamp,
            "attachments": list(self.attachments),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Note":
        """Восстанавливает заметку из словаря.

        Устойчив к «почти корректным» данным:

        * неизвестные ключи игнорируются;
        * отсутствующие или некорректные ``id``/``timestamp`` генерируются заново;
        * отсутствующие ``content``/``attachments`` получают значения по умолчанию;
        * некорректные элементы ``attachments`` отбрасываются.

        Некорректные обязательные поля (``title``) дают ``ValueError``
        с указанием конкретной проблемы.
        """
        if not isinstance(data, dict):
            raise ValueError(
                f"Заметка должна быть объектом JSON, получено {type(data).__name__}"
            )

        if "title" not in data:
            raise ValueError("Отсутствует обязательное поле «title»")

        title = data.get("title")

        content = data.get("content", "")
        if content is None:
            content = ""

        note_id = data.get("id")
        if not isinstance(note_id, str) or not note_id.strip():
            note_id = new_id()

        timestamp = data.get("timestamp")
        try:
            validate_timestamp(timestamp)
        except ValueError:
            timestamp = new_timestamp()

        attachments = data.get("attachments", [])
        if not isinstance(attachments, (list, tuple)):
            attachments = []
        else:
            # Некорректные элементы отбрасываются, сама заметка сохраняется.
            attachments = [
                item for item in attachments if isinstance(item, str) and item.strip()
            ]

        return cls(
            title=title,
            content=content,
            id=note_id,
            timestamp=timestamp,
            attachments=attachments,
        )
