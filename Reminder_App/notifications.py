import threading
import time
from typing import Callable, Optional

import tkinter as tk
from tkinter import messagebox

try:
    from win10toast import ToastNotifier
except ImportError:  # pragma: no cover
    ToastNotifier = None


class NotificationManager:
    def __init__(self, db, poll_interval: float = 1.0) -> None:
        self.db = db
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.toast = ToastNotifier() if ToastNotifier else None
        self._status_callback: Optional[Callable[[], None]] = None

    def set_status_callback(self, callback: Optional[Callable[[], None]]) -> None:
        self._status_callback = callback

    def _notify_status_change(self) -> None:
        if self._status_callback:
            try:
                self._status_callback()
            except Exception:
                pass

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            due = self.db.get_due_reminders()
            updated = False
            for reminder in due:
                self._show_notification(reminder["title"], reminder["description"])
                self.db.update_status(reminder["id"], "Готово")
                updated = True
            if updated:
                self._notify_status_change()
            time.sleep(self.poll_interval)

    def _show_notification(self, title: str, message: str) -> None:
        if self.toast:
            try:
                self.toast.show_toast(
                    title,
                    message or "Напоминание",
                    icon_path=None,
                    duration=5,
                    threaded=True,
                )
                return
            except Exception:
                pass
        self._show_popup(title, message)

    def _show_popup(self, title: str, message: str) -> None:
        root = tk.Tk()
        root.title(title)
        root.attributes("-topmost", True)
        root.geometry("320x160+100+100")

        label = tk.Label(root, text=message or "Напоминание", wraplength=300)
        label.pack(expand=True, fill=tk.BOTH, padx=12, pady=12)

        root.after(30000, root.destroy)
        root.mainloop()

    def show_manual_notification(self, title: str, message: str) -> None:
        self._show_notification(title, message)

    def test_notification(self) -> None:
        self._show_notification("Тестовое напоминание", "Проверка уведомлений")


__all__ = ["NotificationManager"]
