"""Тесты ядра: модель Note, FileDriver и NoteManager (бизнес-правила, JSON-хранилище)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from core.note_manager import NoteManager, NoteNotFoundError, NoteValidationError
from driver.file_driver import FileDriver
from models import Note


@pytest.fixture()
def storage_path(tmp_path):
    """Путь к временному JSON-хранилищу."""
    return tmp_path / "notes.json"


@pytest.fixture()
def manager(storage_path):
    return NoteManager(driver=FileDriver(storage_path))


def read_raw(storage_path):
    """Сырое содержимое JSON-хранилища."""
    return json.loads(storage_path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------- #
# Модель Note
# --------------------------------------------------------------------- #


def test_note_defaults_and_roundtrip():
    note = Note(title="Заголовок")
    assert note.id and note.content == "" and note.attachments == []
    datetime.fromisoformat(note.timestamp)  # timestamp — валидный ISO 8601

    restored = Note.from_dict(note.to_dict())
    assert restored == note


def test_note_rejects_invalid_fields():
    with pytest.raises(ValueError):
        Note(title="")
    with pytest.raises(ValueError):
        Note(title="   ")
    with pytest.raises(ValueError):
        Note(title="t", attachments=["ok", 42])
    with pytest.raises(ValueError):
        Note(title="t", attachments="not-a-list")
    with pytest.raises(ValueError):
        Note.from_dict(["список вместо объекта"])


# --------------------------------------------------------------------- #
# FileDriver: хранилище и обработка ошибок
# --------------------------------------------------------------------- #


def test_storage_file_created_if_missing(storage_path):
    assert not storage_path.exists()
    manager = NoteManager(driver=FileDriver(storage_path))

    assert storage_path.exists()
    assert read_raw(storage_path) == []
    assert manager.get_all_notes() == []


def test_empty_file_treated_as_no_notes(storage_path):
    storage_path.write_text("", encoding="utf-8")
    manager = NoteManager(driver=FileDriver(storage_path))
    assert manager.get_all_notes() == []


def test_corrupted_json_is_recovered_with_backup(storage_path):
    """Повреждённый JSON не роняет приложение: записи спасаются, делается бэкап."""
    storage_path.write_text(
        '[{"title": "Спасённая", "content": "тело", "attachments": []},',
        encoding="utf-8",
    )

    manager = NoteManager(driver=FileDriver(storage_path))

    assert [note.title for note in manager.get_all_notes()] == ["Спасённая"]
    assert (storage_path.parent / "notes_backup.json").exists()
    assert manager.problems == []


def test_root_not_a_list_is_wrapped_into_list(storage_path):
    """Объект вместо списка превращается в список из одной заметки."""
    storage_path.write_text(
        json.dumps({"title": "объект вместо списка"}),
        encoding="utf-8",
    )

    manager = NoteManager(driver=FileDriver(storage_path))

    assert [note.title for note in manager.get_all_notes()] == ["объект вместо списка"]
    assert read_raw(storage_path)[0]["title"] == "объект вместо списка"
    assert (storage_path.parent / "notes_backup.json").exists()


def test_invalid_record_is_skipped_not_fatal(storage_path):
    """Некорректная запись пропускается, остальные заметки загружаются."""
    storage_path.write_text(
        json.dumps([{"title": ""}, {"title": "Рабочая"}]),
        encoding="utf-8",
    )

    manager = NoteManager(driver=FileDriver(storage_path))

    assert [note.title for note in manager.get_all_notes()] == ["Рабочая"]
    assert manager.problems, "ожидалось предупреждение о пропущенной записи"
    assert (storage_path.parent / "notes_backup.json").exists()


# --------------------------------------------------------------------- #
# NoteManager: создание и чтение
# --------------------------------------------------------------------- #


def test_create_note_fields_and_persistence(storage_path, manager):
    note = manager.create_note("Покупки", content="Молоко", attachments=["C:/list.txt"])

    assert note.title == "Покупки" and note.content == "Молоко"
    datetime.fromisoformat(note.timestamp)
    assert read_raw(storage_path)[0]["title"] == "Покупки"


@pytest.mark.parametrize("title", ["", "   ", None])
def test_create_note_empty_title_raises(manager, title):
    with pytest.raises(NoteValidationError):
        manager.create_note(title)
    assert manager.get_all_notes() == []


def test_create_note_bad_attachments_raises(manager):
    with pytest.raises(NoteValidationError):
        manager.create_note("t", attachments=["ok", 42])
    assert manager.get_all_notes() == []


def test_get_all_notes_returns_copies(manager):
    manager.create_note("a")
    manager.create_note("b")

    notes = manager.get_all_notes()
    notes.clear()
    assert len(manager.get_all_notes()) == 2


def test_get_note_by_id(manager):
    created = manager.create_note("Цель", content="тело")

    assert manager.get_note_by_id(created.id) == created
    with pytest.raises(NoteNotFoundError):
        manager.get_note_by_id("нет-такого-id")


def test_find_notes(manager):
    manager.create_note("Работа", content="Отчёт")
    manager.create_note("Отдых")

    found = manager.find_notes("отчёт")
    assert [n.title for n in found] == ["Работа"]


# --------------------------------------------------------------------- #
# NoteManager: обновление
# --------------------------------------------------------------------- #


def test_update_is_partial_and_bumps_timestamp(storage_path, manager):
    note = manager.create_note("Черновик", content="текст", attachments=["a.txt"])
    old_timestamp = note.timestamp

    updated = manager.update_note(note.id, title="Чистовик")

    assert updated.title == "Чистовик"
    assert updated.content == "текст"
    assert updated.attachments == ["a.txt"]
    assert updated.id == note.id
    assert datetime.fromisoformat(updated.timestamp) >= datetime.fromisoformat(old_timestamp)
    assert read_raw(storage_path)[0]["title"] == "Чистовик"


def test_update_unknown_id_raises(manager):
    manager.create_note("существует")
    with pytest.raises(NoteNotFoundError):
        manager.update_note("нет-такого-id", title="любое")


def test_update_empty_storage_raises(manager):
    with pytest.raises(NoteNotFoundError):
        manager.update_note("любой-id", title="любое")


def test_update_empty_title_raises_and_keeps_state(storage_path):
    manager = NoteManager(driver=FileDriver(storage_path))
    note = manager.create_note("Оригинал")

    with pytest.raises(NoteValidationError):
        manager.update_note(note.id, title="  ")

    assert NoteManager(driver=FileDriver(storage_path)).get_note_by_id(note.id).title == "Оригинал"


def test_update_bad_attachments_and_unknown_field_raise(manager):
    note = manager.create_note("t")
    with pytest.raises(NoteValidationError):
        manager.update_note(note.id, attachments=[1, 2])
    with pytest.raises(NoteValidationError):
        manager.update_note(note.id, colour="red")
    with pytest.raises(NoteValidationError):
        manager.update_note(note.id)  # ни одного поля
    with pytest.raises(NoteValidationError):
        manager.update_note(note.id, id="подмена-id")


# --------------------------------------------------------------------- #
# NoteManager: удаление
# --------------------------------------------------------------------- #


def test_delete_note_removes_and_persists(storage_path, manager):
    note = manager.create_note("Временная")

    deleted = manager.delete_note(note.id)

    assert deleted.id == note.id
    assert manager.get_all_notes() == []
    assert read_raw(storage_path) == []


def test_delete_unknown_id_raises(manager):
    manager.create_note("существует")
    with pytest.raises(NoteNotFoundError):
        manager.delete_note("нет-такого-id")


def test_delete_empty_storage_raises(manager):
    with pytest.raises(NoteNotFoundError):
        manager.delete_note("любой-id")


# --------------------------------------------------------------------- #
# Сквозное сохранение между экземплярами
# --------------------------------------------------------------------- #


def test_notes_survive_new_manager(storage_path):
    first = NoteManager(driver=FileDriver(storage_path))
    note = first.create_note("Пережила перезапуск", attachments=["x.txt"])
    first.update_note(note.id, content="обновлено")
    first.create_note("Вторая")

    second = NoteManager(driver=FileDriver(storage_path))
    reloaded = second.get_note_by_id(note.id)
    assert reloaded.content == "обновлено"
    assert reloaded.attachments == ["x.txt"]
    assert len(second.get_all_notes()) == 2
