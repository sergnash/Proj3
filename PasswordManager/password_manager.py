#!/usr/bin/env python3
"""CLI-хранилище паролей на SQLite с шифрованием Fernet.

Данные размещаются рядом со скриптом:
    passwords.db  - база данных SQLite;
    .key          - ключ шифрования Fernet, завёрнутый в ключ, выведенный из
                    мастер-пароля (scrypt). Без мастер-пароля файл бесполезен.

Запуск без аргументов открывает интерактивное меню: мастер-пароль
запрашивается один раз, после чего команды вызываются по номеру пункта.
После выполнения пункта показывается результат, пароль (если он есть)
копируется в буфер обмена, а возврат в меню выполняется по Enter.
Отдельная команда аргументом (`add`, `get`, ...) сохранена для скриптов
и тестов.

Каталог данных можно переопределить переменной окружения
PASSWORD_MANAGER_DATA_DIR (полезно для тестов или нескольких хранилищ).
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import string
import subprocess
import sys
from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence, TypeVar

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - зависит от окружения пользователя
    print(
        "Ошибка: не установлена библиотека cryptography.\n"
        "Установите зависимости: python -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1)


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(
    os.environ.get("PASSWORD_MANAGER_DATA_DIR", str(APP_DIR))
).expanduser()
DB_PATH = DATA_DIR / "passwords.db"
KEY_PATH = DATA_DIR / ".key"

# Мастер-пароль версии 1 хранился как одиночный SHA-256-хеш. Константа нужна
# только для перевода старых хранилищ на новый формат.
MASTER_HASH_SETTING = "master_password_hash"
MIN_MASTER_LENGTH = 8
MAX_LOGIN_ATTEMPTS = 3

# Формат .key: ключ хранилища завёрнут в KEK, выведенный из мастер-пароля.
KEY_FILE_VERSION = 2
KDF_NAME = "scrypt"
KDF_SALT_BYTES = 16
KDF_DKLEN = 32
KDF_N = 1 << 15
KDF_R = 8
KDF_P = 1
KDF_MAXMEM = 128 * 1024 * 1024

# Пределы для параметров, прочитанных из файла: подсунутый .key с завышенными
# n/r/p не должен съедать память и процессор.
KDF_N_RANGE = (1 << 12, 1 << 16)
KDF_R_RANGE = (1, 32)
KDF_P_RANGE = (1, 8)

# Версия схемы базы: 1 - без updated_at, 2 - с updated_at.
SCHEMA_VERSION = 2

DEFAULT_GENERATED_LENGTH = 16
MIN_GENERATED_LENGTH = 8
MAX_GENERATED_LENGTH = 64

LOWERCASE = string.ascii_lowercase
UPPERCASE = string.ascii_uppercase
DIGITS = string.digits
SYMBOLS = "!@#$%^&*_"

# Порядок групп соответствует нумерации в меню настройки генерации.
GENERATION_GROUPS: tuple[tuple[str, str], ...] = (
    ("Прописные (A-Z)", UPPERCASE),
    ("Строчные (a-z)", LOWERCASE),
    ("Цифры (0-9)", DIGITS),
    ("Специальные (!@#$%^&*_)", SYMBOLS),
)
DEFAULT_GROUPS: tuple[str, ...] = tuple(
    alphabet for _label, alphabet in GENERATION_GROUPS
)

T = TypeVar("T")


@dataclass(frozen=True)
class PasswordOptions:
    """Параметры генерации пароля, выбранные пользователем."""

    length: int = DEFAULT_GENERATED_LENGTH
    groups: tuple[str, ...] = DEFAULT_GROUPS


class VaultError(Exception):
    """Ожидаемая ошибка приложения, которую нужно показать пользователю."""


class AuthError(VaultError):
    """Не удалось пройти аутентификацию."""


# ---------------------------------------------------------------------------
# Шифрование и генерация паролей
# ---------------------------------------------------------------------------


def legacy_sha256_hash(password: str) -> str:
    """SHA-256-хеш мастер-пароля из версии 1. Только для миграции."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def derive_kek(
    password: str,
    salt: bytes,
    *,
    n: int = KDF_N,
    r: int = KDF_R,
    p: int = KDF_P,
) -> bytes:
    """Вывести из мастер-пароля ключ шифрования ключа (KEK) через scrypt."""
    try:
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=KDF_MAXMEM,
            dklen=KDF_DKLEN,
        )
    except (ValueError, MemoryError) as exc:
        raise VaultError(
            "Не удалось вывести ключ из мастер-пароля: недопустимые "
            "параметры KDF."
        ) from exc
    return base64.urlsafe_b64encode(derived)


