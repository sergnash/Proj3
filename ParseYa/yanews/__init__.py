"""Парсер заголовков новостей с dzen.ru/news."""

from .fetcher import FetchError, fetch_news_html
from .parser import NewsItem, parse_news
from .exporter import write_csv

__all__ = ["FetchError", "fetch_news_html", "NewsItem", "parse_news", "write_csv"]
__version__ = "1.0.0"
