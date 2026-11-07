# Пакет драйверов: абстракции доступа к постоянному хранилищу.

from driver.file_driver import BaseDriver, FileDriver, StorageError

__all__ = ["BaseDriver", "FileDriver", "StorageError"]