def generate_password(
    length: int = DEFAULT_GENERATED_LENGTH,
    groups: Sequence[str] | None = None,
) -> str:
    """Сгенерировать криптографически стойкий пароль заданной длины.

    ``groups`` - набор алфавитов (типов символов). Если он не указан,
    используются все четыре типа; пустой набор считается ошибкой. В пароле
    гарантированно есть хотя бы один символ каждого выбранного типа.
    """
    if not MIN_GENERATED_LENGTH <= length <= MAX_GENERATED_LENGTH:
        raise VaultError(
            f"Длина пароля должна быть от {MIN_GENERATED_LENGTH} "
            f"до {MAX_GENERATED_LENGTH} символов."
        )

    selected = tuple(
        group
        for group in (DEFAULT_GROUPS if groups is None else groups)
        if group
    )
    if not selected:
        raise VaultError("Не выбран ни один тип символов.")
    if len(selected) > length:
        raise VaultError(
            "Длина пароля меньше числа выбранных типов символов."
        )

    alphabet = "".join(selected)
    characters = [secrets.choice(group) for group in selected]
    characters.extend(
        secrets.choice(alphabet)
        for _ in range(length - len(characters))
    )
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


# ---------------------------------------------------------------------------
# Буфер обмена
# ---------------------------------------------------------------------------


def _load_pyperclip():
    """Вернуть модуль pyperclip, если он установлен (необязательно)."""
    try:
        import pyperclip  # type: ignore[import-not-found]
    except ImportError:
        return None
    return pyperclip


def clipboard_commands() -> list[list[str]]:
    """Системные утилиты, читающие текст для буфера обмена из stdin."""
    if sys.platform.startswith("win"):
        return [["clip"]]
    if sys.platform == "darwin":
        return [["pbcopy"]]
    return [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]


def copy_to_clipboard(text: str) -> bool:
    """Скопировать текст в системный буфер обмена; True при успехе."""
    module = _load_pyperclip()
    if module is not None:
        try:
            module.copy(text)
            return True
        except Exception:  # pyperclip бросает собственные исключения
            pass

    for command in clipboard_commands():
        executable = shutil.which(command[0])
        if executable is None:
            continue
        try:
            subprocess.run(
                [executable, *command[1:]],
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Файл ключа: ключ хранилища заворачивается в KEK из мастер-пароля
# ---------------------------------------------------------------------------


def is_legacy_key_file(raw: bytes) -> bool:
    """Отличить старый файл с открытым ключом от нового формата (JSON)."""
    text = raw.strip()
    if text.startswith(b"{"):
        return False
    try:
        Fernet(text)
    except (ValueError, TypeError):
        return False
    return True


def wrap_key(key: bytes, password: str) -> dict[str, object]:
    """Завернуть ключ хранилища в ключ, выведенный из мастер-пароля."""
    salt = secrets.token_bytes(KDF_SALT_BYTES)
    kek = derive_kek(password, salt)
    return {
        "version": KEY_FILE_VERSION,
        "kdf": KDF_NAME,
        "n": KDF_N,
        "r": KDF_R,
        "p": KDF_P,
        "salt": base64.b64encode(salt).decode("ascii"),
        "wrapped_key": Fernet(kek).encrypt(key).decode("ascii"),
    }


def _validate_kdf_params(
    payload: dict[str, object],
) -> tuple[bytes, int, int, int, str]:
    """Проверить параметры KDF из файла, вернуть (salt, n, r, p, wrapped)."""

    def read_int(name: str, bounds: tuple[int, int]) -> int:
        value = payload.get(name)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not bounds[0] <= value <= bounds[1]
        ):
            raise VaultError(
                f"Файл ключа {KEY_PATH} повреждён: неверный параметр {name}."
            )
        return value

    n = read_int("n", KDF_N_RANGE)
    r = read_int("r", KDF_R_RANGE)
    p = read_int("p", KDF_P_RANGE)
    if n & (n - 1):
        raise VaultError(
            f"Файл ключа {KEY_PATH} повреждён: n должно быть степенью двойки."
        )

    salt_value = payload.get("salt")
    if not isinstance(salt_value, str):
        raise VaultError(f"Файл ключа {KEY_PATH} повреждён: нет соли.")
    try:
        salt = base64.b64decode(salt_value, validate=True)
    except ValueError as exc:
        raise VaultError(
            f"Файл ключа {KEY_PATH} повреждён: соль не в base64."
        ) from exc
    if len(salt) < 8:
        raise VaultError(
            f"Файл ключа {KEY_PATH} повреждён: слишком короткая соль."
        )

    wrapped = payload.get("wrapped_key")
    if not isinstance(wrapped, str) or not wrapped:
        raise VaultError(
            f"Файл ключа {KEY_PATH} повреждён: нет зашифрованного ключа."
        )

    return salt, n, r, p, wrapped


