"""Централизованная иерархия ошибок NoteMaster.

Все ошибки делятся на две категории:

* **ошибки пользователя** (:class:`UserError`) — неверный ввод, пустой
  заголовок, отсутствующий id. Пользователю показывается текст как есть,
  приложение продолжает работу;
* **системные ошибки** (:class:`SystemFailure`) — проблемы файловой системы,
  повреждённое хранилище. Пользователь видит понятное сообщение, технические
  подробности уходят в лог.

Каждая ошибка несёт машиночитаемый ``code`` и необязательную ``hint`` —
подсказку «что делать». Единый формат позволяет CLI и web выводить
ошибки одинаково.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Базовая ошибка приложения с кодом, категорией и подсказкой."""

    code: str = "app_error"
    category: str = "system"
    default_message: str = "Произошла ошибка"
    default_hint: str | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        hint: str | None = None,
        details: str | None = None,
        code: str | None = None,
    ) -> None:
        self.message = (message or self.default_message).strip() or self.default_message
        self.hint = hint if hint is not None else self.default_hint
        self.details = details
        if code:
            self.code = code
        super().__init__(self.message)

    # ------------------------------------------------------------------ #

    @property
    def is_user_error(self) -> bool:
        """True, если ошибка вызвана действиями пользователя."""
        return self.category == "user"

    @property
    def user_message(self) -> str:
        """Текст, который можно показывать пользователю без обработки."""
        return self.message

    def to_dict(self) -> dict[str, Any]:
        """Словарь для API/логов/шаблонов."""
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "hint": self.hint,
        }

    def __str__(self) -> str:
        return self.message


# --------------------------------------------------------------------- #
# Ошибки пользователя
# --------------------------------------------------------------------- #


class UserError(AppError):
    """Ошибка, вызванная действиями пользователя."""

    category = "user"
    default_message = "Некорректные данные"


class ValidationError(UserError):
    """Значение не прошло проверку."""

    code = "validation_error"
    default_message = "Данные не прошли проверку"


class NoteValidationError(ValidationError):
    """Нарушено бизнес-правило заметки."""

    code = "note_validation"


class NotFoundError(UserError):
    """Запрошенный объект отсутствует."""

    code = "not_found"
    default_message = "Объект не найден"


class NoteNotFoundError(NotFoundError):
    """Заметка с указанным id отсутствует в хранилище."""

    code = "note_not_found"
    default_message = "Заметка не найдена"
    default_hint = "Проверьте id: список заметок доступен по команде «list»."


class InvalidInputError(UserError):
    """Некорректный ввод (пустая команда, неверный формат аргумента)."""

    code = "invalid_input"


class AttachmentError(UserError):
    """Проблема с вложением, о которой должен знать пользователь."""

    code = "attachment_error"
    default_message = "Ошибка вложения"


# --------------------------------------------------------------------- #
# Системные ошибки
# --------------------------------------------------------------------- #


class SystemFailure(AppError):
    """Системная ошибка: файловая система, хранилище, окружение."""

    category = "system"
    default_message = "Внутренняя ошибка"


class StorageError(SystemFailure):
    """Ошибка чтения или записи хранилища."""

    code = "storage_error"
    default_message = "Ошибка хранилища"
    default_hint = "Проверьте права доступа к файлу data/notes.json."


class RecoveryError(StorageError):
    """Не удалось восстановить повреждённое хранилище."""

    code = "recovery_error"
    default_message = "Не удалось восстановить хранилище"


class FileAccessError(SystemFailure):
    """Ошибка доступа к файлу на диске."""

    code = "file_access_error"
    default_message = "Нет доступа к файлу"


# --------------------------------------------------------------------- #
# Единый формат для интерфейсов
# --------------------------------------------------------------------- #


def describe_error(exc: BaseException) -> dict[str, Any]:
    """Приводит любое исключение к единому формату для CLI и web.

    Returns:
        Словарь с ключами ``code``, ``category``, ``message``, ``hint``.
        Для неизвестных исключений текст скрывается за общим сообщением,
        чтобы не показывать пользователю технические детали.
    """
    if isinstance(exc, AppError):
        return exc.to_dict()

    return {
        "code": "unexpected_error",
        "category": "system",
        "message": "Непредвиденная ошибка",
        "hint": "Повторите действие. Если ошибка повторяется — проверьте логи приложения.",
    }


def error_text(exc: BaseException, *, prefix: str = "Ошибка") -> str:
    """Готовая строка «Ошибка: …» с подсказкой на следующей строке."""
    info = describe_error(exc)
    text = f"{prefix}: {info['message']}"
    if info.get("hint"):
        text += f"\nПодсказка: {info['hint']}"
    return text


# --------------------------------------------------------------------- #
# Совместимость со старым API
# --------------------------------------------------------------------- #

#: Раньше все ошибки ядра наследовались от ``NoteError``.
#: Теперь это общий корень, чтобы ``except NoteError`` продолжал работать.
NoteError = AppError

__all__ = [
    "AppError",
    "AttachmentError",
    "FileAccessError",
    "InvalidInputError",
    "NoteError",
    "NoteNotFoundError",
    "NoteValidationError",
    "NotFoundError",
    "RecoveryError",
    "StorageError",
    "SystemFailure",
    "UserError",
    "ValidationError",
    "describe_error",
    "error_text",
]
