"""Настройки и константы проекта."""

from __future__ import annotations

from pathlib import Path

#: Страница, которую парсим.
NEWS_URL = "https://dzen.ru/news"

#: Главная dzen.ru. Загружается перед лентой, чтобы получить куки сессии:
#: без них сайт отдаёт заглушку антибот-редиректа вместо HTML ленты.
WARMUP_URL = "https://dzen.ru/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: Accept-Encoding намеренно не задаётся: requests сам объявляет только
#: те кодировки, которые умеет распаковать (br — лишь при установленном
#: пакете brotli). Жёсткий "gzip, deflate, br" даёт бинарный мусор вместо HTML.
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

#: Папка для результатов по умолчанию.
DEFAULT_OUTPUT_DIR = Path("output")
DEFAULT_OUTPUT_FILE = "news.csv"

#: Имя файла, в который сохраняется «сырой» HTML (используется в --save-html).
RAW_HTML_FILE = "news.html"
