"""Тесты разбора HTML. Сеть не используется."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from yanews.exporter import write_csv
from yanews.parser import clean_text, parse_news

FULL_CARD = """
<article class="news-site--card-part-wrapper__cardPartWrapper-bn
                news-site--card-news__cardNewsWrapper-2Z card-base">
  <a class="news-site--card-news__cardLink-_n" data-testid="card-link" href="{link}"></a>
  <div class="news-site--card-part-author__authorWrapper-Tf">
    <div class="news-site--card-author__authorTitle-yU"
         title="Источник">Вести</div>
    <span class="news-site--author-title__text-39">Вести</span>
  </div>
  <div class="news-site--card-meta__cardMeta-FV">
    <span>Час назад</span>
  </div>
  <div class="news-site--card-part-content-wrapper__contentWrapper-27">
    <a class="news-site--card-news__titleLink-2Q"
       href="{link}"
       title="{title}">
      <div class="news-site--card-part-news-title__title-3m">{title}</div>
    </a>
    <div class="news-site--card-part-news-description__description-3W">
      Короткое&nbsp;описание.
    </div>
  </div>
</article>
"""

TOP_CARD = """
<div class="news-top-stories-v2__other">
  <a class="news-site--card-top-avatar__rootElement-1U" href="{link}">
    <p class="news-site--card-top-avatar__text-SL">{title}</p>
  </a>
</div>
"""


def page(*cards: str) -> str:
    return "<html><body>" + "".join(cards) + "</body></html>"


class ParseNewsTests(unittest.TestCase):
    def test_parses_full_card_fields(self) -> None:
        html = page(
            FULL_CARD.format(link="https://dzen.ru/news/story/1", title="Заголовок один")
        )
        items = parse_news(html)

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.title, "Заголовок один")
        self.assertEqual(item.url, "https://dzen.ru/news/story/1")
        self.assertEqual(item.source, "Вести")
        self.assertEqual(item.published, "Час назад")
        self.assertEqual(item.description, "Короткое описание.")

    def test_parses_compact_top_card(self) -> None:
        html = page(
            TOP_CARD.format(link="https://dzen.ru/news/story/2", title="Главная новость")
        )
        items = parse_news(html)

        self.assertEqual([i.title for i in items], ["Главная новость"])
        self.assertEqual(items[0].url, "https://dzen.ru/news/story/2")
        self.assertEqual(items[0].source, "")

    def test_deduplicates_repeated_links(self) -> None:
        card = FULL_CARD.format(link="https://dzen.ru/news/story/1", title="Один")
        html = page(card, card, card)

        self.assertEqual(len(parse_news(html)), 1)

    def test_keeps_page_order(self) -> None:
        html = page(
            TOP_CARD.format(link="https://dzen.ru/news/story/top", title="Топ"),
            FULL_CARD.format(link="https://dzen.ru/news/story/a", title="Рубрика А"),
            FULL_CARD.format(link="https://dzen.ru/news/story/b", title="Рубрика Б"),
        )
        titles = [i.title for i in parse_news(html)]

        self.assertEqual(titles, ["Топ", "Рубрика А", "Рубрика Б"])

    def test_ignores_cards_without_title(self) -> None:
        html = page(
            '<article class="news-site--card-news__cardNewsWrapper-2Z">'
            '<a class="news-site--card-news__titleLink-2Q" href="https://x"></a>'
            "</article>"
        )

        self.assertEqual(parse_news(html), [])

    def test_empty_page_returns_empty_list(self) -> None:
        self.assertEqual(parse_news("<html><body></body></html>"), [])


class CleanTextTests(unittest.TestCase):
    def test_collapses_whitespace_and_nbsp(self) -> None:
        self.assertEqual(clean_text("  Новость   о   чём-то \n"), "Новость о чём-то")

    def test_handles_empty_input(self) -> None:
        self.assertEqual(clean_text(None), "")
        self.assertEqual(clean_text(""), "")


class WriteCsvTests(unittest.TestCase):
    def test_writes_header_rows_and_utf8_bom(self) -> None:
        html = page(
            FULL_CARD.format(link="https://dzen.ru/news/story/1", title="Ёлка в Москве")
        )
        items = parse_news(html)

        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(items, Path(tmp) / "nested" / "news.csv")

            self.assertTrue(path.exists())
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))

            with path.open(encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Ёлка в Москве")
        self.assertEqual(rows[0]["source"], "Вести")


if __name__ == "__main__":
    unittest.main()
