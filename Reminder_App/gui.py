import datetime as dt
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional


STATUSES = ["Все", "Ожидает", "Готово", "Просрочено", "Отменено"]


class ReminderGUI:
    def __init__(self, db, notifier) -> None:
        self.db = db
        self.notifier = notifier
        self.notifier.set_status_callback(self.refresh_reminders)
        self.root = tk.Tk()
        self.root.title("Напоминалка")
        self.root.geometry("700x500")
        self.selected_status = tk.StringVar(value=STATUSES[0])
        self.status_bar = tk.StringVar(value="ВСЕГО 0 | Ожидает 0 | Готово 0 | Просрочено 0")
        self._create_widgets()
        self.refresh_reminders()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _create_widgets(self) -> None:
        form_frame = tk.Frame(self.root)
        form_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(form_frame, text="Заголовок").grid(row=0, column=0, sticky="w")
        self.title_entry = tk.Entry(form_frame)
        self.title_entry.grid(row=0, column=1, sticky="ew")

        tk.Label(form_frame, text="Описание").grid(row=1, column=0, sticky="w")
        self.desc_entry = tk.Entry(form_frame)
        self.desc_entry.grid(row=1, column=1, sticky="ew")

        tk.Label(form_frame, text="Дата и время (YYYY-MM-DD HH:MM)").grid(row=2, column=0, sticky="w")
        self.datetime_entry = tk.Entry(form_frame)
        self.datetime_entry.grid(row=2, column=1, sticky="ew")

        button_frame = tk.Frame(form_frame)
        button_frame.grid(row=3, column=0, columnspan=2, pady=5)

        tk.Button(button_frame, text="Добавить", command=self.add_reminder).pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Тест уведомления", command=self.test_notification).pack(side=tk.LEFT, padx=5)

        for minutes in (1, 5, 15, 30, 60):
            tk.Button(button_frame, text=f"+{minutes} мин",
                      command=lambda m=minutes: self.set_quick_time(m)).pack(side=tk.LEFT, padx=2)

        form_frame.columnconfigure(1, weight=1)

        filter_frame = tk.Frame(self.root)
        filter_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(filter_frame, text="Фильтр по статусу").pack(side=tk.LEFT)
        ttk.Combobox(filter_frame, textvariable=self.selected_status,
                     values=STATUSES, state="readonly",
                     width=15).pack(side=tk.LEFT, padx=5)
        tk.Button(filter_frame, text="Обновить", command=self.refresh_reminders).pack(side=tk.LEFT, padx=5)

        tree_frame = tk.Frame(self.root)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ("id", "title", "description", "due_at", "status", "created_at")
        headers = {
            "id": "ID",
            "title": "Заголовок",
            "description": "Описание",
            "due_at": "Дата/Время",
            "status": "Статус",
            "created_at": "Создано",
        }
        widths = {
            "id": 60,
            "title": 180,
            "description": 220,
            "due_at": 140,
            "status": 120,
            "created_at": 140,
        }
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings")
        for col in columns:
            self.tree.heading(col, text=headers.get(col, col))
            self.tree.column(col, width=widths.get(col, 150), anchor=tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self.on_double_click)

        self.status_colors = {
            "Просрочено": "#f8d7da",
            "Готово": "#d4edda",
        }
        for status, color in self.status_colors.items():
            self.tree.tag_configure(status, background=color)

        action_frame = tk.Frame(self.root)
        action_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Button(action_frame, text="Отметить как готово", command=self.mark_as_done).pack(side=tk.LEFT, padx=5)
        tk.Button(action_frame, text="Отменить", command=lambda: self.update_status("Отменено")).pack(side=tk.LEFT, padx=5)
        tk.Button(action_frame, text="Удалить", command=self.delete_reminder).pack(side=tk.LEFT, padx=5)

        status_frame = tk.Frame(self.root)
        status_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(status_frame, textvariable=self.status_bar).pack(side=tk.LEFT)

    def add_reminder(self) -> None:
        title = self.title_entry.get().strip()
        description = self.desc_entry.get().strip()
        due_str = self.datetime_entry.get().strip()
        if not title or not due_str:
            messagebox.showwarning("Ошибка", "Заголовок и дата обязательны")
            return
        try:
            due_dt = dt.datetime.strptime(due_str, "%Y-%m-%d %H:%M")
        except ValueError:
            messagebox.showerror("Ошибка", "Неверный формат даты")
            return
        self.db.add_reminder(title, description, due_dt.strftime("%Y-%m-%d %H:%M"))
        self.refresh_reminders()
        self.title_entry.delete(0, tk.END)
        self.desc_entry.delete(0, tk.END)
        self.datetime_entry.delete(0, tk.END)

    def refresh_reminders(self) -> None:
        self.db.mark_overdue()
        for item in self.tree.get_children():
            self.tree.delete(item)
        status_filter = self.selected_status.get()
        reminders = self.db.get_all_reminders(None if status_filter == "Все" else status_filter)
        for reminder in reminders:
            status = reminder["status"]
            tags = (status,) if status in self.status_colors else ()
            self.tree.insert(
                "",
                tk.END,
                values=(
                    reminder["id"],
                    reminder["title"],
                    reminder["description"],
                    reminder["due_at"],
                    reminder["status"],
                    reminder["created_at"],
                ),
                tags=tags,
            )
        self.update_status_bar()

    def update_status_bar(self) -> None:
        total = self.db.get_reminders_count()
        counts = self.db.get_status_counts()
        summary = (
            f"ВСЕГО {total} | "
            f"Ожидает {counts.get('Ожидает', 0)} | "
            f"Готово {counts.get('Готово', 0)} | "
            f"Просрочено {counts.get('Просрочено', 0)} |"
            f"Отменено {counts.get('Отменено', 0)}"
        )
        self.status_bar.set(summary)

    def get_selected_id(self) -> Optional[int]:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Выбор", "Выберите напоминание")
            return None
        item = self.tree.item(selected[0])
        return int(item["values"][0])

    def mark_as_done(self) -> None:
        reminder_id = self.get_selected_id()
        if reminder_id is None:
            return
        self.db.update_status(reminder_id, "Готово")
        self.refresh_reminders()

    def update_status(self, status: str) -> None:
        reminder_id = self.get_selected_id()
        if reminder_id is None:
            return
        self.db.update_status(reminder_id, status)
        self.refresh_reminders()

    def delete_reminder(self) -> None:
        reminder_id = self.get_selected_id()
        if reminder_id is None:
            return
        if messagebox.askyesno("Удаление", "Удалить напоминание?"):
            self.db.delete_reminder(reminder_id)
            self.refresh_reminders()

    def on_double_click(self, event) -> None:
        item = self.tree.selection()
        if not item:
            return
        reminder_id = int(self.tree.item(item[0])["values"][0])
        reminder = self.db.get_reminder_by_id(reminder_id)
        if reminder:
            messagebox.showinfo(reminder["title"], reminder["description"] or "Без описания")

    def test_notification(self) -> None:
        self.notifier.test_notification()

    def set_quick_time(self, minutes: int) -> None:
        target = dt.datetime.now() + dt.timedelta(minutes=minutes)
        self.datetime_entry.delete(0, tk.END)
        self.datetime_entry.insert(0, target.strftime("%Y-%m-%d %H:%M"))

    def on_closing(self) -> None:
        if messagebox.askokcancel("Выход", "Вы действительно хотите выйти?"):
            self.notifier.stop()
            self.db.close()
            self.root.destroy()

    def run(self) -> None:
        self.notifier.start()
        self.root.mainloop()


__all__ = ["ReminderGUI"]
