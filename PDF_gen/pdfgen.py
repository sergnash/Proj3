#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdfgen.py — генератор PDF-документов из HTML-шаблонов.

Что делает:
  * читает все CSV/JSON в каталоге данных (по умолчанию ./data);
  * читает все HTML-шаблоны в каталоге шаблонов (по умолчанию ./templates);
  * показывает нумерованные меню, даёт выбрать файл данных и шаблон;
  * выводит список счетов (invoice id) и по выбранному рендерит документ;
  * сохраняет PDF в ./output и открывает системным просмотрщиком.

Запуск:   python pdfgen.py
Справка:  python pdfgen.py --help
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from jinja2 import Environment, FileSystemLoader, meta, select_autoescape

try:  # pandas удобнее, но не обязателен — есть запасной путь через csv
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parent
DATA_EXTS = {".csv", ".json"}
TEMPLATE_EXTS = {".html", ".htm"}

# Ключи, по которым ищем идентификатор счёта (сравниваются в нормализованном виде)
INVOICE_KEY_CANDIDATES = (
    "invoiceid", "invoiceno", "invoicenumber", "invoice", "invoicecode",
    "номерсчета", "номерсчёт", "счет", "счёт", "номер", "id",
)

# Шрифты с полной поддержкой кириллицы. Порядок важен: первый найденный побеждает.
FONT_FILES = {
    "windows": {
        "regular": [r"C:\Windows\Fonts\DejaVuSans.ttf", r"C:\Windows\Fonts\Roboto-Regular.ttf"],
        "bold": [r"C:\Windows\Fonts\DejaVuSans-Bold.ttf", r"C:\Windows\Fonts\Roboto-Bold.ttf"],
        "italic": [r"C:\Windows\Fonts\DejaVuSans-Oblique.ttf", r"C:\Windows\Fonts\Roboto-Italic.ttf"],
    },
    "darwin": {
        "regular": [
            "/Library/Fonts/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/DejaVuSans.ttf",
            "/Library/Fonts/Roboto-Regular.ttf",
        ],
        "bold": [
            "/Library/Fonts/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Roboto-Bold.ttf",
        ],
        "italic": [
            "/Library/Fonts/DejaVuSans-Oblique.ttf",
            "/Library/Fonts/Supplemental/DejaVuSans-Oblique.ttf",
        ],
    },
    "linux": {
        "regular": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/roboto/Roboto-Regular.ttf",
        ],
        "bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        ],
        "italic": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Oblique.ttf",
        ],
    },
}


class UserQuit(Exception):
    """Пользователь нажал q / Ctrl+C — выходим без трассировки."""


# --------------------------------------------------------------------------- #
# Консоль
# --------------------------------------------------------------------------- #

def setup_console() -> None:
    """UTF-8 в консоли, чтобы кириллица не превращалась в кракозябры."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def hr(char: str = "─", width: int = 74) -> str:
    return char * width


def heading(text: str) -> None:
    print()
    print(text)
    print(hr())


def info(label: str, value: str) -> None:
    print(f"  {label:<18}{value}")


def human_size(num: int) -> str:
    for unit in ("Б", "КБ", "МБ"):
        if num < 1024 or unit == "МБ":
            return f"{num:.0f} {unit}" if unit == "Б" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.0f} Б"


# --------------------------------------------------------------------------- #
# Чтение файлов данных
# --------------------------------------------------------------------------- #

def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Пробуем типичные кодировки: UTF-8 (с BOM и без), затем Windows-1251."""
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp1252"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8 (с потерями)"


