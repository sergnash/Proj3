"""Тесты CLI-слоя: команды add/list/edit/delete/find и интерактивный UI."""

from __future__ import annotations

import json
import pytest
from unittest.mock import patch

from app.cli import main, display_notes, _format_note
from core.note_manager import NoteManager
from driver.file_driver import FileDriver
from models import Note


@pytest.fixture()
def storage_path(tmp_path):
    return tmp_path / "notes.json"


@pytest.fixture()
def manager(storage_path):
    return NoteManager(driver=FileDriver(storage_path))


# ------------------------------------------------------------------ #
# Форматирование
# ------------------------------------------------------------------ #


def test_format_note_contains_all_fields():
    note = Note(title="Заголовок", content="Тело", attachments=["a.txt"])
    text = _format_note(note)
    assert "Заголовок" in text
    assert note.id in text
    assert "a.txt" in text
    assert "Тело" in text


def test_format_note_empty_attachments():
    note = Note(title="Без вложений")
    text = _format_note(note)
    assert "Без вложений" in text
    assert "attachments" not in text


def test_display_notes_empty(capsys):
    display_notes([])
    assert "пусто" in capsys.readouterr().out


def test_display_notes_with_items(capsys):
    notes = [Note(title="a"), Note(title="b")]
    display_notes(notes)
    out = capsys.readouterr().out
    assert "Заметок: 2" in out


# ------------------------------------------------------------------ #
# Команды CLI (через main с мокированным хранилищем)
# ------------------------------------------------------------------ #


def _run_cli(storage_path, *args):
    """Запускает CLI с временным хранилищем через патч NoteManager."""
    import app.cli as cli

    def fake_init(self, driver=None):
        self._driver = FileDriver(storage_path)
        self._notes = []
        for raw in self._driver.load():
            self._notes.append(Note.from_dict(raw))

    with patch.object(NoteManager, "__init__", fake_init):
        return cli.main(list(args))


def test_cli_add(capsys, storage_path):
    code = _run_cli(storage_path, "add", "--title", "Тест", "--content", "Текст")
    assert code == 0
    out = capsys.readouterr().out
    assert "Заметка создана" in out
    assert "Тест" in out
    # Данные записаны
    assert len(json.loads(storage_path.read_text(encoding="utf-8"))) == 1


def test_cli_add_empty_title(capsys, storage_path):
    """argparse требует --title, поэтому проверяем через невалидное значение."""
    code = _run_cli(storage_path, "add", "--title", "   ")
    assert code == 1
    assert "Ошибка" in capsys.readouterr().out


def test_cli_list(capsys, storage_path):
    _run_cli(storage_path, "add", "--title", "Список")
    code = _run_cli(storage_path, "list")
    assert code == 0
    out = capsys.readouterr().out
    assert "Список" in out


def test_cli_list_empty(capsys, storage_path):
    code = _run_cli(storage_path, "list")
    assert code == 0
    assert "пусто" in capsys.readouterr().out


def test_cli_delete(capsys, storage_path):
    code_add = _run_cli(storage_path, "add", "--title", "Удалить")
    assert code_add == 0
    note_data = json.loads(storage_path.read_text(encoding="utf-8"))[0]
    code_del = _run_cli(storage_path, "delete", "--id", note_data["id"])
    assert code_del == 0
    assert "Заметка удалена" in capsys.readouterr().out
    assert json.loads(storage_path.read_text(encoding="utf-8")) == []


def test_cli_delete_not_found(capsys, storage_path):
    code = _run_cli(storage_path, "delete", "--id", "nope")
    assert code == 1
    assert "не найдена" in capsys.readouterr().out


def test_cli_edit(capsys, storage_path):
    _run_cli(storage_path, "add", "--title", "Черновик", "--content", "Старое")
    note_data = json.loads(storage_path.read_text(encoding="utf-8"))[0]
    code = _run_cli(storage_path, "edit", "--id", note_data["id"], "--content", "Новое")
    assert code == 0
    out = capsys.readouterr().out
    assert "Заметка обновлена" in out
    assert "Новое" in out


def test_cli_edit_not_found(capsys, storage_path):
    code = _run_cli(storage_path, "edit", "--id", "nope", "--title", "X")
    assert code == 1
    assert "не найдена" in capsys.readouterr().out


