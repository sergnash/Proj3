"""Flask-приложение NoteMaster — веб-интерфейс поверх NoteManager.

Запуск (из корня проекта):
    python -m web.app

Или напрямую:
    cd web && python app.py

Или с параметрами:
    python -m web.app --debug --port 8080 --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path для импортов core, models, driver
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.exceptions import HTTPException

from core.attachments import (
    LEGACY_ROOT,
    UPLOAD_ROOT,
    build_stored_name,
    display_name,
    human_size,
    icon_for,
    resolve,
)
from core.errors import AppError, describe_error
from core.note_manager import NoteManager

# ------------------------------------------------------------------ #
# Настройка логирования HTTP
# ------------------------------------------------------------------ #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("web")

# ------------------------------------------------------------------ #
# Flask-приложение
# ------------------------------------------------------------------ #

# Абсолютные пути к шаблонам и статике — независимость от cwd и способа запуска.
_WEB_DIR = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(_WEB_DIR / "templates"),
    static_folder=str(_WEB_DIR / "static"),
)
app.secret_key = "notemaster-dev-key"
# Ограничение размера загрузки: 25 МБ на запрос
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


def _get_manager() -> NoteManager:
    """Создаёт свежий NoteManager (перечитывает JSON на каждый запрос)."""
    return NoteManager()


def _flash_problems(manager: NoteManager) -> None:
    """Показывает пользователю проблемы, найденные при загрузке хранилища."""
    for problem in getattr(manager, "problems", None) or []:
        flash(problem, "warning")


def _flash_error(exc: BaseException) -> None:
    """Показывает ошибку с подсказкой в едином формате."""
    info = describe_error(exc)
    text = info["message"]
    if info.get("hint"):
        text = f"{text} {info['hint']}"
    flash(text, "error")
    if isinstance(exc, AppError) and exc.is_user_error:
        logger.warning("Пользовательская ошибка: %s", exc)
    else:
        logger.error(
            "Системная ошибка: %s",
            exc,
            exc_info=not isinstance(exc, AppError),
        )


# ------------------------------------------------------------------ #
# Вложения: сохранение, имена, раздача для предпросмотра
#
# Браузер из соображений безопасности НЕ передаёт реальный путь файла,
# поэтому загруженные файлы копируются в web/static/uploads под именем
# «<uuid заметки>_<оригинальное имя>». В JSON сохраняется это имя,
# а раздаёт файл маршрут /attachment/<id>/<номер>.
#
# Вложения из CLI могут быть абсолютными путями (Вариант 1): такие файлы
# не копируются, а открываются по локальному пути через тот же маршрут.
# ------------------------------------------------------------------ #


def _upload_root() -> Path:
    """Текущий каталог загрузок (может быть подменён в тестах)."""
    return Path(UPLOAD_ROOT)


def _legacy_root() -> Path:
    return Path(LEGACY_ROOT)


def _unique_target(root: Path, note_id: str, filename: str) -> Path:
    """Подбирает свободное имя файла в каталоге загрузок."""
    target = root / build_stored_name(note_id, filename)
    if not target.exists():
        return target

    stem, suffix = target.stem, target.suffix
    for index in range(1, 1000):
        candidate = root / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    return root / f"{stem}-{note_id[:8]}{suffix}"


def _save_uploads(files, note_id: str) -> tuple[list[str], list[str]]:
    """Сохраняет выбранные файлы.

    Returns:
        ``(сохранённые имена, сообщения об ошибках)``.
    """
    saved: list[str] = []
    errors: list[str] = []
    root = _upload_root()

    for file in files:
        if file is None or not file.filename:
            continue
        try:
            root.mkdir(parents=True, exist_ok=True)
            target = _unique_target(root, note_id, file.filename)
            file.save(target)
        except OSError as exc:
            logger.error("Не удалось сохранить вложение %r: %s", file.filename, exc)
            errors.append(f"«{file.filename}»: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 — один файл не должен ронять запрос
            logger.exception("Ошибка при сохранении вложения %r", file.filename)
            errors.append(f"«{file.filename}»: {exc}")
            continue
        saved.append(target.name)
        logger.info("Загружено вложение %r → %s", file.filename, target.name)

    return saved, errors


def _delete_uploads(raw_paths: list[str]) -> None:
    """Удаляет файлы вложений, лежащие внутри известных каталогов.

    Локальные файлы пользователя (абсолютные пути вне проекта) не удаляются —
    из заметки убирается только ссылка.
    """
    for raw in raw_paths:
        ref = resolve(raw, upload_root=_upload_root(), legacy_root=_legacy_root())
        if ref.state.value not in {"stored", "legacy"} or ref.path is None:
            logger.info("Вложение %s не удаляется с диска (вне хранилища)", raw)
            continue
        try:
            ref.path.unlink(missing_ok=True)
            logger.info("Удалено вложение %s", ref.path)
        except OSError as exc:
            logger.error("Не удалось удалить вложение %s: %s", raw, exc)


def _attachment_view(raw: str, note_id: str, index: int) -> dict[str, object]:
    """Данные вложения для шаблона."""
    ref = resolve(
        raw,
        upload_root=_upload_root(),
        legacy_root=_legacy_root(),
        note_id=note_id,
    )
    return {
        "raw": raw,
        "index": index,
        "name": ref.name or display_name(raw, note_id),
        "icon": icon_for(ref.name or raw),
        "size": human_size(ref.size),
        "available": ref.available,
        "error": ref.error_message,
        "url": url_for("attachment_file", note_id=note_id, index=index),
    }


def _attachment_views(note) -> list[dict[str, object]]:
    return [
        _attachment_view(raw, note.id, index)
        for index, raw in enumerate(note.attachments)
    ]


# ------------------------------------------------------------------ #
# Логирование запросов
# ------------------------------------------------------------------ #


@app.before_request
def _log_request():
    logger.info("%s %s", request.method, request.path)


@app.after_request
def _log_response(response):
    logger.info("→ %d %s %s", response.status_code, request.method, request.path)
    return response


# ------------------------------------------------------------------ #
# Обработчики ошибок
# ------------------------------------------------------------------ #


def _error_html(title: str, body: str, status: int) -> tuple[str, int]:
    """Встроенная HTML-страница ошибки (без шаблонов Jinja)."""
    safe_title = html.escape(title)
    safe_body = html.escape(body)
    return (
        f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>{safe_title}</title>
<style>
body {{ font-family: 'Segoe UI', sans-serif; background: #fafafa; color: #2d2d2d;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; margin: 0; }}
.box {{ text-align: center; padding: 2rem; max-width: 34rem; }}
h1 {{ font-size: 1.5rem; color: #e53935; }}
p {{ color: #666; line-height: 1.5; }}
a {{ color: #1e88e5; }}
</style></head><body><div class="box">
<h1>{safe_title}</h1><p>{safe_body}</p>
<p><a href="{html.escape(url_for('index'))}">← Вернуться к заметкам</a></p>
</div></body></html>""",
        status,
    )