def unwrap_key(password: str, payload: dict[str, object]) -> bytes | None:
    """Расшифровать ключ хранилища; None, если мастер-пароль неверный."""
    salt, n, r, p, wrapped = _validate_kdf_params(payload)
    kek = derive_kek(password, salt, n=n, r=r, p=p)
    try:
        key = Fernet(kek).decrypt(wrapped.encode("ascii"))
    except InvalidToken:
        return None
    except (ValueError, TypeError) as exc:
        raise VaultError(
            f"Файл ключа {KEY_PATH} повреждён: неверный токен."
        ) from exc

    if len(key) != 44:
        raise VaultError(
            f"Файл ключа {KEY_PATH} повреждён: неверная длина ключа."
        )
    return key


def save_key_file(payload: dict[str, object]) -> None:
    """Атомарно записать .key (права 0600 там, где они поддерживаются)."""
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    temporary = KEY_PATH.with_name(KEY_PATH.name + ".tmp")

    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as key_file:
            key_file.write(data)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, KEY_PATH)
    except OSError as exc:
        with suppress(OSError):
            temporary.unlink()
        raise VaultError(f"Не удалось сохранить ключ в {KEY_PATH}: {exc}") from exc


def create_key_file(password: str) -> bytes:
    """Создать новый ключ хранилища и сохранить его завёрнутым."""
    key = Fernet.generate_key()
    save_key_file(wrap_key(key, password))
    return key


def read_key_file() -> dict[str, object]:
    """Прочитать .key; для старого формата вернуть открытый ключ."""
    try:
        raw = KEY_PATH.read_bytes()
    except OSError as exc:
        raise VaultError(
            f"Не удалось прочитать файл ключа {KEY_PATH}: {exc}"
        ) from exc

    if is_legacy_key_file(raw):
        return {"version": 1, "raw_key": raw.strip()}

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VaultError(
            f"Файл ключа {KEY_PATH} повреждён или имеет неизвестный формат."
        ) from exc

    if not isinstance(payload, dict) or payload.get("version") != KEY_FILE_VERSION:
        raise VaultError(
            f"Файл ключа {KEY_PATH} имеет неподдерживаемую версию формата."
        )
    return payload


def encrypt_password(fernet: Fernet, password: str) -> str:
    """Зашифровать пароль и вернуть токен в виде строки."""
    return fernet.encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_password(fernet: Fernet, token: str) -> str:
    """Расшифровать токен пароля."""
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise VaultError(
            "Не удалось расшифровать пароль. Проверьте, что файл .key "
            "не повреждён и относится к этой базе данных."
        ) from exc


# ---------------------------------------------------------------------------
# Работа с базой данных
# ---------------------------------------------------------------------------


def connect_db() -> sqlite3.Connection:
    """Открыть соединение с SQLite, создав каталог данных при необходимости."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    # Затирать освобождаемые страницы: иначе токен удалённого пароля остаётся
    # в файле базы и извлекается любым hex-редактором.
    connection.execute("PRAGMA secure_delete = ON")
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    """Создать таблицы настроек и записей, если их ещё нет."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            setting_key   TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS entries (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            title              TEXT NOT NULL,
            title_key          TEXT NOT NULL UNIQUE,
            login              TEXT NOT NULL,
            password_encrypted TEXT NOT NULL,
            created_at         TEXT NOT NULL,
            updated_at         TEXT
        );
        """
    )
    migrate_schema(connection)
    connection.commit()


def migrate_schema(connection: sqlite3.Connection) -> None:
    """Довести схему базы, созданной предыдущей версией, до актуальной."""
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(entries)")
    }
    if "updated_at" not in columns:
        connection.execute("ALTER TABLE entries ADD COLUMN updated_at TEXT")
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def get_setting(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT setting_value FROM settings WHERE setting_key = ?",
        (key,),
    ).fetchone()
    return None if row is None else str(row["setting_value"])


def set_setting(
    connection: sqlite3.Connection,
    key: str,
    value: str,
) -> None:
    connection.execute(
        """
        INSERT INTO settings (setting_key, setting_value)
        VALUES (?, ?)
        ON CONFLICT(setting_key) DO UPDATE
        SET setting_value = excluded.setting_value
        """,
        (key, value),
    )
    connection.commit()


def normalize_title(title: str) -> str:
    """Ключ для поиска без учёта регистра (в том числе для кириллицы)."""
    return title.strip().casefold()


def entry_exists(connection: sqlite3.Connection, title: str) -> bool:
    title_key = normalize_title(title)
    row = connection.execute(
        "SELECT 1 FROM entries WHERE title_key = ?",
        (title_key,),
    ).fetchone()
    return row is not None


def count_entries(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) AS count FROM entries").fetchone()
    return int(row["count"])


def add_entry(
    connection: sqlite3.Connection,
    title: str,
    login: str,
    password_encrypted: str,
) -> None:
    title = title.strip()
    login = login.strip()
    title_key = normalize_title(title)
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        connection.execute(
            """
            INSERT INTO entries
                (title, title_key, login, password_encrypted, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, title_key, login, password_encrypted, created_at),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        raise VaultError(
            f"Запись с названием «{title}» уже существует."
        ) from exc