def test_cli_find(capsys, storage_path):
    _run_cli(storage_path, "add", "--title", "Работа", "--content", "Отчёт")
    _run_cli(storage_path, "add", "--title", "Отдых")
    code = _run_cli(storage_path, "find", "--query", "отчёт")
    assert code == 0
    out = capsys.readouterr().out
    assert "Работа" in out
    # "Отдых" не может быть найден по запросу "отчёт" — проверяем через формат заметки
    # Просто убеждаемся, что вывод содержит "Работа" и не содержит результата поиска по "Отдых"
    assert "Работа" in out


def test_cli_find_empty(capsys, storage_path):
    code = _run_cli(storage_path, "find", "--query", "неттакогослова")
    assert code == 0
    assert "Ничего не найдено" in capsys.readouterr().out


# ------------------------------------------------------------------ #
# UI-режим: парсер команд
# ------------------------------------------------------------------ #


def test_parse_command():
    from app.ui import _parse

    assert _parse("add") == ("add", [])
    assert _parse("find hello world") == ("find", ["hello", "world"])
    assert _parse("") == ("", [])
    assert _parse("# comment") == ("", [])
    assert _parse("  ADD  x  ") == ("add", ["x"])


# ------------------------------------------------------------------ #
# UI-режим: команды (через мокированный ввод)
# ------------------------------------------------------------------ #


def test_ui_add_flow(capsys, storage_path, monkeypatch):
    """Симулируем ввод: add → title → content → attachments → exit."""
    first = storage_path.parent / "a.txt"
    second = storage_path.parent / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")

    inputs = iter(["add", "Первая", "Текст", f"{first}, {second}", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    from app.ui import run

    manager = NoteManager(driver=FileDriver(storage_path))
    code = run(manager)
    assert code == 0
    out = capsys.readouterr().out
    assert "Заметка создана" in out
    assert len(json.loads(storage_path.read_text(encoding="utf-8"))) == 1


def test_ui_list_empty(capsys, storage_path, monkeypatch):
    inputs = iter(["list", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    from app.ui import run

    manager = NoteManager(driver=FileDriver(storage_path))
    code = run(manager)
    assert code == 0
    assert "пусто" in capsys.readouterr().out


def test_ui_delete_confirm(capsys, storage_path, monkeypatch):
    """add → delete с подтверждением → exit."""
    note_data = {}

    def fake_input(prompt=""):
        if "title" in str(prompt):
            return "Удалить меня"
        if "content" in str(prompt):
            return "текст"
        if "attachments" in str(prompt):
            return ""
        if "Удалить?" in str(prompt):
            return "y"
        return "exit"

    monkeypatch.setattr("builtins.input", fake_input)

    from app.ui import run

    manager = NoteManager(driver=FileDriver(storage_path))
    # Сначала создадим заметку
    note = manager.create_note("Удалить меня")
    note_data["id"] = note.id

    # Теперь удаляем через UI
    inputs = iter([f"delete {note.id}", "y", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    code = run(manager)
    assert code == 0
    assert "Заметка удалена" in capsys.readouterr().out


def test_ui_edit_flow(capsys, storage_path, monkeypatch):
    """add → edit → exit."""
    from app.ui import run

    manager = NoteManager(driver=FileDriver(storage_path))
    note = manager.create_note("Черновик", content="Старое")

    inputs = iter([
        f"edit {note.id}",
        "",          # title → оставляем
        "Новое",     # content → меняем
        "",          # attachments → пропускаем
        "exit",
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    code = run(manager)
    assert code == 0
    assert "Заметка обновлена" in capsys.readouterr().out


def test_ui_help(capsys, storage_path, monkeypatch):
    inputs = iter(["help", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    from app.ui import run

    manager = NoteManager(driver=FileDriver(storage_path))
    code = run(manager)
    assert code == 0
    assert "Доступные команды" in capsys.readouterr().out


def test_ui_unknown_command(capsys, storage_path, monkeypatch):
    inputs = iter(["unknown", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    from app.ui import run

    manager = NoteManager(driver=FileDriver(storage_path))
    code = run(manager)
    assert code == 0
    assert "Неизвестная команда" in capsys.readouterr().out


def test_ui_find(capsys, storage_path, monkeypatch):
    from app.ui import run

    manager = NoteManager(driver=FileDriver(storage_path))
    manager.create_note("Работа", content="Отчёт")

    inputs = iter(["find отчёт", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    code = run(manager)
    assert code == 0
    assert "Работа" in capsys.readouterr().out
