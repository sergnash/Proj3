"""Интерактивный режим NoteMaster — REPL через терминал.

Вызывается из CLI:  python -m app.main ui
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from colorama import Fore, Style

from app.cli import (
    _BOLD,
    _RESET,
    _color,
    _err,
    _format_note,
    _ok,
    _warn,
    display_notes,
    log_error,
    print_error,
)
from core.attachments import normalize_attachments
from core.errors import AppError
from core.note_manager import NoteManager
from models import Note

logger = logging.getLogger("app.ui")

PROMPT = _color("nm> ", Fore.GREEN + _BOLD)
HELP_TEXT = f"""
{_BOLD}Доступные команды:{_RESET}

  add       — создать заметку
  list      — показать все заметки
  find <text> — поиск по тексту
  edit <id> — редактировать заметку
  delete <id> — удалить заметку
  help      — показать эту справку
  exit      — выйти

{_BOLD}Подсказка:{_RESET}  введите ? или пустую строку для повтора поля.
"""


# ------------------------------------------------------------------ #
# Ввод с повтором при пустом значении
# ------------------------------------------------------------------ #


def _prompt(field: str, default: str = "", *, allow_empty: bool = False) -> str | None:
    """Запрашивает ввод у пользователя.

    Если введён ``?`` — выводит подсказку и запрашивает снова.
    Если введена пустая строка и есть текущее значение — возвращает None
    (значение не менять). При ``allow_empty=True`` пустая строка допустима
    и возвращается как ``""``.
    """
    shown = default or ("нет" if allow_empty else "")
    while True:
        raw = input(f"  {field} {Fore.CYAN}[{shown}]{Style.RESET_ALL}: ").strip()
        if raw == "?":
            print(f"  {_warn(' Пропускаем (оставляем текущее значение).')}")
            return None
        if not raw:
            if allow_empty:
                return ""
            if default:
                print(f"  {_warn(' Пропускаем (оставляем текущее значение).')}")
                return None
            print(f"  {_err(' Поле не может быть пустым. Попробуйте снова.')}")
            continue
        return raw


# ------------------------------------------------------------------ #
# Команды UI-режима
# ------------------------------------------------------------------ #


def _ui_add(manager: NoteManager) -> None:
    print("\n" + _BOLD + "--- Создание заметки ---" + _RESET)
    title = _prompt("title (обязательно)")
    if title is None:
        print(_warn(" Заголовок обязателен — отмена."))
        return
    content = _prompt("content")
    raw_attachments = _prompt("attachments (через запятую)", allow_empty=True)
    raw_paths = [a.strip() for a in raw_attachments.split(",") if a.strip()] if raw_attachments else []

    try:
        attachments = normalize_attachments(raw_paths, base_dir=Path.cwd())
        note = manager.create_note(title, content or "", attachments or None)
    except AppError as exc:
        log_error("ui add", exc)
        print_error(exc)
        return

    print(_ok(" Заметка создана!"))
    print(_format_note(note))


def _ui_list(manager: NoteManager) -> None:
    display_notes(manager.get_all_notes())


def _ui_find(manager: NoteManager, parts: list[str]) -> None:
    query = " ".join(parts) if parts else ""
    if not query:
        print(_warn(" Введите текст для поиска: find <текст>"))
        return
    results = manager.find_notes(query)
    if results:
        print(_BOLD + f"\nНайдено {len(results)} по запросу «{query}»:{_RESET}")
        display_notes(results)
    else:
        print(_warn(f" Ничего не найдено по запросу «{query}»."))


def _ui_delete(manager: NoteManager, note_id: str) -> None:
    if not note_id:
        print(_warn(" Укажите id заметки: delete <id>"))
        return
    try:
        note = manager.get_note_by_id(note_id)
        print(_BOLD + "\nУдаляемая заметка:" + _RESET)
        print(_format_note(note))
        confirm = input(f"  {_color('Удалить? [y/N]: ', Fore.YELLOW)}").strip().lower()
        if confirm in ("y", "yes"):
            manager.delete_note(note_id)
            print(_ok(f" Заметка удалена: {note.title}"))
        else:
            print(" Отменено.")
    except AppError as exc:
        log_error("ui delete", exc)
        print_error(exc)


def _ui_edit(manager: NoteManager, note_id: str) -> None:
    if not note_id:
        print(_warn(" Укажите id заметки: edit <id>"))
        return
    try:
        note = manager.get_note_by_id(note_id)
    except AppError as exc:
        log_error("ui edit", exc)
        print_error(exc)
        return

    print(_BOLD + "\nТекущие значения:" + _RESET)
    print(_format_note(note))

    fields: dict[str, Any] = {}

    # title
    new_title = _prompt("title", note.title or "(пусто)")
    if new_title is not None:
        fields["title"] = new_title

    # content
    new_content = _prompt("content", note.content or "(пусто)")
    if new_content is not None:
        fields["content"] = new_content

    # attachments: Enter — не менять, «-» — очистить список
    default_attachments = ", ".join(note.attachments) if note.attachments else ""
    raw = _prompt(
        "attachments (через запятую, - очистить)",
        default_attachments,
        allow_empty=not default_attachments,
    )
    if raw is not None:
        raw_paths = [] if raw == "-" else [a.strip() for a in raw.split(",") if a.strip()]
        try:
            fields["attachments"] = normalize_attachments(raw_paths, base_dir=Path.cwd())
        except AppError as exc:
            log_error("ui edit", exc)
            print_error(exc)
            return

    if not fields:
        print(_warn(" Не изменено ни одного поля."))
        return

    try:
        updated = manager.update_note(note_id, **fields)
    except AppError as exc:
        log_error("ui edit", exc)
        print_error(exc)
        return

    print(_ok(" Заметка обновлена!"))
    print(_format_note(updated))


# ------------------------------------------------------------------ #
# Парсер строки команды
# ------------------------------------------------------------------ #

_COMMANDS = {"add", "list", "find", "edit", "delete", "help", "exit", "quit"}


def _parse(line: str) -> tuple[str, list[str]]:
    """Разбирает строку ввода на (команда, аргументы)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ("", [])
    parts = stripped.split(None, 1)
    cmd = parts[0].lower()
    args = parts[1].split() if len(parts) > 1 else []
    return cmd, args