def update_entry(
    connection: sqlite3.Connection,
    entry_id: int,
    *,
    title: str,
    login: str,
    password_encrypted: str,
) -> None:
    """Обновить запись по идентификатору и отметить время изменения."""
    title = title.strip()
    login = login.strip()
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        connection.execute(
            """
            UPDATE entries
            SET title = ?, title_key = ?, login = ?,
                password_encrypted = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                title,
                normalize_title(title),
                login,
                password_encrypted,
                updated_at,
                entry_id,
            ),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        raise VaultError(
            f"Запись с названием «{title}» уже существует."
        ) from exc


def get_entry(
    connection: sqlite3.Connection,
    title: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, title, login, password_encrypted, created_at, updated_at
        FROM entries
        WHERE title_key = ?
        """,
        (normalize_title(title),),
    ).fetchone()


def list_entries(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            "SELECT title, login FROM entries ORDER BY title_key"
        ).fetchall()
    )


def delete_entry(connection: sqlite3.Connection, title: str) -> bool:
    cursor = connection.execute(
        "DELETE FROM entries WHERE title_key = ?",
        (normalize_title(title),),
    )
    connection.commit()
    return cursor.rowcount > 0


def compact_database(connection: sqlite3.Connection) -> None:
    """Переписать файл базы, чтобы освободившиеся страницы не остались на диске."""
    connection.execute("VACUUM")


# ---------------------------------------------------------------------------
# Мастер-пароль и разблокировка хранилища
# ---------------------------------------------------------------------------


def setup_master_password() -> str:
    """Запросить мастер-пароль при первом запуске и вернуть его."""
    print("Первый запуск: создайте мастер-пароль.")
    while True:
        password = getpass.getpass(
            f"Новый мастер-пароль (не короче {MIN_MASTER_LENGTH} символов): "
        )
        if not password:
            print("Пароль не может быть пустым.", file=sys.stderr)
            continue
        if len(password) < MIN_MASTER_LENGTH:
            print(
                f"Пароль должен содержать не менее {MIN_MASTER_LENGTH} символов.",
                file=sys.stderr,
            )
            continue

        confirmation = getpass.getpass("Повторите мастер-пароль: ")
        if password != confirmation:
            print("Пароли не совпадают. Попробуйте ещё раз.", file=sys.stderr)
            continue

        print("Мастер-пароль принят: им будет защищён ключ хранилища.")
        return password


def prompt_master_password(check: Callable[[str], T | None]) -> T:
    """Спрашивать мастер-пароль, пока проверка не вернёт результат."""
    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        password = getpass.getpass("Мастер-пароль: ")
        result = check(password)
        if result is not None:
            print("Вход выполнен.")
            return result

        attempts_left = MAX_LOGIN_ATTEMPTS - attempt
        if attempts_left:
            print(
                f"Неверный пароль. Осталось попыток: {attempts_left}.",
                file=sys.stderr,
            )

    raise AuthError("Неверный мастер-пароль. Доступ запрещён.")


