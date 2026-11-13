"""Загрузка HTML-страницы новостей."""

from __future__ import annotations

import requests

from .config import DEFAULT_HEADERS, NEWS_URL, WARMUP_URL


class FetchError(RuntimeError):
    """Не удалось получить страницу с новостями."""


def _is_antibot_stub(html: str) -> bool:
    """Проверяет, что сервер вернул заглушку SSO-редиректа вместо ленты.

    При отсутствии сессионных куки dzen.ru отдаёт короткую страницу с
    <body></body> и скриптом, который сам себя отправляет на
    sso.passport.yandex.ru. Настоящая лента весит сотни килобайт.
    """
    lowered = html.lower()
    return len(html) < 50_000 and (
        "sso.passport.yandex.ru" in lowered or "sso.dzen.ru" in lowered
    )


def fetch_news_html(
    url: str = NEWS_URL,
    *,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> str:
    """Возвращает HTML страницы новостей.

    Перед запросом ленты открывает главную dzen.ru в той же сессии: сайт
    выставляет куки (yandexuid, zen_sso_checked и др.), без которых на
    /news приходит антибот-редирект.

    :raises FetchError: при сетевой ошибке, не-2xx статусе или заглушке.
    """
    sess = session or requests.Session()
    sess.headers.update(DEFAULT_HEADERS)

    try:
        sess.get(WARMUP_URL, timeout=timeout)
        response = sess.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise FetchError(f"Сетевая ошибка при запросе {url}: {exc}") from exc

    if not response.ok:
        raise FetchError(
            f"Сервер вернул HTTP {response.status_code} для {response.url}"
        )

    html = response.text
    if _is_antibot_stub(html):
        raise FetchError(
            "Получена антибот-заглушка вместо ленты новостей. "
            "Обычно помогает повторный запуск (куки обновятся) "
            "или VPN, если доступ ограничен в вашем регионе."
        )
    return html