def clean_records(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Строки -> словари с «человеческими» ключами и значениями-строками."""
    out: list[dict[str, Any]] = []
    for row in rows:
        rec: dict[str, Any] = {}
        for key, value in row.items():
            if key is None:
                continue  # лишние ячейки в кривом CSV
            name = str(key).replace("\ufeff", "").strip()
            if not name:
                continue
            if value is None:
                value = ""
            elif not isinstance(value, (str, list, dict)):
                value = str(value)
            elif isinstance(value, str):
                value = value.strip()
            rec[name] = value
        if any(str(v).strip() for v in rec.values()):
            out.append(rec)
    return out


def sniff_delimiter(text: str) -> str:
    """Определяем разделитель CSV: запятая, точка с запятой, табуляция, |."""
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        pass
    # Запасной вариант: считаем символы в первой непустой строке
    first = next((line for line in sample.splitlines() if line.strip()), "")
    counts = {ch: first.count(ch) for ch in ",;\t|"}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def load_csv(path: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    text, enc = decode_bytes(path.read_bytes())
    delimiter = sniff_delimiter(text)
    readable = {",": "запятая", ";": "точка с запятой", "\t": "табуляция", "|": "вертикальная черта"}

    if pd is not None:
        df = pd.read_csv(
            io.StringIO(text),
            dtype=str,
            keep_default_na=False,
            skipinitialspace=True,
            sep=delimiter,
        )
        df.columns = [str(c).strip() for c in df.columns]
        rows = df.to_dict(orient="records")
        how = f"pandas {pd.__version__}, разделитель: {readable.get(delimiter, repr(delimiter))}"
    else:  # запасной путь, если pandas не установлен
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
        how = f"csv (стандартная библиотека), разделитель: {readable.get(delimiter, repr(delimiter))}"
    return clean_records(rows), {"encoding": enc, "parser": how}


def normalize_json(obj: Any) -> list[dict[str, Any]]:
    """Приводим любой разумный JSON к списку записей-словарей."""
    if isinstance(obj, list):
        records = [item for item in obj if isinstance(item, dict)]
        if records:
            return records
        return [{"value": item} for item in obj if not isinstance(item, (dict, list))]

    if isinstance(obj, dict):
        # 1) словарь вида {"id": {...}, "id2": {...}}
        if obj and all(isinstance(v, dict) for v in obj.values()):
            return [{"id": k, **v} for k, v in obj.items()]

        # 2) словарь с вложенным списком записей: {"invoices": [...]}
        shallow = {k: v for k, v in obj.items() if not isinstance(v, (list, dict))}
        best: list[dict[str, Any]] | None = None
        for value in obj.values():
            if isinstance(value, list):
                inner = [item for item in value if isinstance(item, dict)]
                if inner and (best is None or len(inner) > len(best)):
                    best = inner
            elif isinstance(value, dict):
                inner = normalize_json(value)
                if inner and (best is None or len(inner) > len(best)):
                    best = inner
        if best is not None:
            merged = []
            for rec in best:
                merged.append({**shallow, **rec})
            return merged

        # 3) одна запись
        return [obj]

    return []


def load_json(path: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    text, enc = decode_bytes(path.read_bytes())
    records = clean_records(normalize_json(json.loads(text)))
    return records, {"encoding": enc, "parser": "json (стандартная библиотека)"}


def load_records(path: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    return load_csv(path) if path.suffix.lower() == ".csv" else load_json(path)


# Результаты работы самого скрипта: <шаблон>_<счёт>_<дата-время>.<ext>
GENERATED_RE = re.compile(r"_\d{8}-\d{6}\.(html|htm|pdf)$", re.IGNORECASE)


def discover(directory: Path, exts: set[str]) -> list[Path]:
    if not directory.is_dir():
        return []
    found = []
    for path in directory.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in exts:
            continue
        if path.name.startswith((".", "~$")):
            continue
        if GENERATED_RE.search(path.name):
            continue  # не показываем собственные результаты как шаблоны/данные
        found.append(path)
    return sorted(found, key=lambda p: (len(p.relative_to(directory).parts), p.name.lower()))


# --------------------------------------------------------------------------- #
# Счета
# --------------------------------------------------------------------------- #

def normalize_key(key: str) -> str:
    return re.sub(r"[\s_.\-/\\]+", "", str(key).lower())


def invoice_key_of(records: list[dict[str, Any]]) -> str | None:
    keys: list[str] = []
    for rec in records:
        for key in rec:
            if key not in keys:
                keys.append(key)
    if not keys:
        return None

    by_norm = {normalize_key(k): k for k in keys}
    for candidate in INVOICE_KEY_CANDIDATES:
        if candidate in by_norm:
            return by_norm[candidate]
    for key in keys:  # запасной вариант — частичное совпадение
        norm = normalize_key(key)
        if "invoice" in norm or "счет" in norm or "счёт" in norm:
            return key
    return keys[0]


def group_by_invoice(
    records: list[dict[str, Any]], key: str | None
) -> "dict[str, list[dict[str, Any]]]":
    groups: dict[str, list[dict[str, Any]]] = {}
    for index, rec in enumerate(records):
        raw = "" if key is None else str(rec.get(key, "") or "").strip()
        invoice = raw or f"(без номера, запись {index + 1})"
        groups.setdefault(invoice, []).append(rec)
    return groups


def describe_record(rec: dict[str, Any], invoice_key: str | None) -> str:
    """Короткая строка-подсказка в меню выбора счёта."""
    hints: list[str] = []
    for probe in ("date", "invoice_date", "дата", "buyer", "customer", "покупатель", "total", "amount", "сумма", "status"):
        for key, value in rec.items():
            if normalize_key(key).startswith(normalize_key(probe)) and str(value).strip():
                if key != invoice_key and len(hints) < 2:
                    hints.append(f"{key}: {str(value).strip()[:28]}")
                break
    return " · ".join(hints)


# --------------------------------------------------------------------------- #
# Меню
# --------------------------------------------------------------------------- #

def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise UserQuit


def choose(prompt: str, count: int, back_label: str | None = None) -> int | None:
    """Нумерованный выбор. Возвращает индекс (0-based) или None при выходе."""
    while True:
        raw = ask(prompt)
        if raw.lower() in {"q", "quit", "exit", "й", "выход", "0"}:
            return None
        if raw.lower() in {"b", "back", "н", "назад"} and back_label:
            return None
        if raw.isdigit() and 1 <= int(raw) <= count:
            return int(raw) - 1
        print(f"  ! Введите число от 1 до {count}" + (" (или q — выход)" if back_label else ""))


def print_files(paths: list[Path], base: Path, labels: list[str] | None = None) -> None:
    width = max((len(p.relative_to(base).as_posix()) for p in paths), default=10)
    for i, path in enumerate(paths, 1):
        name = path.relative_to(base).as_posix()
        label = labels[i - 1] if labels else f"{path.suffix.upper().lstrip('.')}, {human_size(path.stat().st_size)}"
        print(f"  {i:>2}) {name:<{width}}  {label}")


def print_invoices(groups: "dict[str, list[dict[str, Any]]]", invoice_key: str | None) -> None:
    width = max((len(name) for name in groups), default=8)
    for i, (name, recs) in enumerate(groups.items(), 1):
        hint = describe_record(recs[0], invoice_key)
        extra = f"  [{len(recs)} записей]" if len(recs) > 1 else ""
        print(f"  {i:>3}) {name:<{min(width, 28)}}  {hint}{extra}")


# --------------------------------------------------------------------------- #
# Шаблон и печать
# --------------------------------------------------------------------------- #

def font_css() -> str:
    """@font-face на локальный DejaVu Sans (или Roboto) — гарантия кириллицы."""
    platform_key = "windows" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux")
    variants = FONT_FILES.get(platform_key, {})

    # Шрифт, положенный пользователем в assets/fonts/, важнее системного
    local = ROOT / "assets" / "fonts" / "DejaVuSans.ttf"
    faces: list[str] = []
    if local.is_file():
        faces.append(f'@font-face {{ font-family: "DocSans"; src: url("{local.as_uri()}"); }}')
        faces.append('@font-face { font-family: "DocSans"; font-weight: bold; '
                     f'src: url("{(ROOT / "assets" / "fonts" / "DejaVuSans-Bold.ttf").as_uri()}"); }}'
                     if (ROOT / "assets" / "fonts" / "DejaVuSans-Bold.ttf").is_file() else "")
    else:
        for style, weight in (("regular", "normal"), ("bold", "bold"), ("italic", "italic")):
            for candidate in variants.get(style, []):
                path = Path(candidate)
                if path.is_file():
                    decl = "" if weight == "normal" else f" font-weight: {weight};"
                    faces.append(
                        f'@font-face {{ font-family: "DocSans";{decl} src: url("{path.as_uri()}"); }}'
                    )
                    break

    faces = [f for f in faces if f]
    stack = '"DocSans", "DejaVu Sans", "Roboto", "Segoe UI", Arial, sans-serif'
    return (
        "\n".join(faces)
        + "\n"
        + f'html, body, p, td, th, div, span, li, h1, h2, h3, h4, h5, h6 {{ font-family: {stack}; }}'
    )


def money(value: Any) -> str:
    """Форматирование суммы: 1234567.5 -> 1 234 567,50 (по возможности)."""
    if value is None or value == "":
        return "—"
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return str(value)
    formatted = f"{number:,.2f}".replace(",", "\u2009")
    return formatted


def render(
    template_path: Path,
    template_dir: Path,
    context: dict[str, Any],
    strict: bool = False,
) -> tuple[str, list[str]]:
    """Рендерит шаблон. Возвращает (html, список отсутствующих полей)."""
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "htm"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["money"] = money
    env.filters["dash"] = lambda v: str(v).strip() if str(v).strip() else "—"
    source = (template_dir / template_path.relative_to(template_dir)).read_text(encoding="utf-8")

    # Мягкая проверка: шаблон и файл данных могут не подходить друг другу
    declared = meta.find_undeclared_variables(env.parse(source))
    missing = sorted(name for name in declared if name not in context)
    if missing and strict:
        available = ", ".join(sorted(context)) or "—"
        raise RuntimeError(
            f"Шаблон {template_path.name} ожидает поля, которых нет в данных: "
            f"{', '.join(missing)}.\n"
            f"  Доступные поля: {available}"
        )

    template = env.from_string(source)
    try:
        html = template.render(**context)
    except Exception as exc:  # например, обращение к вложенному полю отсутствующей записи
        raise RuntimeError(
            f"Не удалось отрисовать {template_path.name}: {exc}\n"
            f"  Похоже, шаблон рассчитан на другую структуру данных. "
            f"Доступные поля: {', '.join(sorted(context))}"
        ) from exc

    # Вставляем @font-face последним в <head>, чтобы он победил стили шаблона
    style = f'<style id="pdfgen-fonts">\n{font_css()}\n</style>'
    lowered = html.lower()
    head_close = lowered.find("</head>")
    if head_close != -1:
        html = html[:head_close] + style + "\n" + html[head_close:]
    else:
        html = style + "\n" + html
    return html, missing


GTK_DLL_DIR_HINTS = (
    # стандартный GTK3 Runtime для Windows
    r"C:\Program Files\GTK3-Runtime Win64\bin",
    r"C:\Program Files (x86)\GTK3-Runtime Win32\bin",
    str(Path.home() / "AppData" / "Local" / "gtk3-runtime" / "bin"),
    # GTK-библиотеки, которые ставятся вместе с другими программами
    r"C:\Program Files\Inkscape\bin",
    r"C:\Program Files\GIMP 2\bin",
    r"C:\Program Files\GIMP 3\bin",
    r"C:\Program Files\LibreOffice\program",
)


def prepare_gtk_dlls() -> None:
    """Windows: подсказать загрузчику, где лежат libpango / libgobject.

    На Linux/macOS ничего не нужно — там библиотеки ищутся системно.
    """
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    extra = os.environ.get("PDFGEN_GTK_DIR")
    candidates = ([extra] if extra else []) + list(GTK_DLL_DIR_HINTS)
    for folder in candidates:
        path = Path(folder)
        if not path.is_dir():
            continue
        if not any(path.glob("libpango-1.0*.dll")):
            continue
        try:
            os.add_dll_directory(str(path))
        except OSError:
            continue


def write_pdf(html: str, base_url: Path, target: Path) -> None:
    prepare_gtk_dlls()
    try:
        from weasyprint import HTML  # импорт ленивый: понятная ошибка, если нет Pango
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "WeasyPrint не может загрузиться — не найдены библиотеки Pango/GTK.\n"
            "  Windows: установите GTK3 Runtime — https://github.com/tschoonj/"
            "GTK-for-Windows-Runtime-Environment-Installer/releases\n"
            "           либо укажите путь к папке с libpango-1.0-0.dll в переменной "
            "PDFGEN_GTK_DIR\n"
            "  macOS:   brew install pango libffi\n"
            "  Linux:   sudo apt install libpango-1.0-0 libpangoft2-1.0-0\n"
            "Пока можно вывести документ в HTML: запустите с флагом --html."
        ) from exc

    HTML(string=html, base_url=str(base_url)).write_pdf(target=str(target))


def open_file(path: Path, how: str = "system") -> None:
    try:
        if how == "browser":
            webbrowser.open(path.as_uri())
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        print(f"  ! Не удалось открыть файл автоматически: {exc}")
        print(f"    Откройте вручную: {path}")


def safe_filename(text: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", text, flags=re.UNICODE).strip("_")
    return cleaned[:60] or "document"


def build_context(
    record: dict[str, Any],
    group: list[dict[str, Any]],
    invoice: str,
    data_path: Path,
    template_path: Path,
    columns: list[str],
) -> dict[str, Any]:
    context: dict[str, Any] = dict(record)  # поля доступны напрямую: {{ buyer }}
    context.update(
        {
            "record": record,
            "invoice": record,
            "invoice_id": invoice,
            "rows": group,
            "documents": group,
            "columns": columns or list(record.keys()),
            "generated_at": datetime.now(),
            "source": {"file": data_path.name, "path": str(data_path)},
            "template_name": template_path.name,
        }
    )
    return context


# --------------------------------------------------------------------------- #
# Основной сценарий
# --------------------------------------------------------------------------- #

def generate(
    data_path: Path,
    template_path: Path,
    template_dir: Path,
    invoice: str,
    output_dir: Path,
    do_open: bool,
    open_how: str,
    html_only: bool,
    strict: bool = False,
) -> Path:
    records, info = load_records(data_path)
    if not records:
        raise RuntimeError(f"В файле {data_path.name} не найдено записей.")

    invoice_key = invoice_key_of(records)
    groups = group_by_invoice(records, invoice_key)
    if invoice not in groups:
        available = ", ".join(list(groups)[:10])
        raise RuntimeError(f"Счёт «{invoice}» не найден. Доступные: {available}")

    group = groups[invoice]
    columns = list(records[0].keys())
    context = build_context(group[0], group, invoice, data_path, template_path, columns)

    html, missing = render(template_path, template_dir, context, strict=strict)

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if html_only:
        target = output_dir / f"{safe_filename(template_path.stem)}_{safe_filename(invoice)}_{stamp}.html"
        target.write_text(html, encoding="utf-8")
    else:
        target = output_dir / f"{safe_filename(template_path.stem)}_{safe_filename(invoice)}_{stamp}.pdf"
        write_pdf(html, template_path.parent, target)

    print()
    print(f"  ✔ Готово: {target}")
    info_line = f"{info['parser']}, кодировка {info['encoding']}, записей: {len(records)}"
    print(f"  · {info_line}")
    if len(group) > 1:
        print(f"  · по счёту «{invoice}» найдено записей: {len(group)} (в документ попала первая)")
    if missing:
        print(
            f"  ! В шаблоне используются отсутствующие поля: {', '.join(missing)} — "
            f"они выведены пустыми. Запустите с --strict, чтобы это была ошибка."
        )

    if do_open:
        open_file(target, open_how)
    return target


def ensure_dirs(*dirs: Path) -> list[Path]:
    """Создаёт каталоги, которых нет. Возвращает список созданных."""
    created: list[Path] = []
    for folder in dirs:
        if folder.is_dir():
            continue
        try:
            folder.mkdir(parents=True, exist_ok=True)
            created.append(folder)
        except OSError as exc:
            print(f"  ! Не удалось создать каталог {folder}: {exc}")
    return created


def main(argv: list[str] | None = None) -> int:
    setup_console()

    parser = argparse.ArgumentParser(
        description="Генерация PDF из HTML-шаблона по данным CSV/JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python pdfgen.py\n"
            "  python pdfgen.py --list\n"
            "  python pdfgen.py --data data/invoices.csv --template templates/invoice.html "
            "--invoice INV-1001\n"
            "  python pdfgen.py --all --no-open\n"
        ),
    )
    parser.add_argument("-d", "--data-dir", default=None, help="каталог с CSV/JSON (по умолчанию ./data)")
    parser.add_argument("-t", "--template-dir", default=None, help="каталог с HTML-шаблонами (по умолчанию ./templates)")
    parser.add_argument("-o", "--output-dir", default=str(ROOT / "output"), help="каталог для PDF (по умолчанию ./output)")
    parser.add_argument("--data", help="файл данных — пропускает интерактивный выбор")
    parser.add_argument("--template", help="HTML-шаблон — пропускает интерактивный выбор")
    parser.add_argument("--invoice", help="invoice id — пропускает интерактивный выбор")
    parser.add_argument("--all", action="store_true", help="сгенерировать документы по всем счетам выбранного файла")
    parser.add_argument("--list", action="store_true", help="только показать доступные данные и шаблоны")
    parser.add_argument("--html", action="store_true", help="сохранить HTML вместо PDF (не нужен WeasyPrint)")
    parser.add_argument("--strict", action="store_true", help="ошибка, если шаблон использует отсутствующие в данных поля")
    parser.add_argument("--no-open", action="store_true", help="не открывать результат")
    parser.add_argument("--open-with", choices=("system", "browser"), default="system", help="чем открывать результат")
    args = parser.parse_args(argv)

    data_dir_given = args.data_dir is not None
    template_dir_given = args.template_dir is not None
    data_dir = Path(args.data_dir or (ROOT / "data")).expanduser().resolve()
    template_dir = Path(args.template_dir or (ROOT / "templates")).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    try:
        created = ensure_dirs(data_dir, template_dir, output_dir)
        data_files = discover(data_dir, DATA_EXTS)
        templates = discover(template_dir, TEMPLATE_EXTS)

        # Каталоги по умолчанию пусты, а рядом лежат демо-данные — показываем их
        examples_dir = ROOT / "examples"
        demo_note: list[str] = []
        if examples_dir.is_dir() and not data_dir_given and not template_dir_given:
            if not data_files:
                demo_files = discover(examples_dir, DATA_EXTS)
                if demo_files:
                    data_dir, data_files = examples_dir, demo_files
                    demo_note.append("данные взяты из examples/ (data/ пуст)")
            if not templates:
                demo_templates = discover(examples_dir, TEMPLATE_EXTS)
                if demo_templates:
                    template_dir, templates = examples_dir, demo_templates
                    demo_note.append("шаблоны взяты из examples/ (templates/ пуст)")

        heading("PDF Generator  ·  HTML → PDF")
        info("Каталог данных:", str(data_dir))
        info("Каталог шаблонов:", str(template_dir))
        info("Каталог вывода:", str(output_dir))
        for folder in created:
            print(f"  + создан каталог: {folder}")
        for note in demo_note:
            print(f"  · {note}")

        # ---- 1. Файлы данных ------------------------------------------------
        print()
        print("Файлы с данными" + ("" if data_files else " — не найдено"))
        print(hr())
        if not data_files:
            print(f"  Положите .csv или .json в {data_dir} и запустите снова.")
            return 2
        print_files(data_files, data_dir)

        # ---- 2. Шаблоны -----------------------------------------------------
        print()
        print("HTML-шаблоны" + ("" if templates else " — не найдено"))
        print(hr())
        if not templates:
            print(f"  Положите .html в {template_dir} и запустите снова.")
            return 2
        print_files(templates, template_dir, [f"{human_size(p.stat().st_size)}" for p in templates])

        if args.list:
            print()
            print("Режим --list: ничего не генерирую.")
            return 0

        # ---- Выбор файла данных --------------------------------------------
        if args.data:
            data_path = Path(args.data).expanduser().resolve()
            if not data_path.is_file():
                print(f"  ! Файл не найден: {data_path}")
                return 2
        else:
            print()
            idx = choose(f"Выберите файл данных [1-{len(data_files)}] (q — выход): ", len(data_files), "back")
            if idx is None:
                print("Выход.")
                return 0
            data_path = data_files[idx]

        # ---- Выбор шаблона --------------------------------------------------
        if args.template:
            template_path = Path(args.template).expanduser().resolve()
            if not template_path.is_file():
                print(f"  ! Шаблон не найден: {template_path}")
                return 2
        else:
            print()
            idx = choose(f"Выберите шаблон [1-{len(templates)}] (q — выход): ", len(templates), "back")
            if idx is None:
                print("Выход.")
                return 0
            template_path = templates[idx]

        # ---- Чтение данных и список счетов ----------------------------------
        records, file_info = load_records(data_path)
        if not records:
            print(f"  ! В файле {data_path.name} не найдено записей.")
            return 2

        invoice_key = invoice_key_of(records)
        groups = group_by_invoice(records, invoice_key)

        print()
        print(f"Файл: {data_path.name}")
        info("Записей:", str(len(records)))
        info("Кодировка:", file_info["encoding"])
        info("Парсер:", file_info["parser"])
        info("Поле счёта:", invoice_key or "не определено")

        print()
        print(f"Доступные счета — {len(groups)}")
        print(hr())
        print_invoices(groups, invoice_key)

        if args.all:
            chosen = list(groups)
        elif args.invoice:
            if args.invoice not in groups:
                print(f"  ! Счёт «{args.invoice}» не найден.")
                return 2
            chosen = [args.invoice]
        else:
            print()
            idx = choose(
                f"Выберите счёт [1-{len(groups)}] (q — выход): ", len(groups), "back"
            )
            if idx is None:
                print("Выход.")
                return 0
            chosen = [list(groups)[idx]]

        print()
        print(hr())
        failures = 0
        for i, invoice in enumerate(chosen, 1):
            if len(chosen) > 1:
                print(f"\n[{i}/{len(chosen)}] Счёт {invoice}")
            try:
                generate(
                    data_path,
                    template_path,
                    template_dir,
                    invoice,
                    output_dir,
                    do_open=not args.no_open,
                    open_how=args.open_with,
                    html_only=args.html,
                    strict=args.strict,
                )
            except Exception as exc:
                failures += 1
                print(f"  ✖ Ошибка для счёта {invoice}: {exc}")

        return 1 if failures else 0

    except UserQuit:
        print("\nВыход.")
        return 0
    except Exception as exc:  # единая точка для понятных сообщений
        print(f"\n✖ Ошибка: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