def migrate_legacy_key(
    connection: sqlite3.Connection,
    payload: dict[str, object],
) -> bytes:
    """Перевести хранилище версии 1 (открытый .key + SHA-256) в новый формат."""
    stored_hash = get_setting(connection, MASTER_HASH_SETTING)
    if (
        stored_hash is None
        or len(stored_hash) != 64
        or any(char not in "0123456789abcdef" for char in stored_hash)
    ):
        raise VaultError(
            f"Файл ключа {KEY_PATH} записан в старом формате, а хеш "
            "мастер-пароля в базе отсутствует или повреждён. "
            "Восстановите базу данных из резервной копии."
        )

    raw_key = payload.get("raw_key")
    if not isinstance(raw_key, bytes) or len(raw_key) != 44:
        raise VaultError(f"Файл ключа {KEY_PATH} повреждён.")

    def check(candidate: str) -> str | None:
        if hmac.compare_digest(legacy_sha256_hash(candidate), stored_hash):
            return candidate
        return None

    password = prompt_master_password(check)
    save_key_file(wrap_key(raw_key, password))
    connection.execute(
        "DELETE FROM settings WHERE setting_key = ?",
        (MASTER_HASH_SETTING,),
    )
    connection.commit()
    print("Файл ключа переведён в новый формат и защищён мастер-паролем.")
    return raw_key


def unlock_vault(connection: sqlite3.Connection) -> bytes:
    """Вернуть ключ хранилища: создать, развернуть паролем или мигрировать."""
    if not KEY_PATH.exists():
        if count_entries(connection) > 0:
            raise VaultError(
                f"Файл ключа {KEY_PATH} не найден, но в базе есть записи. "
                "Восстановите .key из резервной копии — без него пароли "
                "расшифровать невозможно."
            )
        password = setup_master_password()
        key = create_key_file(password)
        print(f"Создан ключ шифрования: {KEY_PATH}")
        return key

    payload = read_key_file()
    if payload.get("version") == 1:
        return migrate_legacy_key(connection, payload)

    def check(candidate: str) -> bytes | None:
        return unwrap_key(candidate, payload)

    return prompt_master_password(check)


# ---------------------------------------------------------------------------
# Ввод пользователя и настройка генерации пароля
# ---------------------------------------------------------------------------


def prompt_required(label: str) -> str:
    while True:
        try:
            value = input(label).strip()
        except StopIteration:
            raise EOFError from None
        if value:
            return value
        print("Значение не может быть пустым.", file=sys.stderr)


def read_optional_input(prompt: str) -> str | None:
    """Прочитать строку; None, если ввод закончился (EOF или конец теста)."""
    try:
        return input(prompt).strip()
    except (EOFError, StopIteration):
        print()
        return None


def ask_yes_no(prompt: str, *, default: bool = False) -> bool:
    """Спросить да/нет; пустой ввод или конец ввода даёт ``default``."""
    answer = read_optional_input(prompt)
    if not answer:
        return default
    return answer.casefold() in {"y", "yes", "д", "да"}


PATH_LIKE_PATTERN = re.compile(
    r"(?:[A-Za-z]:\\|\\\\[A-Za-z]|/Users/|/home/)"
)


def looks_like_path(text: str) -> bool:
    """Похож ли фрагмент на путь к файлу или папке."""
    return bool(PATH_LIKE_PATTERN.search(text))


def confirm_manual_password(password: str) -> str:
    """Показать введённый пароль и дать возможность переписать его."""
    while True:
        print(f"Введённый пароль ({len(password)} символов): {password}")
        if looks_like_path(password):
            print(
                "Внимание: в пароле есть фрагмент, похожий на путь "
                "к файлу или папке. Возможно, он вставился из буфера.",
                file=sys.stderr,
            )
            confirmed = ask_yes_no("Сохранить такой пароль? [y/N]: ")
        else:
            confirmed = ask_yes_no(
                "Сохранить пароль? [Y/n]: ",
                default=True,
            )

        if confirmed:
            return password

        new_password = read_optional_input("Пароль (символы видны): ")
        if not new_password:
            raise VaultError("Пароль не может быть пустым.")
        password = new_password


def prompt_length(default: int = DEFAULT_GENERATED_LENGTH) -> int:
    """Спросить длину пароля (8-64) и вернуть её."""
    while True:
        raw = read_optional_input(
            f"Длина пароля ({MIN_GENERATED_LENGTH}-{MAX_GENERATED_LENGTH}, "
            f"Enter — {default}): "
        )
        if raw is None or raw == "":
            return default
        try:
            length = int(raw)
        except ValueError:
            print("Длина должна быть целым числом.", file=sys.stderr)
            continue
        if MIN_GENERATED_LENGTH <= length <= MAX_GENERATED_LENGTH:
            return length
        print(
            f"Длина должна быть от {MIN_GENERATED_LENGTH} "
            f"до {MAX_GENERATED_LENGTH}.",
            file=sys.stderr,
        )


