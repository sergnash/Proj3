# NoteMaster — система управления заметками

**NoteMaster** — проект с перспективой масштабирования в полноценный продукт
(API, UI, интеграции). Текущая итерация — **«Качество»**: системная
стабилизация, предсказуемая обработка ошибок, восстановление повреждённого
хранилища и корректная работа с вложениями.

## Структура проекта

```
project_root/
├── app/
│   ├── main.py            # Тонкая обёртка: делегирует в cli.main()
│   ├── cli.py             # CLI: argparse (add, list, delete, edit, find, ui)
│   └── ui.py              # Интерактивный REPL (add, list, edit, delete, find, help, exit)
├── web/
│   ├── app.py             # Flask-приложение (маршруты заметок и вложений)
│   ├── templates/
│   │   ├── base.html      # Базовый шаблон: плавающий фон, flash-сообщения
│   │   ├── index.html     # Главная: карточки заметок + форма добавления
│   │   ├── note.html      # Просмотр и редактирование заметки
│   │   └── partials/
│   │       ├── form_add.html
│   │       └── form_edit.html
│   └── static/
│       ├── uploads/        # Файлы, загруженные через веб (UUID_имя)
│       ├── css/styles.css  # Белая тема, цитрусовые акценты, карточки, анимации
│       └── js/
│           ├── background.js  # Плавающие полупрозрачные круги
│           └── ui.js          # Подтверждение удаления, авто-скрытие flash
├── core/
│   ├── __init__.py        # Ленивые реэкспорты (разрыв цикла core ↔ driver)
│   ├── note_manager.py    # Ядро: класс NoteManager и бизнес-правила
│   ├── errors.py          # Иерархия ошибок: пользовательские и системные
│   └── attachments.py     # Резолвинг вложений: stored / legacy / local / missing
├── models.py              # Модель данных Note и валидация полей
├── driver/
│   ├── file_driver.py     # Файловый слой: абстракция доступа к хранилищу
│   └── recovery.py        # Проверка, восстановление и бэкап JSON
├── data/
│   └── notes.json         # Хранилище: список JSON-объектов
├── tests/
│   ├── test_core.py       # Тесты ядра и драйвера
│   ├── test_cli.py        # Тесты CLI и UI-режима
│   ├── test_web.py        # Тесты Flask-маршрутов
│   └── test_quality.py    # Чек-лист краевых случаев (итерация «Качество»)
├── requirements.txt
├── .gitignore
└── README.md
```

## Назначение слоёв

| Слой | Модуль | Ответственность |
|---|---|---|
| **Модель** | `models.py` | Класс `Note` (`id`, `title`, `content`, `timestamp`, `attachments`), валидация полей, сериализация `to_dict` / `from_dict` |
| **Ошибки** | `core/errors.py` | `AppError` → пользовательские (`ValidationError`, `NotFoundError`, `AttachmentError`) и системные (`StorageError`, `FileAccessError`); код, категория, подсказка, единый `describe_error()` |
| **Драйвер** | `driver/file_driver.py` | Абстракция хранилища: `BaseDriver` (интерфейс) и `FileDriver` (JSON-файл). Загрузка/сохранение, атомарная запись, резервное копирование |
| **Восстановление** | `driver/recovery.py` | Чтение с BOM и битыми байтами, спасение объектов из повреждённого JSON, бэкап `notes_backup.json`, отчёт `RecoveryReport` |
| **Ядро** | `core/note_manager.py` | `NoteManager` — единственная точка работы с заметками: CRUD, бизнес-правила, устойчивая загрузка, логирование |
| **Вложения** | `core/attachments.py` | Резолвинг путей (`stored` / `legacy` / `local` / `missing` / `invalid`), санитайзинг имён, размеры, иконки |
| **CLI** | `app/cli.py` | `argparse`-парсер: команды `add`, `list`, `delete`, `edit`, `find`. Цветной вывод, ошибки с подсказками |
| **UI** | `app/ui.py` | Интерактивный REPL-цикл. Подтверждение удаления, повторный ввод при пустых полях, показ старых значений |
| **Web** | `web/app.py` | Flask-приложение. Маршруты заметок и `/attachment/<id>/<n>`, flash-сообщения, дружелюбные страницы ошибок |

Зависимости направлены «вниз»: `app / web → core → driver / models`.

## Бизнес-правила

* нельзя редактировать или удалять заметки, если хранилище пусто;
* обновление происходит только после получения заметки по id
  (`NoteNotFoundError` иначе);
* обновление частичное — изменяются только переданные поля
  (`title`, `content`, `attachments`); поля `id` и `timestamp` защищены от правки,
  `timestamp` обновляется автоматически;
