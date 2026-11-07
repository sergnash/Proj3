"""Тонкая обёртка: делегирует весь CLI-интерфейс в app.cli.main().

Запуск:
    python -m app.main add --title "..."
    python -m app.main list
    python -m app.main ui
"""

from __future__ import annotations
import sys

from app.cli import main

sys.exit(main())