# ------------------------------------------------------------------ #
# Главный цикл
# ------------------------------------------------------------------ #


def run(manager: NoteManager) -> int:
    """Запускает интерактивный REPL."""
    print("\n" + _BOLD + _color("=== NoteMaster — интерактивный режим ===", Fore.CYAN + _BOLD) + _RESET)
    print(_color("Введите help для списка команд. Вводите ? для подсказки.\n", Fore.LIGHTBLACK_EX))

    while True:
        try:
            raw = input(PROMPT)
        except (EOFError, KeyboardInterrupt):
            print()
            break

        cmd, args = _parse(raw)

        try:
            if cmd == "" or cmd in ("?", "help"):
                print(HELP_TEXT)
                continue
            if cmd in ("exit", "quit"):
                print(_ok(" До свидания!"))
                return 0
            if cmd == "add":
                _ui_add(manager)
            elif cmd == "list":
                _ui_list(manager)
            elif cmd == "find":
                _ui_find(manager, args)
            elif cmd == "edit":
                _ui_edit(manager, args[0] if args else "")
            elif cmd == "delete":
                _ui_delete(manager, args[0] if args else "")
            else:
                print(_warn(f" Неизвестная команда «{cmd}». Введите help."))
        except AppError as exc:
            log_error(f"ui {cmd or 'command'}", exc)
            print_error(exc)
        except KeyboardInterrupt:
            print("\n Операция прервана.")
        except Exception as exc:  # noqa: BLE001 — REPL не должен падать
            log_error(f"ui {cmd or 'command'}", exc)
            print_error(exc)

    return 0
