"""Разбор HTML ленты dzen.ru/news."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from bs4 import BeautifulSoup, Tag

#: Классы в разметке dzen имеют хеш-суффикс (``news-site--card-news__titleLink-2Q``),
#: который меняется при каждом релизе фронтенда. Поэтому ищем по префиксу.
TITLE_LINK_RE = re.compile(r"card-news__titleLink")
TITLE_TEXT_RE = re.compile(r"card-part-news-title__title")
DESCRIPTION_RE = re.compile(r"card-part-news-description__description")
SOURCE_RE = re.compile(r"author-title__text")
TIME_RE = re.compile(r"card-meta__cardMeta")
TOP_CARD_TEXT_RE = re.compile(r"card-top-avatar__text")
CARD_CONTAINER_RE = re.compile(r"card-news__cardNewsWrapper")

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class NewsItem:
    """Одна новость из ленты."""

    title: str
    url: str = ""
    source: str = ""
    published: str = ""
    description: str = ""

    def as_row(self) -> dict[str, str]:
        """Представление для CSV-строки."""
        return {
            "title": self.title,
            "source": self.source,
            "published": self.published,
            "url": self.url,
            "description": self.description,
        }


CSV_FIELDS = ("title", "source", "published", "url", "description")


def clean_text(value: str | None) -> str:
    """Схлопывает пробелы и неразрывные пробелы в один обычный."""
    if not value:
        return ""
    return _WHITESPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()


def _class_matches(tag: Tag, pattern: re.Pattern[str]) -> bool:
    classes = tag.get("class") or []
    return any(pattern.search(cls) for cls in classes)


def _find_by_class(scope: Tag, pattern: re.Pattern[str]) -> Tag | None:
    for tag in scope.find_all(True):
        if _class_matches(tag, pattern):
            return tag
    return None


def _text_of(scope: Tag, pattern: re.Pattern[str]) -> str:
    tag = _find_by_class(scope, pattern)
    return clean_text(tag.get_text(" ", strip=True)) if tag else ""


def _parse_full_card(card: Tag) -> NewsItem | None:
    """Разбирает обычную карточку ленты (article с картинкой и описанием)."""
    link = _find_by_class(card, TITLE_LINK_RE)
    if link is None:
        return None

    title = clean_text(link.get("title")) or _text_of(card, TITLE_TEXT_RE)
    if not title:
        return None

    return NewsItem(
        title=title,
        url=str(link.get("href") or ""),
        source=_text_of(card, SOURCE_RE),
        published=_text_of(card, TIME_RE),
        description=_text_of(card, DESCRIPTION_RE),
    )


def _parse_top_card(text_node: Tag) -> NewsItem | None:
    """Разбирает компактную карточку блока «Главное» (текст без описания).

    Класс с текстом висит на ``<p>`` внутри ``<a>``, поэтому ссылку берём
    у ближайшего предка-якоря.
    """
    title = clean_text(text_node.get_text(" ", strip=True))
    if not title:
        return None

    anchor = text_node if text_node.name == "a" else text_node.find_parent("a")
    return NewsItem(title=title, url=str(anchor.get("href") or "") if anchor else "")


def _dedupe_links(nodes: Iterable[Tag]) -> list[Tag]:
    """Возвращает уникальные контейнеры карточек по их `<a href>`."""
    seen: set[str] = set()
    result: list[Tag] = []
    for node in nodes:
        href = str(node.get("href") or "")
        key = href or id(node)
        if key in seen:
            continue
        seen.add(key)  # type: ignore[arg-type]
        result.append(node)
    return result


def parse_news(html: str) -> list[NewsItem]:
    """Извлекает новости из HTML страницы dzen.ru/news.

    Порядок — как на странице: блок «Главное», затем материалы по рубрикам.
    Дубликаты (одна и та же ссылка встречается в нескольких блоках)
    отбрасываются, сохраняется первое вхождение.
    """
    soup = BeautifulSoup(html, "lxml")
    items: list[NewsItem] = []
    seen_urls: set[str] = set()

    containers = [t for t in soup.find_all(True) if _class_matches(t, CARD_CONTAINER_RE)]

    # Компактные карточки «Главного» лежат вне контейнеров обычных карточек.
    top_links = [t for t in soup.find_all(True) if _class_matches(t, TOP_CARD_TEXT_RE)]

    for node in _dedupe_links(top_links):
        item = _parse_top_card(node)
        if item and item.url not in seen_urls:
            seen_urls.add(item.url)
            items.append(item)

    for card in containers:
        item = _parse_full_card(card)
        if item is None or item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        items.append(item)

    return items