def prompt_groups() -> tuple[str, ...]:
    """Спросить отдельно по каждому типу символов, включать ли его."""
    while True:
        print("Типы символов (Enter — да):")
        selected: list[str] = []
        for label, alphabet in GENERATION_GROUPS:
            if ask_yes_no(f"  {label}? [Y/n]: ", default=True):
                selected.append(alphabet)
        if selected:
            return tuple(selected)
        print(
            "Нужно выбрать хотя бы один тип символов.",
            file=sys.stderr,
        )


def prompt_generate_options() -> PasswordOptions:
    """Предложить пользователю длину и типы символов для генерации."""
    print("Параметры генерации:")
    length = prompt_length()
    groups = prompt_groups()
    return PasswordOptions(length=length, groups=groups)


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------


def command_add(
    connection: sqlite3.Connection,
    fernet: Fernet,
) -> str | None:
    """Добавить запись и вернуть сгенерированный пароль (или None)."""
    title = prompt_required("Название/сервис: ")

    if entry_exists(connection, title):
        raise VaultError(f"Запись с названием «{title}» уже существует.")

    login = prompt_required("Логин: ")
    generated = ask_yes_no("Сгенерировать пароль? [Y/n]: ", default=True)

    if generated:
        options = prompt_generate_options()
        password = generate_password(options.length, options.groups)
    else:
        password = read_optional_input("Пароль (символы видны): ")
        if not password:
            raise VaultError("Пароль не может быть пустым.")
        password = confirm_manual_password(password)

    encrypted = encrypt_password(fernet, password)
    add_entry(connection, title, login, encrypted)
    print(f"Запись «{title}» добавлена.")

    if generated:
        print(f"Сгенерированный пароль: {password}")
        return password
    return None


def command_get(
    connection: sqlite3.Connection,
    fernet: Fernet,
    title: str | None,
) -> str:
    if title is None:
        title = prompt_required("Название/сервис: ")

    entry = get_entry(connection, title)
    if entry is None:
        raise VaultError(f"Запись «{title}» не найдена.")

    password = decrypt_password(fernet, str(entry["password_encrypted"]))
    print(f"Название: {entry['title']}")
    print(f"Логин: {entry['login']}")
    print(f"Пароль: {password}")
    if entry["updated_at"]:
        print(f"Обновлено: {entry['updated_at']}")
    return password


def command_update(
    connection: sqlite3.Connection,
    fernet: Fernet,
    title: str | None,
    generate: bool,
    ask_generate: bool = False,
) -> str | None:
    if title is None:
        title = prompt_required("Название/сервис: ")

    entry = get_entry(connection, title)
    if entry is None:
        raise VaultError(f"Запись «{title}» не найдена.")

    print(f"Название: {entry['title']}")
    print(f"Логин: {entry['login']}")
    print("Оставьте поле пустым, чтобы не менять значение.")

    new_title = input(f"Новое название [{entry['title']}]: ").strip()
    new_login = input(f"Новый логин [{entry['login']}]: ").strip()

    if not new_title:
        new_title = str(entry["title"])
    if not new_login:
        new_login = str(entry["login"])

    if ask_generate and not generate:
        generate = ask_yes_no("Сгенерировать новый пароль? [y/N]: ")

    generated = False
    if generate:
        options = prompt_generate_options()
        password = generate_password(options.length, options.groups)
        generated = True
        encrypted = encrypt_password(fernet, password)
    else:
        password = (
            read_optional_input("Новый пароль (Enter — оставить): ")
            or ""
        )
        if password:
            password = confirm_manual_password(password)
        encrypted = (
            encrypt_password(fernet, password)
            if password
            else str(entry["password_encrypted"])
        )

    if (
        new_title == str(entry["title"])
        and new_login == str(entry["login"])
        and encrypted == str(entry["password_encrypted"])
    ):
        print("Изменений нет.")
        return None

    update_entry(
        connection,
        int(entry["id"]),
        title=new_title,
        login=new_login,
        password_encrypted=encrypted,
    )
    print(f"Запись «{new_title}» обновлена.")

    if generated:
        print(f"Сгенерированный пароль: {password}")
        return password
    return None


def command_list(connection: sqlite3.Connection) -> None:
    entries = list_entries(connection)
    if not entries:
        print("Сохранённых записей нет.")
        return

    print(f"Сохранённые записи ({len(entries)}):")
    for index, entry in enumerate(entries, start=1):
        print(f"{index}. {entry['title']} — {entry['login']}")


