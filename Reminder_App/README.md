# Reminder App

Простое настольное приложение-напоминалка для Windows 10/11 на tkinter с SQLite и уведомлениями системы (win10toast) с fallback-окнами.

## Структура
- `database.py` — класс `ReminderDatabase`, отвечает за работу с SQLite и CRUD-функции напоминаний.
- `notifications.py` — класс `NotificationManager`, уведомления через Win10 Toast или всплывающее окно.
- `gui.py` — класс `ReminderGUI`, графический интерфейс, фильтры, управление статусами.
- `main.py` — точка входа.

## Требования
- Python 3.11+
- `win10toast`

## Запуск
```
pip install -r requirements.txt
python main.py
```
