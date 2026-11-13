"""Командная строка парсера."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_OUTPUT_FILE,
    NEWS_URL,
    RAW_HTML_FILE,
)
from .exporter import write_csv
from .fetcher import FetchError, fetch_news_html
from .parser import parse_news


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yanews",
        description="Собирает заголовки новостей с dzen.ru/news в CSV-файл.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / DEFAULT_OUTPUT_FILE,
        help="путь к CSV-файлу (по умолчанию: output/news.csv)",
    )
    parser.add_argument(
        "-u",
        "--url",
        default=NEWS_URL,
        help=f"адрес ленты (по умолчанию: {NEWS_URL})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="таймаут HTTP-запроса в секундах (по умолчанию: 30)",
    )
    parser.add_argument(
        "--from-html",
        type=Path,
        metavar="FILE",
        help="разобрать сохранённый HTML вместо запроса в сеть",
    )
    parser.add_argument(
        "--save-html",
        type=Path,
        metavar="FILE",
        help=f"сохранить полученный HTML в файл (например, {RAW_HTML_FILE})",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="не печатать заголовки в консоль",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.from_html:
            html = args.from_html.read_text(encoding="utf-8")
        else:
            html = fetch_news_html(args.url, timeout=args.timeout)
    except FetchError as exc:
        print(f"Ошибка загрузки: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Не удалось прочитать файл: {exc}", file=sys.stderr)
        return 1

    if args.save_html:
        args.save_html.parent.mkdir(parents=True, exist_ok=True)
        args.save_html.write_text(html, encoding="utf-8")
        print(f"HTML сохранён: {args.save_html}")

    items = parse_news(html)
    if not items:
        print(
            "Новости не найдены: разметка страницы не совпала с ожидаемой. "
            "Сохраните HTML через --save-html и проверьте селекторы.",
            file=sys.stderr,
        )
        return 2

    path = write_csv(items, args.output)

    if not args.quiet:
        for number, item in enumerate(items, start=1):
            source = f" [{item.source}]" if item.source else ""
            print(f"{number:>3}. {item.title}{source}")

    print(f"\nНайдено новостей: {len(items)}")
    print(f"CSV сохранён: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
