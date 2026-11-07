"""Чек-лист итерации «Качество»: краевые случаи, восстановление, ошибки.

Каждый тест соответствует пункту mini-checklist из README:

1. файл отсутствует;
2. файл повреждён;
3. пустой список заметок;
4. невалидный id;
5. пустой title;
6. ошибки чтения/записи;
7. некорректные attachments;
8. удаление несуществующей заметки;
9. ошибки CLI-ввода;
10. ошибки HTTP-запросов.

Плюс проверки BOM, отката состояния и раздачи локальных вложений.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from core.attachments import (
    AttachmentState,
    normalize_attachments,
    resolve,
)
from core.errors import (
    AppError,
    AttachmentError,
    NoteNotFoundError,
    NoteValidationError,
    StorageError,
    describe_error,
)
from core.note_manager import NoteManager
from driver.file_driver import FileDriver
from driver.recovery import BACKUP_FILENAME, RecoveryReport, extract_objects
from models import Note


# --------------------------------------------------------------------- #
# Фикстуры
# --------------------------------------------------------------------- #


@pytest.fixture()
def storage_path(tmp_path):
    return tmp_path / "notes.json"


@pytest.fixture()
def manager(storage_path):
    return NoteManager(driver=FileDriver(storage_path))


def read_raw(storage_path):
    return json.loads(storage_path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------- #
# 1. Файл отсутствует
# --------------------------------------------------------------------- #


def test_missing_storage_file_is_created(storage_path):
    driver = FileDriver(storage_path)
    notes = NoteManager(driver=driver).get_all_notes()

    assert notes == []
    assert storage_path.exists()
    assert read_raw(storage_path) == []
    assert driver.last_report is not None
    assert driver.last_report.status == "created"
    assert driver.last_report.changed is True


# --------------------------------------------------------------------- #
# 2. Файл повреждён
# --------------------------------------------------------------------- #


def test_partially_corrupted_json_is_recovered(storage_path):
    storage_path.write_text(
        '[{"title": "Спасённая", "content": "текст", "attachments": []},',
        encoding="utf-8",
    )

    driver = FileDriver(storage_path)
    notes = NoteManager(driver=driver).get_all_notes()

    assert [note.title for note in notes] == ["Спасённая"]
    assert driver.last_report.status == "recovered"
    assert (storage_path.parent / BACKUP_FILENAME).exists()


def test_fully_broken_json_resets_with_backup(storage_path):
    storage_path.write_text("{ это не json", encoding="utf-8")

    driver = FileDriver(storage_path)
    notes = NoteManager(driver=driver).get_all_notes()

    assert notes == []
    assert driver.last_report.status == "reset"
    assert driver.last_report.data_loss is True
    backup = storage_path.parent / BACKUP_FILENAME
    assert backup.exists()
    assert "это не json" in backup.read_text(encoding="utf-8")


def test_bom_and_invalid_bytes_do_not_break_reading(storage_path):
    payload = json.dumps([{"title": "С BOM"}], ensure_ascii=False).encode("utf-8")
    storage_path.write_bytes(b"\xef\xbb\xbf" + payload)

    manager = NoteManager(driver=FileDriver(storage_path))

    assert [note.title for note in manager.get_all_notes()] == ["С BOM"]


def test_extract_objects_ignores_nested_braces_and_quotes():
    text = 'мусор {"title": "a {b}", "nested": {"x": 1}} хвост'
    assert extract_objects(text) == [{"title": "a {b}", "nested": {"x": 1}}]


def test_recovery_report_flags_data_loss():
    report = RecoveryReport(status="repaired", dropped=2)
    assert report.data_loss is True
    assert "отброшено записей: 2" in report.summary()


def test_from_dict_recovers_id_timestamp_and_attachments():
    note = Note.from_dict(
        {
            "title": "Почти корректная",
            "id": 123,
            "timestamp": "не-дата",
            "attachments": ["ok.txt", 42, "", None],
        }
    )

    assert note.id and isinstance(note.id, str)
    assert note.timestamp
    assert note.attachments == ["ok.txt"]
    assert Note.from_dict({"title": "Без полей"}).content == ""


# --------------------------------------------------------------------- #
# 3. Пустой список заметок
# --------------------------------------------------------------------- #


def test_empty_storage_operations_are_user_errors(manager):
    with pytest.raises(NoteNotFoundError) as get_error:
        manager.get_note_by_id("any-id")
    with pytest.raises(NoteNotFoundError) as delete_error:
        manager.delete_note("any-id")

    for error in (get_error.value, delete_error.value):
        assert error.is_user_error is True
        assert "не найдена" in error.message
        assert error.hint


# --------------------------------------------------------------------- #
# 4. Невалидный id
# --------------------------------------------------------------------- #


def test_invalid_id_is_reported_as_user_error(manager):
    manager.create_note("Существует")

    with pytest.raises(NoteValidationError) as exc_info:
        manager.get_note_by_id("   ")

    error = exc_info.value
    assert error.is_user_error
    assert "Не указан id" in error.message
    assert error.hint
    assert error.to_dict()["category"] == "user"


# --------------------------------------------------------------------- #
# 5. Пустой title
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("title", ["", "   ", "\n\t", None])
def test_empty_title_is_rejected(manager, title):
    with pytest.raises(NoteValidationError) as exc_info:
        manager.create_note(title)

    assert "title" in exc_info.value.message
    assert manager.count() == 0


def test_empty_title_on_update_keeps_previous_value(storage_path, manager):
    note = manager.create_note("Оригинал")

    with pytest.raises(NoteValidationError):
        manager.update_note(note.id, title="  ")

    reloaded = NoteManager(driver=FileDriver(storage_path)).get_note_by_id(note.id)
    assert reloaded.title == "Оригинал"


# --------------------------------------------------------------------- #
# 6. Ошибки чтения/записи
# --------------------------------------------------------------------- #


def test_read_error_becomes_system_storage_error(storage_path, monkeypatch):
    storage_path.write_text("[]", encoding="utf-8")

    def broken_read(self):
        raise OSError("диск недоступен")

    monkeypatch.setattr(Path, "read_bytes", broken_read)

    with pytest.raises(StorageError) as exc_info:
        FileDriver(storage_path).load()

    error = exc_info.value
    assert error.category == "system"
    assert error.is_user_error is False
    assert "прочитать" in error.message.lower()


def test_write_error_rolls_back_state(storage_path, monkeypatch):
    manager = NoteManager(driver=FileDriver(storage_path))
    manager.create_note("До сбоя")

    def broken_replace(src, dst):
        raise OSError("нет прав на запись")

    monkeypatch.setattr("driver.file_driver.os.replace", broken_replace)

    with pytest.raises(StorageError):
        manager.create_note("Во время сбоя")

    assert manager.count() == 1
    assert [note.title for note in manager.get_all_notes()] == ["До сбоя"]
    # Старый файл не повреждён неудачной записью.
    assert [item["title"] for item in read_raw(storage_path)] == ["До сбоя"]


# --------------------------------------------------------------------- #
# 7. Некорректные attachments
# --------------------------------------------------------------------- #


def test_invalid_attachments_are_rejected(manager):
    with pytest.raises(NoteValidationError) as exc_info:
        manager.create_note("t", attachments=[42])
    assert "вложен" in exc_info.value.message

    note = manager.create_note("ok")
    with pytest.raises(NoteValidationError):
        manager.update_note(note.id, attachments="not-a-list")


def test_resolve_handles_invalid_missing_and_local(tmp_path):
    invalid = resolve("..\\secret.txt")
    assert invalid.state is AttachmentState.INVALID
    assert invalid.available is False
    assert invalid.error_message

    missing = resolve("nope.pdf", upload_root=tmp_path)
    assert missing.state is AttachmentState.MISSING
    assert "не найден" in missing.error_message

    local_file = tmp_path / "документ.txt"
    local_file.write_text("данные", encoding="utf-8")
    local = resolve(str(local_file))
    assert local.state is AttachmentState.LOCAL
    assert local.available is True
    assert local.size == len("данные".encode("utf-8"))


def test_normalize_attachments_rejects_missing_files(tmp_path):
    existing = tmp_path / "есть.txt"
    existing.write_text("x", encoding="utf-8")

    normalized = normalize_attachments([existing])
    assert normalized == [str(existing.resolve())]

    with pytest.raises(AttachmentError) as exc_info:
        normalize_attachments([tmp_path / "нет.txt"])
    assert exc_info.value.is_user_error
    assert "не найден" in exc_info.value.message
    assert exc_info.value.hint


# --------------------------------------------------------------------- #
# 8. Удаление несуществующей заметки
# --------------------------------------------------------------------- #


def test_delete_nonexistent_note_has_hint(manager):
    manager.create_note("Есть")

    with pytest.raises(NoteNotFoundError) as exc_info:
        manager.delete_note("нет-такой")

    assert "не найдена" in exc_info.value.message
    assert "Обновите список" in (exc_info.value.hint or "")
    assert manager.count() == 1


# --------------------------------------------------------------------- #
# 9. Ошибки CLI-ввода
# --------------------------------------------------------------------- #


def _run_cli(storage_path, *args):
    import app.cli as cli

    def fake_init(self, driver=None):
        self._driver = FileDriver(storage_path)
        self.problems = []
        self._notes = [Note.from_dict(raw) for raw in self._driver.load()]

    with patch.object(NoteManager, "__init__", fake_init):
        return cli.main(list(args))


def test_cli_missing_note_prints_hint(capsys, storage_path, caplog):
    with caplog.at_level(logging.WARNING, logger="app"):
        code = _run_cli(storage_path, "delete", "--id", "nope")

    out = capsys.readouterr().out
    assert code == 1
    assert "Ошибка" in out
    assert "Подсказка" in out
    assert "Traceback" not in out
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_cli_missing_attachment_is_user_error(capsys, storage_path):
    code = _run_cli(
        storage_path,
        "add",
        "--title",
        "С файлом",
        "--attachment",
        "нет-такого-файла.pdf",
    )

    out = capsys.readouterr().out
    assert code == 1
    assert "не найден" in out
    assert read_raw(storage_path) == [] if storage_path.exists() else True


def test_cli_storage_failure_does_not_show_traceback(capsys, monkeypatch):
    import app.cli as cli

    def broken_init(self, driver=None):
        raise StorageError("файл занят другим процессом")

    monkeypatch.setattr(NoteManager, "__init__", broken_init)

    code = cli.main(["list"])

    out = capsys.readouterr().out
    assert code == 1
    assert "Не удалось открыть хранилище" in out
    assert "Traceback" not in out


# --------------------------------------------------------------------- #
# 10. Ошибки HTTP-запросов
# --------------------------------------------------------------------- #


@pytest.fixture()
def upload_root(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    return root


@pytest.fixture()
def web_client(storage_path, upload_root, monkeypatch):
    import web.app as web_app

    monkeypatch.setattr(web_app, "UPLOAD_ROOT", upload_root)
    monkeypatch.setattr(
        web_app,
        "_get_manager",
        lambda: NoteManager(driver=FileDriver(storage_path)),
    )
    web_app.app.config.update(TESTING=True, SECRET_KEY="test")
    return web_app.app.test_client()


def test_web_unknown_route_is_friendly(web_client):
    response = web_client.get("/no-such-page")

    assert response.status_code == 404
    body = response.data.decode()
    assert "Страница не найдена" in body
    assert "Вернуться к заметкам" in body


def test_web_broken_storage_does_not_crash(web_client, storage_path):
    storage_path.write_text("{ сломано", encoding="utf-8")

    response = web_client.get("/")

    assert response.status_code == 200
    assert "Заметок пока нет" in response.data.decode()


def test_web_missing_attachment_returns_friendly_404(web_client, storage_path):
    manager = NoteManager(driver=FileDriver(storage_path))
    note = manager.create_note("Без файла", attachments=["потерянный.pdf"])

    response = web_client.get(f"/attachment/{note.id}/0")

    assert response.status_code == 404
    body = response.data.decode()
    assert "Файл недоступен" in body
    assert "потерянный.pdf" in body


def test_web_invalid_attachment_index_returns_404(web_client, storage_path):
    manager = NoteManager(driver=FileDriver(storage_path))
    note = manager.create_note("Без файлов")

    response = web_client.get(f"/attachment/{note.id}/5")

    assert response.status_code == 404
    assert "Вложение не найдено" in response.data.decode()


def test_web_serves_local_absolute_attachment(web_client, storage_path, tmp_path):
    local_file = tmp_path / "локальный.txt"
    local_file.write_text("локальные данные", encoding="utf-8")

    manager = NoteManager(driver=FileDriver(storage_path))
    note = manager.create_note("Локальный файл", attachments=[str(local_file)])

    response = web_client.get(f"/attachment/{note.id}/0")

    assert response.status_code == 200
    assert response.data.decode("utf-8") == "локальные данные"


def test_web_edit_nonexistent_note_redirects(web_client):
    response = web_client.post("/edit/нет-такой", data={"title": "X"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_web_empty_title_does_not_create_note(web_client, storage_path):
    response = web_client.post("/add", data={"title": "   ", "content": "x"})

    assert response.status_code == 302
    notes = read_raw(storage_path) if storage_path.exists() else []
    assert notes == []


# --------------------------------------------------------------------- #
# Единый формат ошибок
# --------------------------------------------------------------------- #


def test_describe_error_has_uniform_shape():
    user = describe_error(NoteValidationError("Плохой ввод", hint="Исправьте"))
    assert set(user) == {"code", "category", "message", "hint"}
    assert user["category"] == "user"
    assert user["message"] == "Плохой ввод"

    system = describe_error(RuntimeError("секретный стек"))
    assert system["category"] == "system"
    assert system["code"] == "unexpected_error"
    assert "секретный стек" not in system["message"]
    assert system["hint"]


def test_all_app_errors_are_app_error():
    for exc in (
        NoteValidationError("x"),
        NoteNotFoundError("x"),
        StorageError("x"),
        AttachmentError("x"),
    ):
        assert isinstance(exc, AppError)
        assert exc.code