@app.errorhandler(404)
def _handle_404(exc):
    return _error_html(
        "Страница не найдена",
        "Запрошенный адрес не существует. Возможно, заметка была удалена.",
        404,
    )


@app.errorhandler(500)
def _handle_500(exc):
    logger.exception("Необработанная ошибка сервера: %s", exc)
    return _error_html(
        "Внутренняя ошибка сервера",
        "Что-то пошло не так. Попробуйте обновить страницу; подробности — в логах.",
        500,
    )


@app.errorhandler(413)
def _handle_too_large(exc):
    limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    flash(f"Файлы слишком большие: ограничение {limit_mb} МБ на запрос.", "error")
    referer = request.headers.get("Referer", "")
    return redirect(referer) if referer else redirect(url_for("index"))


@app.errorhandler(Exception)
def _handle_unexpected(exc):
    """Обрабатывает всё, что не поймано выше: приложение не падает."""
    if isinstance(exc, HTTPException):
        return _error_html(
            exc.name or "Ошибка запроса",
            exc.description or "Запрос не может быть обработан.",
            exc.code or 500,
        )

    _flash_error(exc)
    try:
        return redirect(url_for("index"))
    except Exception:  # noqa: BLE001 — шаблоны могут быть недоступны
        return _error_html(
            "Внутренняя ошибка сервера",
            "Произошла непредвиденная ошибка. Проверьте логи приложения.",
            500,
        )


# ------------------------------------------------------------------ #
# Вспомогательные фильтры Jinja
# ------------------------------------------------------------------ #


@app.template_filter("short_id")
def short_id(note_id: str) -> str:
    """Сокращает UUID до 8 символов."""
    return note_id[:8] if note_id else ""


# ------------------------------------------------------------------ #
# Маршруты
# ------------------------------------------------------------------ #


@app.route("/")
def index():
    """Главная: список всех заметок."""
    notes = []
    try:
        manager = _get_manager()
        _flash_problems(manager)
        notes = manager.get_all_notes()
    except AppError as exc:
        _flash_error(exc)
    except Exception as exc:  # noqa: BLE001 — страница должна открыться
        _flash_error(exc)
    return render_template("index.html", notes=notes)


@app.route("/note/<note_id>")
def note_detail(note_id: str):
    """Просмотр и редактирование заметки."""
    try:
        manager = _get_manager()
        note = manager.get_note_by_id(note_id)
    except AppError as exc:
        _flash_error(exc)
        return redirect(url_for("index"))
    except Exception as exc:  # noqa: BLE001
        _flash_error(exc)
        return redirect(url_for("index"))

    return render_template(
        "note.html",
        note=note,
        attachments=_attachment_views(note),
    )


