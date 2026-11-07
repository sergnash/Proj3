"""Тесты веб-модуля: маршруты Flask через test_client."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

sys_path_backup = None
sys_path_restored = False


@pytest.fixture(autouse=True)
def _ensure_sys_path():
    """Добавляет корень проекта в sys.path для импортов core, models, driver."""
    global sys_path_backup, sys_path_restored
    import sys

    root = Path(__file__).resolve().parents[1]
    sys_path_backup = sys.path.copy()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    yield
    sys.path = sys_path_backup
    sys_path_restored = True


@pytest.fixture()
def storage_path(tmp_path):
    return tmp_path / "notes.json"


@pytest.fixture()
def upload_root(tmp_path):
    """Временная папка загруженных вложений."""
    root = tmp_path / "uploads"
    root.mkdir()
    return root


@pytest.fixture()
def client(storage_path, upload_root, monkeypatch):
    """Flask test client с временным хранилищем и папкой загрузок."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from unittest.mock import patch

    import web.app as web_app
    from core.note_manager import NoteManager
    from driver.file_driver import FileDriver
    from models import Note

    monkeypatch.setattr(web_app, "UPLOAD_ROOT", upload_root)

    def fake_init(self, driver=None):
        self._driver = FileDriver(storage_path)
        self._notes = [Note.from_dict(raw) for raw in self._driver.load()]

    with patch.object(NoteManager, "__init__", fake_init):
        web_app.app.config["TESTING"] = True
        web_app.app.config["SECRET_KEY"] = "test"
        yield web_app.app.test_client()

    web_app.app.config["TESTING"] = False


def read_notes(storage_path):
    if not storage_path.exists():
        return []
    return json.loads(storage_path.read_text(encoding="utf-8"))


def upload(name, payload=b"data"):
    """Файл для test_client в форматеwerkzeug."""
    import io

    return (io.BytesIO(payload), name)


def test_index_page(client, storage_path):
    """Главная страница доступна и содержит форму."""
    r = client.get("/")
    assert r.status_code == 200
    html = r.data.decode()
    assert "add-title" in html  # форма
    assert "note-card" in html or "Заметок пока нет" in html


def test_index_empty(client, storage_path):
    """Главная при пустом хранилище."""
    r = client.get("/")
    assert "Заметок пока нет" in r.data.decode()


