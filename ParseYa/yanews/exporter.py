"""Сохранение результатов в CSV."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence

from .parser import CSV_FIELDS, NewsItem


def write_csv(
    items: Sequence[NewsItem],
    path: str | Path,
    *,
    encoding: str = "utf-8-sig",
) -> Path:
    """Записывает новости в CSV и возвращает путь к файлу.

    Кодировка по умолчанию ``utf-8-sig``: Excel на Windows без BOM
    показывает кириллицу как «кракозябры».
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with target.open("w", encoding=encoding, newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for item in items:
            writer.writerow(item.as_row())
    return target
