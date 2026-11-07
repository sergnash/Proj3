"""Ядро системы управления заметками.

NoteManager инкапсулирует всю работу с заметками: создание, чтение,
частичное обновление, удаление, валидацию бизнес-правил и сохранение
через драйвер хранилища.

Ошибки делятся на пользовательские (:class:`NoteValidationError`,
:class:`NoteNotFoundError`) и системные (:class:`StorageError`) — см.
:mod:`core.errors`.
"""

from __future__ import annotations

import logging
from typing import Any

from core.errors import (
    NoteError,
    NoteNotFoundError,
    NoteValidationError,
    StorageError,
)
from driver.file_driver import BaseDriver, FileDriver
from models import (
    Note,
    new_id,
    new_timestamp,
    validate_attachments,
    validate_content,
    validate_title,
)

logger = logging.getLogger(__name__)

#: Поля, которые разрешено менять через update_note.
EDITABLE_FIELDS = ("title", "content", "attachments")

#: Поля, защищённые от изменения.
PROTECTED_FIELDS = ("id", "timestamp")

__all__ = [
    "EDITABLE_FIELDS",
    "NoteError",
    "NoteManager",
    "NoteNotFoundError",
    "NoteValidationError",
    "StorageError",
]


class NoteManager:
    """Управляет множеством заметок: инкапсулирует бизнес-логику и хранилище.

    Бизнес-правила:
        * нельзя обновлять или удалять заметки, если хранилище пусто;
        * обновление возможно только после получения заметки по id;
        * обновление частичное — меняются только переданные поля;
        * title не может быть пустым;
        * attachments — список непустых строк.
    """

    def __init__(self, driver: BaseDriver | None = None) -> None:
        """Создаёт менеджер.

        Args:
            driver: драйвер хранилища. По умолчанию — FileDriver (data/notes.json).
        """
        self._driver: BaseDriver = driver if driver is not None else FileDriver()
        #: Проблемы, обнаруженные при загрузке (для предупреждения пользователя).
        self.problems: list[str] = []
        self._notes: list[Note] = self._load_notes()
        logger.info("Загружено заметок: %d", len(self._notes))

    # ------------------------------------------------------------------ #
    # Публичный API
    # ------------------------------------------------------------------ #

    def create_note(
        self,
        title: str,
        content: str = "",
        attachments: list[str] | None = None,
    ) -> Note:
        """Создаёт заметку, сохраняет её в хранилище и возвращает её.

        Raises:
            NoteValidationError: пустой title или некорректные поля.
        """
        try:
            note = Note(
                title=title,
                content=content,
                id=new_id(),
                timestamp=new_timestamp(),
                attachments=list(attachments) if attachments is not None else [],
            )
        except ValueError as exc:
            logger.warning("Отклонено создание заметки: %s", exc)
            raise NoteValidationError(str(exc)) from exc

        self._notes.append(note)
        try:
            self._save()
        except StorageError:
            # Откатываем изменение, чтобы состояние в памяти не расходилось с диском.
            self._notes.remove(note)
            raise
        logger.info("Создана заметка id=%s title=%r", note.id, note.title)
        return self._copy(note)

    def get_all_notes(self) -> list[Note]:
        """Возвращает все заметки (копии, чтобы защитить внутреннее состояние)."""
        return [self._copy(note) for note in self._notes]

    def get_note_by_id(self, note_id: str) -> Note:
        """Возвращает копию заметки по id.

        Raises:
            NoteNotFoundError: заметки с таким id нет.
        """
        return self._copy(self._find(note_id))

    def update_note(self, note_id: str, **fields: Any) -> Note:
        """Частично обновляет заметку: меняются только переданные поля.

        Допустимые поля: title, content, attachments.
        Все значения проверяются до применения — при ошибке заметка не меняется.

        Raises:
            NoteNotFoundError: заметки с таким id нет (в т.ч. хранилище пусто).
            NoteValidationError: некорректные значения или неизвестное поле.
        """
        note = self._find(note_id)
        if not fields:
            raise NoteValidationError(
                "Не передано ни одного поля для обновления",
                hint=f"Доступные поля: {', '.join(EDITABLE_FIELDS)}.",
            )

        updates = self._validate_updates(fields, note_id)

        previous = note.to_dict()
        for name, value in updates.items():
            setattr(note, name, value)
        note.timestamp = new_timestamp()

        try:
            self._save()
        except StorageError:
            # Возвращаем прежние значения, чтобы не потерять данные в памяти.
            for name, value in previous.items():
                setattr(note, name, value)
            raise

        logger.info("Обновлена заметка id=%s поля=%s", note.id, sorted(updates))
        return self._copy(note)

    def delete_note(self, note_id: str) -> Note:
        """Удаляет заметку по id, сохраняет хранилище и возвращает удалённую копию.

        Raises:
            NoteNotFoundError: заметки с таким id нет (в т.ч. хранилище пусто).
        """
        note = self._find(note_id)
        index = self._notes.index(note)
        self._notes.remove(note)
        try:
            self._save()
        except StorageError:
            self._notes.insert(index, note)
            raise
        logger.info("Удалена заметка id=%s", note_id)
        return self._copy(note)

    def find_notes(self, query: str) -> list[Note]:
        """Ищет подстроку query в title и content (регистронезависимо)."""
        needle = (query or "").strip().lower()
        if not needle:
            return self.get_all_notes()
        return [
            self._copy(note)
            for note in self._notes
            if needle in note.title.lower() or needle in note.content.lower()
        ]

    def count(self) -> int:
        """Количество заметок в памяти."""
        return len(self._notes)

    # ------------------------------------------------------------------ #
    # Внутренние помощники
    # ------------------------------------------------------------------ #

    def _load_notes(self) -> list[Note]:
        """Восстанавливает заметки из «сырых» данных драйвера.

        Повреждённый JSON драйвер уже починил. Здесь проверяются отдельные
        записи: некорректная запись не роняет приложение — она пропускается,
        а исходный файл сохраняется в ``notes_backup.json``.
        """
        notes: list[Note] = []
        skipped = 0

        for index, raw in enumerate(self._driver.load(), start=1):
            try:
                notes.append(Note.from_dict(raw))
            except ValueError as exc:
                skipped += 1
                message = f"Запись #{index} пропущена: {exc}"
                logger.error("Некорректная запись заметки #%d: %s", index, exc)
                self.problems.append(message)

        if skipped:
            backup = self._driver.backup()
            if backup is not None:
                self.problems.append(f"Резервная копия: {backup}")
            logger.warning("Пропущено некорректных записей: %d", skipped)

        return notes

    def _validate_updates(self, fields: dict[str, Any], note_id: str) -> dict[str, Any]:
        """Проверяет переданные поля и возвращает нормализованные значения."""
        updates: dict[str, Any] = {}
        for name, value in fields.items():
            if name in PROTECTED_FIELDS:
                raise NoteValidationError(
                    f"Поле «{name}» изменять нельзя",
                    hint="id и timestamp обновляются автоматически.",
                )
            if name not in EDITABLE_FIELDS:
                raise NoteValidationError(
                    f"Неизвестное поле обновления: «{name}»",
                    hint=f"Доступные поля: {', '.join(EDITABLE_FIELDS)}.",
                )

            try:
                if name == "title":
                    validate_title(value)
                elif name == "content":
                    validate_content(value)
                elif name == "attachments":
                    validate_attachments(value)
            except ValueError as exc:
                logger.warning("Отклонено обновление id=%s: %s", note_id, exc)
                raise NoteValidationError(str(exc)) from exc

            updates[name] = list(value) if name == "attachments" else value

        return updates

    def _find(self, note_id: str) -> Note:
        """Возвращает внутренний объект заметки по id или бросает ошибку."""
        if not isinstance(note_id, str) or not note_id.strip():
            raise NoteValidationError(
                "Не указан id заметки",
                hint="id можно посмотреть в списке заметок.",
            )

        if not self._notes:
            logger.warning("Запрошена заметка id=%s, но хранилище пусто", note_id)
            raise NoteNotFoundError(
                f"Заметка с id «{note_id}» не найдена: хранилище пусто",
                hint="Создайте заметку: «add» в CLI или кнопка «Добавить» в веб-интерфейсе.",
            )

        for note in self._notes:
            if note.id == note_id:
                return note

        logger.warning("Заметка id=%s не найдена", note_id)
        raise NoteNotFoundError(
            f"Заметка с id «{note_id}» не найдена",
            hint="Возможно, она была удалена. Обновите список заметок.",
        )

    def _save(self) -> None:
        """Сохраняет все заметки в хранилище."""
        self._driver.save([note.to_dict() for note in self._notes])

    @staticmethod
    def _copy(note: Note) -> Note:
        """Защищает внутреннее состояние: наружу отдаём копию заметки."""
        return Note.from_dict(note.to_dict())
