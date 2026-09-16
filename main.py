from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.bulk_profiles import install_bulk_profile_add
from app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Stahovač")
    install_bulk_profile_add(MainWindow)
    data_dir = Path(__file__).resolve().parent / "data"
    window = MainWindow(data_dir)
    window.add_button.setText("+ Profily")
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