def command_delete(
    connection: sqlite3.Connection,
    title: str | None,
    assume_yes: bool,
) -> None:
    if title is None:
        title = prompt_required("Название/сервис: ")

    entry = get_entry(connection, title)
    if entry is None:
        raise VaultError(f"Запись «{title}» не найдена.")

    if not assume_yes:
        if not ask_yes_no(f"Удалить запись «{entry['title']}»? [y/N]: "):
            print("Удаление отменено.")
            return

    if delete_entry(connection, str(entry["title"])):
        try:
            compact_database(connection)
        except sqlite3.Error as exc:
            print(
                f"Не удалось сжать базу данных: {exc}",
                file=sys.stderr,
            )
        print(f"Запись «{entry['title']}» удалена.")
    else:
        raise VaultError(f"Запись «{title}» не найдена.")


def command_new(options: PasswordOptions | None = None) -> str:
    if options is None:
        options = prompt_generate_options()

    password = generate_password(options.length, options.groups)
    print(f"Новый пароль ({options.length} символов):")
    print(password)
    return password


# ---------------------------------------------------------------------------
# Интерактивное меню
# ---------------------------------------------------------------------------

MenuHandler = Callable[[sqlite3.Connection, Fernet], str | None]


def menu_add(connection: sqlite3.Connection, fernet: Fernet) -> str | None:
    return command_add(connection, fernet)


def menu_get(connection: sqlite3.Connection, fernet: Fernet) -> str | None:
    return command_get(connection, fernet, None)


def menu_list(connection: sqlite3.Connection, _fernet: Fernet) -> str | None:
    command_list(connection)
    return None


def menu_update(connection: sqlite3.Connection, fernet: Fernet) -> str | None:
    return command_update(
        connection,
        fernet,
        None,
        generate=False,
        ask_generate=True,
    )


def menu_delete(connection: sqlite3.Connection, _fernet: Fernet) -> str | None:
    command_delete(connection, None, assume_yes=False)
    return None


def menu_new(_connection: sqlite3.Connection, _fernet: Fernet) -> str | None:
    return command_new()


MENU_ACTIONS: tuple[tuple[str, str, MenuHandler | None], ...] = (
    ("1", "Добавить запись", menu_add),
    ("2", "Показать пароль по названию", menu_get),
    ("3", "Список записей", menu_list),
    ("4", "Изменить запись", menu_update),
    ("5", "Удалить запись", menu_delete),
    ("6", "Сгенерировать пароль", menu_new),
    ("0", "Выход", None),
)


def print_menu() -> None:
    print()
    print("=" * 46)
    print(" Менеджер паролей")
    print("=" * 46)
    for number, title, _handler in MENU_ACTIONS:
        print(f" {number}. {title}")
    print()


def pause() -> bool:
    """Подождать Enter перед возвратом в меню; False, если ввод закончился."""
    try:
        input("\nНажмите Enter, чтобы вернуться в меню... ")
    except (EOFError, StopIteration):
        print()
        return False
    return True


def copy_result(password: str) -> None:
    """Показать, удалось ли скопировать пароль в буфер обмена."""
    if copy_to_clipboard(password):
        print("Пароль скопирован в буфер обмена.")
    else:
        print(
            "Не удалось скопировать пароль в буфер обмена: не найдена "
            "подходящая утилита (поставьте пакет pyperclip).",
            file=sys.stderr,
        )