@app.route("/attachment/<note_id>/<int:index>")
def attachment_file(note_id: str, index: int):
    """Отдаёт вложение по номеру в списке заметки.

    Работает и для загруженных файлов, и для локальных путей из CLI.
    Если файл не найден — понятная страница вместо падения.
    """
    try:
        manager = _get_manager()
        note = manager.get_note_by_id(note_id)
    except AppError as exc:
        _flash_error(exc)
        return _error_html("Заметка не найдена", str(exc), 404)

    if not 0 <= index < len(note.attachments):
        return _error_html(
            "Вложение не найдено",
            "В заметке нет файла с таким номером. Возможно, он был удалён.",
            404,
        )

    raw = note.attachments[index]
    ref = resolve(
        raw,
        upload_root=_upload_root(),
        legacy_root=_legacy_root(),
        note_id=note_id,
    )
    if not ref.available or ref.path is None:
        logger.warning("Вложение недоступно: note=%s index=%d raw=%r", note_id, index, raw)
        return _error_html(
            "Файл недоступен",
            f"{ref.error_message or 'Файл не найден'}. "
            f"Вложение «{ref.name or raw}» было перемещено или удалено.",
            404,
        )

    return send_file(ref.path, conditional=True)


@app.route("/uploads/<path:rel_path>")
def uploaded_file(rel_path: str):
    """Совместимость со старыми ссылками на загруженные файлы."""
    ref = resolve(rel_path, upload_root=_upload_root(), legacy_root=_legacy_root())
    if not ref.available or ref.path is None:
        return _error_html(
            "Файл не найден",
            "Вложение было перемещено или удалено.",
            404,
        )
    return send_file(ref.path, conditional=True)


@app.route("/add", methods=["POST"])
def add_note():
    """Создание заметки из формы (вложения — загруженные файлы)."""
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()

    try:
        manager = _get_manager()
        note = manager.create_note(title, content=content)
    except AppError as exc:
        _flash_error(exc)
        return redirect(url_for("index"))
    except Exception as exc:  # noqa: BLE001
        _flash_error(exc)
        return redirect(url_for("index"))

    saved, errors = _save_uploads(request.files.getlist("files"), note.id)
    if saved:
        try:
            note = manager.update_note(note.id, attachments=saved)
        except AppError as exc:
            _flash_error(exc)
            _delete_uploads(saved)
    if errors:
        flash("Не удалось сохранить файлы: " + "; ".join(errors), "error")

    flash(f"Заметка «{note.title}» создана!", "success")
    return redirect(url_for("note_detail", note_id=note.id))


@app.route("/edit/<note_id>", methods=["POST"])
def edit_note(note_id: str):
    """Редактирование заметки: поля + добавление/удаление вложений."""
    fields: dict[str, object] = {}

    title = request.form.get("title")
    if title is not None:
        fields["title"] = title.strip()

    content = request.form.get("content")
    if content is not None:
        fields["content"] = content.strip()

    try:
        manager = _get_manager()
        note = manager.get_note_by_id(note_id)

        if request.form.get("attachments_touched"):
            current = note.attachments
            removed = set(request.form.getlist("remove_attachments"))
            kept = [path for path in current if path not in removed]
            if removed:
                _delete_uploads(sorted(removed))

            saved, errors = _save_uploads(request.files.getlist("files"), note_id)
            if errors:
                flash("Не удалось сохранить файлы: " + "; ".join(errors), "error")
            kept += saved
            if kept != current:
                fields["attachments"] = kept

        if not fields:
            flash("Изменений нет — заметка осталась прежней.", "warning")
            return redirect(url_for("note_detail", note_id=note_id))

        note = manager.update_note(note_id, **fields)
        flash(f"Заметка «{note.title}» обновлена!", "success")
        return redirect(url_for("note_detail", note_id=note.id))
    except AppError as exc:
        _flash_error(exc)
        return redirect(url_for("index"))
    except Exception as exc:  # noqa: BLE001
        _flash_error(exc)
        return redirect(url_for("index"))


@app.route("/delete/<note_id>", methods=["POST"])
def delete_note(note_id: str):
    """Удаление заметки и её загруженных вложений."""
    try:
        manager = _get_manager()
        note = manager.get_note_by_id(note_id)
        _delete_uploads(note.attachments)
        manager.delete_note(note_id)
        flash(f"Заметка «{note.title}» удалена.", "success")
    except AppError as exc:
        _flash_error(exc)
    except Exception as exc:  # noqa: BLE001
        _flash_error(exc)
    return redirect(url_for("index"))


# ------------------------------------------------------------------ #
# Запуск
# ------------------------------------------------------------------ #

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notemaster-web",
        description="NoteMaster — веб-интерфейс (Flask)",
    )
    parser.add_argument("--debug", action="store_true", help="режим отладки (reloader)")
    parser.add_argument("--port", type=int, default=5000, help="порт (по умолчанию 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="адрес (по умолчанию 127.0.0.1)")
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    debug = args.debug
    use_reloader = debug

    try:
        app.run(
            host=args.host,
            port=args.port,
            debug=debug,
            use_reloader=use_reloader,
            use_debugger=debug,
        )
    except OSError as exc:
        if "address" in str(exc).lower() or "port" in str(exc).lower():
            print(
                f"\nОшибка: порт {args.port} уже занят.\n"
                f"Убедитесь, что другой экземпляр Flask не запущен,\n"
                f"или запустите с --port 8080",
                file=sys.stderr,
            )
        else:
            print(f"\nОшибка запуска: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