* `title` не может быть пустым;
* `attachments` — список строк;
* отсутствующий `notes.json` создаётся автоматически;
* повреждённый JSON восстанавливается, исходник сохраняется в
  `notes_backup.json`; если восстановить нельзя — создаётся пустой файл,
  а приложение продолжает работу.

## Обработка ошибок

Все ошибки приложения наследуются от `core.errors.AppError` и делятся на две
категории:

| Категория | Примеры | Поведение |
|---|---|---|
| **Пользовательская** (`category="user"`) | пустой `title`, неверный id, отсутствующий файл вложения | показывается как есть + подсказка; логируется как `WARNING`; приложение продолжает работу |
| **Системная** (`category="system"`) | нет прав на запись, повреждённое хранилище, недоступный диск | пользователь видит понятное сообщение без технических деталей; полный текст — в логе с уровнем `ERROR` |

Единый формат (`AppError.to_dict()` / `describe_error()`):

```json
{
  "code": "note_not_found",
  "category": "user",
  "message": "Заметка с id «…» не найдена",
  "hint": "Возможно, она была удалена. Обновите список заметок."
}
```

CLI печатает сообщение красным, подсказку — жёлтым; веб показывает их
flash-блоком. Любое непойманное исключение перехватывается на границе
интерфейса, поэтому пользователь не видит трассировку стека.

## Mini-checklist итерации «Качество»

| Сценарий | Ожидаемое поведение | Тест |
|---|---|---|
| Файл отсутствует | создаётся пустой `notes.json`, статус `created` | `test_missing_storage_file_is_created` |
| Файл повреждён | записи спасаются, создаётся `notes_backup.json` | `test_partially_corrupted_json_is_recovered` |
| Восстановление невозможно | пустое хранилище + бэкап, статус `reset` | `test_fully_broken_json_resets_with_backup` |
| BOM / битые байты | файл читается без ошибки | `test_bom_and_invalid_bytes_do_not_break_reading` |
| Пустой список заметок | операции дают пользовательскую ошибку, не падение | `test_empty_storage_operations_are_user_errors` |
| Невалидный id | `NoteValidationError` с подсказкой | `test_invalid_id_is_reported_as_user_error` |
| Пустой `title` | заметка не создаётся, состояние не меняется | `test_empty_title_is_rejected`, `test_empty_title_on_update_keeps_previous_value` |
| Ошибки чтения/записи | `StorageError`, откат состояния в памяти | `test_read_error_becomes_system_storage_error`, `test_write_error_rolls_back_state` |
| Некорректные `attachments` | `NoteValidationError` / `AttachmentError` | `test_invalid_attachments_are_rejected`, `test_normalize_attachments_rejects_missing_files` |
| Удаление несуществующей заметки | `NoteNotFoundError` с подсказкой | `test_delete_nonexistent_note_has_hint` |
| Ошибки CLI-ввода | код возврата 1, текст и подсказка, без traceback | `test_cli_missing_note_prints_hint`, `test_cli_storage_failure_does_not_show_traceback` |
| Ошибки HTTP-запросов | дружелюбная 404-страница, редирект, нет 500 | `test_web_unknown_route_is_friendly`, `test_web_missing_attachment_returns_friendly_404` |
| Локальные вложения (Вариант 1) | абсолютный путь открывается через `/attachment/...` | `test_web_serves_local_absolute_attachment` |

## Вложения

Вложение в JSON — строка. Поддерживаются два варианта:

1. **Локальный путь (приоритет).** CLI и API сохраняют абсолютный путь к
   существующему файлу (`C:\Users\...\отчёт.pdf`). Путь проверяется при
   добавлении: если файла нет — пользователь получает понятную ошибку.
2. **Хранилище приложения (fallback).** Браузер не передаёт реальный путь
   файла, поэтому веб-интерфейс копирует загрузки в `web/static/uploads/`
   под именем `<uuid заметки>_<оригинальное имя>`:

```json
"attachments": [
  "0286352b-3029-4547-bc0c-818a99996015_отчёт.pdf",
  "0286352b-3029-4547-bc0c-818a99996015_readme.txt"
]
```

На странице заметки вложение открывается через
`GET /attachment/<id_заметки>/<номер>`: маршрут сам определяет, где лежит файл
(`stored`, `legacy` или `local`). Если файл перемещён или удалён, показывается
карточка «⚠ Файл не найден на диске», а маршрут возвращает дружелюбную
404-страницу — приложение не падает.

Особенности:

* имя файла очищается от пути и опасных символов; коллизии получают суффикс;
* резолвинг защищён от выхода за пределы каталога (path traversal);
* при редактировании существующие вложения можно оставить, отметить «удалить»
  или добавить новые;
* при удалении заметки удаляются только файлы внутри хранилища; локальные
  файлы пользователя не трогаются;
* ограничение размера запроса — 25 МБ (`MAX_CONTENT_LENGTH`).

## Пример использования NoteManager