def run_interactive() -> None:
    """Разблокировать хранилище один раз и обслуживать пункты меню."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with closing(connect_db()) as connection:
        init_db(connection)
        fernet = Fernet(unlock_vault(connection))

        while True:
            print_menu()
            try:
                choice = input("Пункт меню: ").strip()
            except (EOFError, KeyboardInterrupt, StopIteration):
                print("\nВыход.")
                return

            handler: MenuHandler | None = None
            known = False
            for number, _title, action in MENU_ACTIONS:
                if number == choice:
                    known = True
                    handler = action
                    break

            if not known:
                print(
                    f"Нет пункта «{choice}». Введите номер от 0 до 6.",
                    file=sys.stderr,
                )
                continue

            if handler is None:
                print("Выход.")
                return

            result: str | None = None
            try:
                result = handler(connection, fernet)
            except VaultError as exc:
                print(f"Ошибка: {exc}", file=sys.stderr)
            except sqlite3.Error as exc:
                print(f"Ошибка базы данных: {exc}", file=sys.stderr)
            except KeyboardInterrupt:
                print("\nОперация отменена.", file=sys.stderr)
            except (EOFError, StopIteration):
                print("\nВыход.")
                return

            if result:
                copy_result(result)

            if not pause():
                return


# ---------------------------------------------------------------------------
# Аргументы и точка входа
# ---------------------------------------------------------------------------


def password_length(value: str) -> int:
    try:
        length = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("длина должна быть целым числом") from exc

    if not MIN_GENERATED_LENGTH <= length <= MAX_GENERATED_LENGTH:
        raise argparse.ArgumentTypeError(
            f"длина должна быть от {MIN_GENERATED_LENGTH} "
            f"до {MAX_GENERATED_LENGTH}"
        )
    return length


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="password_manager.py",
        description=(
            "Локальное CLI-хранилище паролей (SQLite + Fernet). "
            "Без аргументов открывается интерактивное меню."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python password_manager.py            # меню\n"
            "  python password_manager.py add\n"
            "  python password_manager.py get Google\n"
            "  python password_manager.py list\n"
            "  python password_manager.py delete Google\n"
            "  python password_manager.py update Google --generate\n"
            "  python password_manager.py new --length 24\n"
        ),
    )
    parser.set_defaults(handler=None)
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="КОМАНДА",
    )

    add_parser = subparsers.add_parser(
        "add",
        help="добавить новую запись",
        description=(
            "Интерактивно добавить запись. Пароль можно ввести вручную "
            "или сгенерировать с выбором длины и типов символов."
        ),
    )
    add_parser.set_defaults(handler="add")

    get_parser = subparsers.add_parser(
        "get",
        help="показать пароль по названию",
    )
    get_parser.add_argument(
        "title",
        nargs="?",
        help="название записи (если не указано, будет запрошено)",
    )
    get_parser.set_defaults(handler="get")

    list_parser = subparsers.add_parser(
        "list",
        help="показать названия и логины всех записей",
    )
    list_parser.set_defaults(handler="list")

    delete_parser = subparsers.add_parser(
        "delete",
        help="удалить запись по названию",
    )
    delete_parser.add_argument(
        "title",
        nargs="?",
        help="название записи (если не указано, будет запрошено)",
    )
    delete_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="удалить без подтверждения",
    )
    delete_parser.set_defaults(handler="delete")

    update_parser = subparsers.add_parser(
        "update",
        help="изменить название, логин или пароль записи",
        description=(
            "Интерактивно изменить запись. Пустое поле оставляет "
            "значение прежним."
        ),
    )
    update_parser.add_argument(
        "title",
        nargs="?",
        help="название записи (если не указано, будет запрошено)",
    )
    update_parser.add_argument(
        "-g",
        "--generate",
        action="store_true",
        help="сгенерировать новый пароль вместо запроса",
    )
    update_parser.set_defaults(handler="update")

    new_parser = subparsers.add_parser(
        "new",
        help="сгенерировать новый пароль",
        description=(
            "Сгенерировать пароль. Без --length длина и типы символов "
            "запрашиваются интерактивно."
        ),
    )
    new_parser.add_argument(
        "-l",
        "--length",
        type=password_length,
        default=None,
        metavar="N",
        help=(
            f"длина пароля от {MIN_GENERATED_LENGTH} "
            f"до {MAX_GENERATED_LENGTH} (если не указана, будет запрошена)"
        ),
    )
    new_parser.set_defaults(handler="new")

    return parser


def run(args: argparse.Namespace) -> None:
    """Открыть хранилище, разблокировать его мастер-паролем и выполнить команду."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with closing(connect_db()) as connection:
        init_db(connection)
        fernet = Fernet(unlock_vault(connection))

        if args.handler == "add":
            command_add(connection, fernet)
        elif args.handler == "get":
            command_get(connection, fernet, args.title)
        elif args.handler == "list":
            command_list(connection)
        elif args.handler == "delete":
            command_delete(connection, args.title, args.yes)
        elif args.handler == "update":
            command_update(connection, fernet, args.title, args.generate)
        elif args.handler == "new":
            options = (
                None
                if args.length is None
                else PasswordOptions(length=args.length)
            )
            command_new(options)
        else:  # pragma: no cover - защита от ошибки в парсере
            raise VaultError(f"Неизвестная команда: {args.handler}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.handler is None:
            run_interactive()
        else:
            run(args)
    except AuthError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except VaultError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"Ошибка базы данных: {exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nОперация отменена.", file=sys.stderr)
        return 130
    except OSError as exc:
        print(f"Ошибка файловой системы: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
