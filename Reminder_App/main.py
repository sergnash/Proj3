from database import ReminderDatabase
from gui import ReminderGUI
from notifications import NotificationManager


def main() -> None:
    db = ReminderDatabase()
    notifier = NotificationManager(db)
    gui = ReminderGUI(db, notifier)
    gui.run()


if __name__ == "__main__":
    main()