def test_add_note(client, storage_path):
    """POST /add создаёт заметку и редиректит."""
    r = client.post(
        "/add",
        data={"title": "Web Note", "content": "Hello Flask"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Web Note" in r.data.decode()
    assert len(read_notes(storage_path)) == 1


def test_add_note_empty_title(client, storage_path):
    """Пустой заголовок отклоняется, заметка не создаётся."""
    notes_before = len(read_notes(storage_path))
    r = client.post("/add", data={"title": "", "content": "x"})
    assert r.status_code == 302  # redirect back
    assert len(read_notes(storage_path)) == notes_before


def test_note_detail(client, storage_path):
    """GET /note/<id> показывает содержимое."""
    client.post("/add", data={"title": "Detail", "content": "Body"})
    notes = read_notes(storage_path)
    nid = notes[0]["id"]
    r = client.get(f"/note/{nid}")
    assert r.status_code == 200
    html = r.data.decode()
    assert "Detail" in html
    assert "Body" in html
    assert "Редактировать" in html
    assert "Удалить" in html


def test_note_detail_not_found(client, storage_path):
    """GET /note/<id> для несуществующей заметки — редирект на главную."""
    r = client.get("/note/nonexistent-id")
    assert r.status_code == 302


def test_edit_note(client, storage_path):
    """POST /edit/<id> обновляет заметку."""
    client.post("/add", data={"title": "Old", "content": "Old"})
    notes = read_notes(storage_path)
    nid = notes[0]["id"]
    r = client.post(
        f"/edit/{nid}",
        data={"title": "New", "content": "New content", "attachments_touched": "1"},
    )
    assert r.status_code == 302
    html = client.get(f"/note/{nid}").data.decode()
    assert "New" in html
    assert "New content" in html


def test_edit_note_not_found(client, storage_path):
    """POST /edit/<id> для несуществующей заметки — редирект на главную."""
    r = client.post("/edit/nonexistent", data={"title": "X"})
    assert r.status_code == 302


def test_delete_note(client, storage_path):
    """POST /delete/<id> удаляет заметку и редиректит."""
    client.post("/add", data={"title": "Bye"})
    notes = read_notes(storage_path)
    nid = notes[0]["id"]
    r = client.post(f"/delete/{nid}")
    assert r.status_code == 302
    assert len(read_notes(storage_path)) == 0


def test_delete_nonexistent(client, storage_path):
    """POST /delete/<id> для несуществующей заметки — редирект на главную."""
    r = client.post("/delete/nonexistent")
    assert r.status_code == 302


def test_form_has_all_fields(client, storage_path):
    """Формы содержат поля и multipart для загрузки файлов."""
    html = client.get("/").data.decode()
    assert 'name="title"' in html
    assert 'name="content"' in html
    assert 'name="files"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'type="file"' in html


def test_form_edit_shows_select_button(client, storage_path):
    """В форме редактирования есть кнопка выбора файлов (не textarea путей)."""
    client.post("/add", data={"title": "Note"})
    nid = read_notes(storage_path)[0]["id"]
    html = client.get(f"/note/{nid}").data.decode()
    assert 'name="files"' in html
    assert "Выбрать файлы" in html
    assert 'name="attachments_touched"' in html


# ------------------------------------------------------------------ #
# Вложения: загрузка, хранение, предпросмотр
# ------------------------------------------------------------------ #


def test_add_uploads_files(client, storage_path, upload_root):
    """Выбранные файлы сохраняются как <uuid заметки>_<имя> в каталоге загрузок."""
    r = client.post(
        "/add",
        data={
            "title": "С отчётом",
            "content": "x",
            "files": [upload("Отчёт.pdf", b"%PDF-1.4"), upload("list.txt", b"abc")],
        },
    )
    nid = r.headers["Location"].split("/")[-1]
    attachments = read_notes(storage_path)[0]["attachments"]

    assert len(attachments) == 2
    assert all(path.startswith(f"{nid}_") for path in attachments)
    for path in attachments:
        assert (upload_root / path).exists()


def test_uploaded_file_is_served(client, storage_path, upload_root):
    """GET /uploads/<path> отдаёт файл для предпросмотра в браузере."""
    client.post("/add", data={"title": "T", "files": upload("report.pdf", b"PDF-CONTENT")})
    path = read_notes(storage_path)[0]["attachments"][0]

    r = client.get(f"/uploads/{path}")
    assert r.status_code == 200
    assert r.data == b"PDF-CONTENT"


def test_uploaded_file_missing_returns_404(client):
    """Запрос несуществующего вложения не роняет приложение."""
    r = client.get("/uploads/no-such-note/no-such-file.pdf")
    assert r.status_code == 404


def test_attachment_name_displayed_in_note_page(client, storage_path):
    """Имя файла видно на странице заметки без служебного префикса."""
    client.post("/add", data={"title": "T", "files": upload("Отчёт_квартал.pdf", b"x")})
    nid = read_notes(storage_path)[0]["id"]
    html = client.get(f"/note/{nid}").data.decode()

    assert "Отчёт_квартал.pdf" in html
    assert "Прикреплённые файлы" in html
    assert "/attachment/" in html  # ссылка на предпросмотр


def test_edit_keeps_existing_attachments(client, storage_path):
    """Без отметки на удаление и без новых файлов вложения сохраняются."""
    client.post("/add", data={"title": "T", "files": upload("keep.txt", b"1")})
    notes = read_notes(storage_path)
    nid, before = notes[0]["id"], notes[0]["attachments"]

    client.post(f"/edit/{nid}", data={"title": "T2", "attachments_touched": "1"})
    assert read_notes(storage_path)[0]["attachments"] == before


def test_edit_removes_marked_attachment(client, storage_path, upload_root):
    """Отмеченный remove_attachments файл убирается из JSON и с диска."""
    client.post("/add", data={"title": "T", "files": [upload("a.txt", b"1"), upload("b.txt", b"2")]})
    notes = read_notes(storage_path)
    nid, paths = notes[0]["id"], notes[0]["attachments"]
    doomed = paths[0]

    client.post(
        f"/edit/{nid}",
        data={"title": "T", "attachments_touched": "1", "remove_attachments": doomed},
    )

    left = read_notes(storage_path)[0]["attachments"]
    assert left == [paths[1]]
    assert not (upload_root / doomed).exists()
    assert (upload_root / paths[1]).exists()


def test_edit_adds_new_files_to_existing(client, storage_path, upload_root):
    """Новые файлы добавляются к уже существующим."""
    client.post("/add", data={"title": "T", "files": upload("old.txt", b"1")})
    notes = read_notes(storage_path)
    nid, before = notes[0]["id"], notes[0]["attachments"]

    client.post(
        f"/edit/{nid}",
        data={"title": "T", "attachments_touched": "1", "files": upload("new.txt", b"2")},
    )

    after = read_notes(storage_path)[0]["attachments"]
    assert after[:1] == before
    assert len(after) == 2
    assert (upload_root / after[1]).read_bytes() == b"2"


def test_delete_note_removes_files(client, storage_path, upload_root):
    """Удаление заметки убирает её вложения с диска."""
    client.post("/add", data={"title": "T", "files": upload("gone.txt", b"1")})
    notes = read_notes(storage_path)
    nid, path = notes[0]["id"], notes[0]["attachments"][0]

    client.post(f"/delete/{nid}")

    assert read_notes(storage_path) == []
    assert not (upload_root / path).exists()


def test_delete_note_removes_all_files(client, storage_path, upload_root):
    """Удаление заметки убирает все её вложения из каталога загрузок."""
    client.post("/add", data={"title": "T", "files": [upload("f.txt", b"1"), upload("g.txt", b"2")]})
    nid, paths = read_notes(storage_path)[0]["id"], read_notes(storage_path)[0]["attachments"]

    client.post(f"/delete/{nid}")

    assert not any((upload_root / path).exists() for path in paths)


def test_unsafe_filename_is_sanitized(client, storage_path, upload_root):
    """Имя файла с путём и опасными символами не даёт выйти за хранилище."""
    client.post("/add", data={"title": "T", "files": upload("../../evil.sh", b"rm -rf")})
    path = read_notes(storage_path)[0]["attachments"][0]

    assert ".." not in path
    assert not path.startswith("..")
    assert (upload_root / path).exists()
    assert not (upload_root.parent / "evil.sh").exists()
