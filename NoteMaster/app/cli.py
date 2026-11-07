"""Консольный интерфейс NoteMaster — CLI с argparse и цветным выводом.

Точка входа:
    python -m app.main           # интерактивный режим
    python -m app.main add --title ...
    python -m app.main list
    python -m app.main delete --id ...
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from colorama import Fore, Style, init

from core.attachments import (
    LEGACY_ROOT,
    UPLOAD_ROOT,
    normalize_attachments,
    resolve,
)
from core.errors import AppError, describe_error
from core.note_manager import NoteManager
from models import Note

init()  # включить ANSI-раскраску на Windows

logger = logging.getLogger("app")

# ------------------------------------------------------------------ #
# Цвета и форматирование
# ------------------------------------------------------------------ #

_OK = Fore.GREEN + "✓" + Style.RESET_ALL
_ERR = Fore.RED + "✗" + Style.RESET_ALL
_WARN = Fore.YELLOW + "!" + Style.RESET_ALL
_BOLD = Style.BRIGHT
_RESET = Style.RESET_ALL


def _color(text: str, color: str) -> str:
    """Обернуть текст в цвет colorama."""
    return f"{color}{text}{_RESET}"


def _err(text: str) -> str:
    return _color(text, _ERR)


def _ok(text: str) -> str:
    return _color(text, _OK)


def _warn(text: str) -> str:
    return _color(text, _WARN)


# ------------------------------------------------------------------ #
# Ошибки: единый формат для CLI и UI
# ------------------------------------------------------------------ #


def print_error(exc: BaseException, *, prefix: str = "Ошибка") -> None:
    """Печатает ошибку красным и подсказку жёлтым."""
    info = describe_error(exc)
    text = f"{prefix}: {info['message']}"
    print(f"\n{_err(' ' + text)}")
    if info.get("hint"):
        print(_color(f" Подсказка: {info['hint']}", Fore.YELLOW))


def log_error(action: str, exc: BaseException) -> None:
    """Логирует ошибку с уровнем, зависящим от её категории."""
    if isinstance(exc, AppError) and exc.is_user_error:
        logger.warning("%s: %s", action, exc)
    else:
        logger.error(
            "%s: %s",
            action,
            exc,
            exc_info=not isinstance(exc, AppError),
        )


def report_problems(manager: NoteManager) -> None:
    """Выводит предупреждения о проблемах, найденных при загрузке хранилища."""
    for problem in getattr(manager, "problems", None) or []:
        print(_warn(f" {problem}"))


# ------------------------------------------------------------------ #
# Отображение заметок
# ------------------------------------------------------------------ #

_NOTE_SEP = _color("─" * 60, Fore.LIGHTBLACK_EX)
_HEADER_SEP = _color("━" * 60, Fore.LIGHTBLACK_EX)


def _format_attachment(raw: str) -> str:
    """Описывает вложение: имя, размер или причину недоступности."""
    ref = resolve(raw, upload_root=UPLOAD_ROOT, legacy_root=LEGACY_ROOT)
    if ref.available:
        return f"{ref.icon} {ref.name} ({ref.human_size})"
    return f"{ref.icon} {ref.name or raw} — {ref.error_message}"


def _format_note(note: Note) -> str:
    """Форматирует одну заметку в красивый блок."""
    lines = [
        f"{_BOLD}{_color(note.title, Fore.CYAN)}{_RESET}",
        f"  id       {note.id}",
        f"  timestamp {note.timestamp}",
    ]
    if note.content:
        lines.append(f"  content   {note.content}")
    if note.attachments:
        lines.append("  attachments")
        lines.extend(f"    • {_format_attachment(raw)}" for raw in note.attachments)
    return _NOTE_SEP + "\n" + "\n".join(lines) + "\n" + _NOTE_SEP


def display_notes(notes: list[Note]) -> None:
    """Выводит список заметок в структурированном виде."""
    if not notes:
        print(_warn("Хранилище пусто — заметок нет."))
        return
    print(f"\n{_BOLD}Заметок: {len(notes)}{_RESET}\n")
    for note in notes:
        print(_format_note(note))
        print()


# ------------------------------------------------------------------ #
# Команды CLI
# ------------------------------------------------------------------ #


def _cmd_add(manager: NoteManager, args: argparse.Namespace) -> int:
    """Создать заметку."""
    attachments = normalize_attachments(
        list(args.attachment) if args.attachment else None,
        base_dir=Path.cwd(),
    )
    note = manager.create_note(
        title=args.title,
        content=args.content,
        attachments=attachments or None,
    )
    print(f"\n{_ok(' Заметка создана!')}")
    print(_format_note(note))
    return 0


def _cmd_list(manager: NoteManager, args: argparse.Namespace) -> int:
    """Вывести все заметки."""
    display_notes(manager.get_all_notes())
    return 0


def _cmd_delete(manager: NoteManager, args: argparse.Namespace) -> int:
    """Удалить заметку по id."""
    note = manager.delete_note(args.id)
    print(f"\n{_ok(' Заметка удалена: ' + note.title)}")
    return 0


def _cmd_edit(manager: NoteManager, args: argparse.Namespace) -> int:
    """Частично обновить заметку."""
    note = manager.get_note_by_id(args.id)

    print(_BOLD + "\nТекущие значения:" + _RESET)
    print(_format_note(note))

    fields: dict[str, object] = {}
    if args.title is not None:
        fields["title"] = args.title
    if args.content is not None:
        fields["content"] = args.content
    if args.attachment is not None:
        fields["attachments"] = normalize_attachments(
            list(args.attachment),
            base_dir=Path.cwd(),
        )

    if not fields:
        print(_warn(" Не переданы поля для обновления (--title / --content / --attachment)."))
        return 0

    updated = manager.update_note(args.id, **fields)
    print(f"\n{_ok(' Заметка обновлена!')}")
    print(_format_note(updated))
    return 0


def _cmd_find(manager: NoteManager, args: argparse.Namespace) -> int:
    """Поиск по подстроке."""
    results = manager.find_notes(args.query)
    if results:
        print(_BOLD + f"\nНайдено {len(results)} по запросу «{args.query}»:{_RESET}")
        display_notes(results)
    else:
        print(_warn(f" Ничего не найдено по запросу «{args.query}»."))
    return 0


def _cmd_ui(manager: NoteManager, args: argparse.Namespace) -> int:
    """Запуск интерактивного режима (делегирование в ui.run)."""
    from app.ui import run

    return run(manager)


# ------------------------------------------------------------------ #
# Парсер
# ------------------------------------------------------------------ #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notemaster",
        description="NoteMaster — управление заметками через терминал",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- add ---
    p = sub.add_parser("add", help="создать заметку")
    p.add_argument("--title", required=True, help="заголовок (обязательный)")
    p.add_argument("--content", default="", help="текст заметки")
    p.add_argument("--attachment", action="append", help="путь к файлу (можно повторять)")
    p.set_defaults(func=_cmd_add)

    # --- list ---
    p = sub.add_parser("list", help="список всех заметок")
    p.set_defaults(func=_cmd_list)

    # --- delete ---
    p = sub.add_parser("delete", help="удалить заметку по id")
    p.add_argument("--id", required=True, help="id заметки")
    p.set_defaults(func=_cmd_delete)

    # --- edit ---
    p = sub.add_parser("edit", help="частично обновить заметку")
    p.add_argument("--id", required=True, help="id заметки")
    p.add_argument("--title", default=None, help="новый заголовок")
    p.add_argument("--content", default=None, help="новый текст")
    p.add_argument("--attachment", action="append", default=None, help="новый список вложений")
    p.set_defaults(func=_cmd_edit)

    # --- find ---
    p = sub.add_parser("find", help="поиск по подстроке")
    p.add_argument("--query", required=True, help="текст для поиска")
    p.set_defaults(func=_cmd_find)

    # --- ui ---
    p = sub.add_parser("ui", help="интерактивный режим")
    p.set_defaults(func=_cmd_ui)

    return parser


# ------------------------------------------------------------------ #
# Точка входа
# ------------------------------------------------------------------ #


def main(argv: Sequence[str] | None = None) -> int:
    """Главный вход CLI.

    Любое исключение превращается в понятное сообщение: приложение
    не завершается трассировкой стека.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        manager = NoteManager()
    except AppError as exc:
        log_error("init", exc)
        print_error(exc, prefix="Не удалось открыть хранилище")
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI не должен падать
        log_error("init", exc)
        print_error(exc, prefix="Не удалось открыть хранилище")
        return 1

    report_problems(manager)

    try:
        return args.func(manager, args)
    except AppError as exc:
        log_error(args.command, exc)
        print_error(exc)
        return 1
    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
        return 130
    except Exception as exc:  # noqa: BLE001 — CLI не должен падать
        log_error(args.command, exc)
        print_error(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
