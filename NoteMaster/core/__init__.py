"""Пакет ядра: бизнес-логика работы с заметками.

Импорты сделаны ленивыми (PEP 562), чтобы ``driver`` мог обращаться к
``core.errors`` без циклической зависимости: ``core.errors`` не тянет
``core.note_manager``, а ``note_manager`` уже зависит от драйвера.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AppError",
    "AttachmentError",
    "NoteError",
    "NoteManager",
    "NoteNotFoundError",
    "NoteValidationError",
    "StorageError",
]

_MANAGER_NAMES = {"NoteManager", "NoteNotFoundError", "NoteValidationError"}
_ERROR_NAMES = {"AppError", "AttachmentError", "NoteError", "StorageError"}


def __getattr__(name: str) -> Any:
    if name in _MANAGER_NAMES:
        from core import note_manager

        return getattr(note_manager, name)
    if name in _ERROR_NAMES:
        from core import errors

        return getattr(errors, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