```python
# запуск из корня проекта: python demo.py
from core.note_manager import NoteManager, NoteNotFoundError

manager = NoteManager()                      # по умолчанию data/notes.json

note = manager.create_note(
    "Список покупок",
    content="Молоко, хлеб",
    attachments=["C:/notes/list.txt"],
)

note = manager.update_note(note.id, content="Молоко, хлеб, сыр")  # частичное обновление

for n in manager.get_all_notes():
    print(n.id, n.title, n.timestamp)

try:
    manager.delete_note(note.id)
except NoteNotFoundError as exc:
    print("Не вышло:", exc)
```

Логирование через стандартный `logging`: создание, обновление, удаление и
ошибки пишутся в лог (`logging.basicConfig(level=logging.INFO)` — например,
как это делает `app/main.py`).

## CLI

```powershell
```powershell
python -m app.main add --title "Первая заметка" --content "Текст" --attachment C:/f.txt
python -m app.main list
python -m app.main edit --id <uuid> --content "Новый текст"
python -m app.main find --query текст
python -m app.main delete --id <uuid>
python -m app.main ui                         # интерактивный режим
```

Пути вложений проверяются: если файла нет, команда завершится с понятной
ошибкой и подсказкой, а заметка не будет создана.
```

## Интерактивный режим

```
=== NoteMaster — интерактивный режим ===
Введите help для списка команд. Вводите ? для подсказки.

nm> add
  title [   ]: Покупки
  content [(пусто)]: Молоко, хлеб
  attachments [(нет)]: C:/list.txt

✓ Заметка создана!
────────────────────────────────────────────────────────────
Покупки
  id       ...
  timestamp ...
  content   Молоко, хлеб
  attachments C:/list.txt
────────────────────────────────────────────────────────────

nm> edit <id>
  title [Покупки]: ?        ← пропускаем (оставляем текущее)
  content [Молоко, хлеб]: Молоко, хлеб, сыр
  ...

nm> delete <id>
  Удалить? [y/N]: y
✓ Заметка удалена: Покупки

nm> help                    ← показать справку
nm> exit                    ← выйти
```

## Запуск

```powershell
# Web-интерфейс
python -m web.app                     # порт 5000, без reloader
python -m web.app --debug --port 8080 # отладка + свой порт

# CLI
python -m app.main add --title "Привет" --content "Текст"
python -m app.main list
python -m app.main ui

# Тесты
python -m pytest tests -v
```

> **Важно:** по умолчанию `use_reloader=False`. Релоадер (автоматическая перезагрузка
> при изменении файлов) запускается только с флагом `--debug`. Это предотвращает
> проблему с осиротевшими дочерними процессами, которые держат порт после
> остановки сервера.

## Устранение неполадок

### Порт уже занят

Если при запуске появляется ошибка «Address already in use» — значит,
предыдущий экземпляр сервера не полностью остановился (остался дочерний процесс).

**Решение:** запустите с другим портом:
```powershell
python -m web.app --port 8080
```

**Если порт всё ещё занят:** найдите и завершите процессы, держащие порт:
```powershell
# Найти PID процесса, держащего порт 5000
netstat -ano | findstr :5000
# Завершить (замените PID на найденный)
taskkill /F /PID <PID>
```

### Страница не отображается в браузере

Проверьте, что сервер запущен (в терминале есть «Running on http://...»)
и вы открыли `http://127.0.0.1:5000` (не `file:///...`).

### Ошибка «ModuleNotFoundError»

Убедитесь, что Flask установлен:
```powershell
pip install -r requirements.txt
```

## Логирование

Используется стандартный `logging` с уровнями:

* `INFO` — запуск, HTTP-запросы, создание/обновление/удаление заметок,
  сохранение вложений;
* `WARNING` — пользовательские ошибки (неверный ввод, отсутствующий id),
  восстановление хранилища, пропущенные записи;
* `ERROR` — системные сбои: ошибки чтения/записи файлов, повреждённый JSON,
  непойманные исключения.

```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
```

CLI и веб настраивают логирование автоматически при запуске.

## Тесты

```powershell
pip install -r requirements.txt
python -m pytest tests -v
```

Набор покрывает ядро, драйвер, восстановление JSON, CLI, UI-режим, Flask и
чек-лист краевых случаев (`tests/test_quality.py`). Полный прогон — **99 тестов**.

## Требования

Python 3.10+. CLI-слой использует `colorama` для ANSI-раскраски терминала
(включается автоматически на Windows). Ядро зависит только от стандартной
библиотеки.

## Направление расширения

* новые драйверы хранилища (`BaseDriver`): SQLite, REST;
* сервисный слой поверх ядра: теги, полнотекстовый поиск, категории;
* API (FastAPI) и UI — тонкие обёртки над тем же `NoteManager`.
