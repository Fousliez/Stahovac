from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .redgifs_dl import download_gif, existing_gif_ids, scan_profile
from .storage import Storage
from .version import APPLICATION_NAME, BUILD_VERSION


PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
FILTERS = [
    ("Všechny", "all"),
    ("Nové", "new"),
    ("Stažené", "downloaded"),
]


class ScanWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    @Slot()
    def run(self):
        try:
            self.finished.emit(scan_profile(self.url))
        except Exception as exc:
            self.failed.emit(str(exc))


class DownloadWorker(QObject):
    progress = Signal(int, int, str)
    item_finished = Signal(str, bool, str)
    finished = Signal(int, int)

    def __init__(
        self,
        username: str,
        items: list[dict],
        destination: str,
        storage: Storage,
    ):
        super().__init__()
        self.username = username
        self.items = items
        self.destination = destination
        self.storage = storage

    @Slot()
    def run(self):
        downloaded = 0
        errors = 0
        total = len(self.items)

        for index, item in enumerate(self.items, start=1):
            gif_id = str(item["id"])
            self.progress.emit(index, total, gif_id)
            try:
                download_gif(gif_id, str(item["url"]), self.destination)
                self.storage.mark(self.username, gif_id)
                downloaded += 1
                self.item_finished.emit(gif_id, True, "")
            except Exception as exc:
                errors += 1
                self.item_finished.emit(gif_id, False, str(exc))

        self.finished.emit(downloaded, errors)


class SettingsDialog(QDialog):
    def __init__(self, storage: Storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.setWindowTitle("Nastavení")
        self.resize(680, 130)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        default_dir = str(Path.home() / "Stažené" / "RedGIF")
        dir_row = QHBoxLayout()
        self.directory_edit = QLineEdit(
            self.storage.get_setting("download_dir", default_dir)
        )
        dir_button = QPushButton("Vybrat…")
        dir_button.clicked.connect(self.choose_directory)
        dir_row.addWidget(self.directory_edit, 1)
        dir_row.addWidget(dir_button)
        form.addRow("Složka pro stahování:", dir_row)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_directory(self):
        current = self.directory_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "Vyber složku pro stahování", current
        )
        if path:
            self.directory_edit.setText(path)

    def save(self):
        self.storage.set_setting(
            "download_dir", self.directory_edit.text().strip()
        )
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path):
        super().__init__()
        self.storage = Storage(data_dir)

        self.scan_thread: QThread | None = None
        self.scan_worker: ScanWorker | None = None
        self.download_thread: QThread | None = None
        self.download_worker: DownloadWorker | None = None

        self.scanning_username = ""
        self.downloading_username = ""
        self.download_destination = ""
        self._download_errors: list[str] = []

        self.setWindowTitle(f"{APPLICATION_NAME} {BUILD_VERSION}")
        self.resize(1120, 760)
        self._build_ui()
        self._apply_style()
        self.refresh_profiles()

    def _build_ui(self):
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("STAHOVAČ")
        title.setObjectName("title")
        subtitle = QLabel("RedGIFs archiv")
        subtitle.setObjectName("subtitle")
        top.addWidget(title)
        top.addWidget(subtitle)
        top.addStretch(1)

        top_right = QVBoxLayout()
        top_right.setSpacing(6)

        self.settings_button = QPushButton("Nastavení")
        self.settings_button.clicked.connect(self.open_settings)
        top_right.addWidget(self.settings_button)

        self.open_folder_button = QPushButton("Otevřít složku profilu")
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self.open_selected_profile_folder)
        top_right.addWidget(self.open_folder_button)

        top.addLayout(top_right)
        layout.addLayout(top)

        buttons = QHBoxLayout()
        self.add_button = QPushButton("+ Profily")
        self.scan_button = QPushButton("Projít profil")
        self.download_button = QPushButton("Stáhnout nové")
        self.delete_button = QPushButton("Odstranit profil")

        for button in (
            self.add_button,
            self.scan_button,
            self.download_button,
            self.delete_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.add_button.clicked.connect(self.add_profiles)
        self.scan_button.clicked.connect(self.scan_selected_profile)
        self.download_button.clicked.connect(self.download_new_items)
        self.delete_button.clicked.connect(self.delete_selected_profile)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Jméno", "Profil", "Poslední kontrola", "Nové", "Staženo", "Stav"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 240)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 80)
        self.table.itemSelectionChanged.connect(self.refresh_items)
        self.table.itemSelectionChanged.connect(self.update_profile_actions)
        self.table.cellDoubleClicked.connect(self.edit_profile_name)
        layout.addWidget(self.table, 2)

        items_top = QHBoxLayout()
        items_label = QLabel("REDGIFY")
        items_label.setObjectName("sectionTitle")
        items_top.addWidget(items_label)
        items_top.addStretch(1)
        items_top.addWidget(QLabel("Zobrazit:"))

        self.item_filter = QComboBox()
        for label, value in FILTERS:
            self.item_filter.addItem(label, value)
        self.item_filter.currentIndexChanged.connect(self.refresh_items)
        items_top.addWidget(self.item_filter)
        layout.addLayout(items_top)

        self.items_table = QTableWidget(0, 3)
        self.items_table.setHorizontalHeaderLabels(["RedGIF ID", "Stav", "Odkaz"])
        self.items_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.items_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.items_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.items_table.verticalHeader().setVisible(False)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.setColumnWidth(0, 260)
        self.items_table.setColumnWidth(1, 130)
        self.items_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.items_table, 3)

        hint = QLabel(
            "Stažené RedGIF ID se ukládají do jedné SQLite databáze "
            "REDGIF_MARKERY.db ve zvolené složce pro stahování. ID jsou indexovaná "
            "a zároveň se drží v paměti, takže kontrola zůstává rychlá i při "
            "velkém počtu položek."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar(self))

    def _apply_style(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f2f3f5; color: #1f2328; font-size: 13px; }
            QLabel#title { font-size: 24px; font-weight: 800; color: #18191b; }
            QLabel#subtitle { font-size: 14px; color: #6b7078; margin-left: 8px; }
            QLabel#sectionTitle { font-size: 13px; font-weight: 800; color: #30343a; }
            QLabel#hint { color: #6b7078; padding: 6px 2px; }
            QPushButton, QComboBox, QLineEdit {
                background: #ffffff; border: 1px solid #c9ccd1; border-radius: 5px;
                padding: 6px 10px; min-height: 20px;
            }
            QPushButton:hover { background: #f8f8f8; border-color: #9da2aa; }
            QPushButton:disabled { color: #969ba3; background: #eceef0; }
            QTableWidget {
                background: #ffffff; alternate-background-color: #f8f9fa;
                border: 1px solid #c9ccd1; gridline-color: #e1e3e6;
                selection-background-color: #dfe8f6; selection-color: #111111;
            }
            QHeaderView::section {
                background: #e8eaed; border: 0; border-right: 1px solid #cfd2d6;
                border-bottom: 1px solid #c4c7cc; padding: 7px; font-weight: 700;
            }
            """
        )

    def selected_username(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 0)
        return str(item.data(Qt.UserRole) or "") if item else ""

    @staticmethod
    def format_last_scan(value: str) -> str:
        if not value:
            return "Ještě nezkontrolováno"

        try:
            stamp = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            return f"{stamp.day}/{stamp.month}/{stamp.year}"
        except ValueError:
            match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", value)
            if match:
                return f"{int(match.group(3))}/{int(match.group(2))}/{match.group(1)}"
            return value

    def profile_download_dir(self, username: str) -> Path:
        base = self.storage.get_setting(
            "download_dir",
            str(Path.home() / "Stažené" / "RedGIF"),
        )
        return Path(base).expanduser() / username

    def edit_profile_name(self, row: int, _column: int):
        item = self.table.item(row, 0)
        username = str(item.data(Qt.UserRole) or "") if item else ""
        if not username:
            return

        profile = self.storage.profile(username) or {}
        current_name = str(profile.get("name", ""))

        name, ok = QInputDialog.getText(
            self,
            "Jméno profilu",
            f"Jméno pro profil {username}:",
            QLineEdit.Normal,
            current_name,
        )
        if not ok:
            return

        self.storage.set_profile_name(username, name)
        self.refresh_profiles()
        self.select_profile(username)

    def update_profile_actions(self):
        self.open_folder_button.setEnabled(bool(self.selected_username()))

    def open_selected_profile_folder(self):
        username = self.selected_username()
        if not username:
            self.statusBar().showMessage("Nejdřív vyber profil.", 2500)
            return

        folder = self.profile_download_dir(username)
        folder.mkdir(parents=True, exist_ok=True)

        try:
            subprocess.Popen(
                ["xdg-open", str(folder)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Otevřít složku",
                f"Složku se nepodařilo otevřít:\n{exc}",
            )

    def sync_existing_files(self, username: str, items: list[dict] | None = None) -> int:
        """Převede už existující videa ve složce na markery."""
        if items is None:
            items = self.storage.load_scan(username)

        destination = str(self.profile_download_dir(username))
        found = existing_gif_ids(items, destination)

        created = 0
        for gif_id in found:
            if not self.storage.is_marked(username, gif_id):
                self.storage.mark(username, gif_id)
                created += 1
        return created

    def refresh_profiles(self):
        profiles = self.storage.profiles()
        selected = self.selected_username()
        self.table.blockSignals(True)
        self.table.setRowCount(len(profiles))
        select_row = -1

        for row_index, profile in enumerate(profiles):
            username = str(profile.get("username", ""))
            custom_name = str(profile.get("name", ""))
            items = self.storage.load_scan(username)
            downloaded_count = self.storage.downloaded_count(username, items)
            new_count = len(self.storage.new_items(username, items))
            last_scan = str(profile.get("last_scan", ""))

            if not last_scan:
                state = "Nezkontrolováno"
            elif new_count:
                state = f"{new_count} ke stažení"
            else:
                state = "V pořádku"

            values = [
                custom_name,
                username,
                self.format_last_scan(last_scan),
                str(new_count),
                str(downloaded_count),
                state,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, username)
                if column in {3, 4}:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, column, item)

            if selected.casefold() == username.casefold():
                select_row = row_index

        self.table.blockSignals(False)

        if select_row >= 0:
            self.table.selectRow(select_row)
        elif profiles:
            self.table.selectRow(0)
        else:
            self.refresh_items()

        self.update_profile_actions()

    def refresh_items(self):
        username = self.selected_username()
        if not username:
            self.items_table.setRowCount(0)
            return

        filter_value = self.item_filter.currentData()
        items = self.storage.load_scan(username)
        rows: list[tuple[dict, bool]] = []

        for item in items:
            downloaded = self.storage.is_marked(username, str(item.get("id", "")))
            if filter_value == "new" and downloaded:
                continue
            if filter_value == "downloaded" and not downloaded:
                continue
            rows.append((item, downloaded))

        self.items_table.setRowCount(len(rows))
        for row_index, (item_data, downloaded) in enumerate(rows):
            gif_id = str(item_data.get("id", ""))
            values = [
                gif_id,
                "Stažený" if downloaded else "NOVÝ",
                str(item_data.get("url", "")),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, gif_id)
                self.items_table.setItem(row_index, column, item)

        self.items_table.resizeRowsToContents()

    @staticmethod
    def parse_profile(raw: str) -> tuple[str, str] | None:
        value = raw.strip().rstrip("/")
        if not value:
            return None

        lower = value.lower()
        if "redgifs.com/users/" in lower:
            tail = re.split(r"redgifs\.com/users/", value, maxsplit=1, flags=re.I)[1]
            username = tail.split("/", 1)[0].split("?", 1)[0].strip()
        else:
            username = value.lstrip("@").strip()

        if not PROFILE_RE.fullmatch(username):
            return None

        return username, f"https://www.redgifs.com/users/{username}"

    def add_profiles(self):
        value, ok = QInputDialog.getMultiLineText(
            self,
            "Přidat RedGIFs profily",
            "Uživatelská jména nebo URL profilu, každý na nový řádek:",
        )
        if not ok:
            return

        added = 0
        invalid: list[str] = []
        for raw in value.replace(",", "\n").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            parsed = self.parse_profile(raw)
            if parsed is None:
                invalid.append(raw)
                continue
            username, url = parsed
            if self.storage.profile(username) is None:
                self.storage.add_profile(username, url)
                added += 1

        self.refresh_profiles()
        self.statusBar().showMessage(f"Přidáno profilů: {added}.", 3000)

        if invalid:
            QMessageBox.warning(
                self,
                "Některé profily nebyly přidány",
                "\n".join(invalid[:10]),
            )

    def set_busy(self, busy: bool):
        for button in (
            self.add_button,
            self.scan_button,
            self.download_button,
            self.delete_button,
            self.settings_button,
            self.open_folder_button,
        ):
            button.setDisabled(busy)
        if not busy:
            self.update_profile_actions()

    def scan_selected_profile(self):
        username = self.selected_username()
        if not username or self.scan_thread is not None or self.download_thread is not None:
            return

        profile = self.storage.profile(username)
        if profile is None:
            return

        self.scanning_username = username
        self.set_busy(True)
        self.statusBar().showMessage(f"Procházím {username}…")

        thread = QThread(self)
        worker = ScanWorker(str(profile["url"]))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._scan_finished)
        worker.failed.connect(self._scan_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._scan_cleanup)

        self.scan_thread = thread
        self.scan_worker = worker
        thread.start()

    @Slot(list)
    def _scan_finished(self, items: list):
        username = self.scanning_username
        if not username:
            return

        self.storage.save_scan(username, items)
        recognized = self.sync_existing_files(username, items)
        new_count = len(self.storage.new_items(username, items))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.storage.update_scan_stats(username, now, len(items), new_count)

        self.refresh_profiles()
        self.select_profile(username)
        self.refresh_items()
        extra = f", {recognized} už bylo ve složce" if recognized else ""
        self.statusBar().showMessage(
            f"Kontrola hotová: {len(items)} nalezených, {new_count} ke stažení{extra}.",
            5000,
        )

    @Slot(str)
    def _scan_failed(self, message: str):
        QMessageBox.warning(self, "Kontrola profilu", message)
        self.statusBar().showMessage("Kontrola se nepodařila.", 4000)

    @Slot()
    def _scan_cleanup(self):
        self.set_busy(False)
        self.scan_thread = None
        self.scan_worker = None
        self.scanning_username = ""

    def select_profile(self, username: str):
        needle = username.casefold()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            value = str(item.data(Qt.UserRole) or "") if item else ""
            if value.casefold() == needle:
                self.table.selectRow(row)
                self.table.setCurrentCell(row, 0)
                return

    def download_new_items(self):
        username = self.selected_username()
        if not username or self.download_thread is not None or self.scan_thread is not None:
            return

        all_items = self.storage.load_scan(username)
        recognized = self.sync_existing_files(username, all_items)
        items = self.storage.new_items(username, all_items)
        if recognized:
            self.refresh_profiles()
            self.select_profile(username)
            self.refresh_items()

        if not items:
            message = "Žádné nové RedGIFy ke stažení."
            if recognized:
                message += f" {recognized} souborů už ve složce bylo a bylo označeno jako stažené."
            self.statusBar().showMessage(message, 5000)
            return

        destination = str(self.profile_download_dir(username))

        self.downloading_username = username
        self.download_destination = destination
        self._download_errors = []
        self.set_busy(True)

        thread = QThread(self)
        worker = DownloadWorker(username, items, destination, self.storage)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._download_progress)
        worker.item_finished.connect(self._download_item_finished)
        worker.finished.connect(self._download_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._download_cleanup)

        self.download_thread = thread
        self.download_worker = worker
        thread.start()

    @Slot(int, int, str)
    def _download_progress(self, index: int, total: int, gif_id: str):
        self.statusBar().showMessage(f"Stahuji {index}/{total}: {gif_id}…")

    @Slot(str, bool, str)
    def _download_item_finished(self, _gif_id: str, success: bool, message: str):
        if not success and message:
            self._download_errors.append(message)
        self.refresh_items()

    @Slot(int, int)
    def _download_finished(self, downloaded: int, errors: int):
        username = self.downloading_username
        if username:
            items = self.storage.load_scan(username)
            new_count = len(self.storage.new_items(username, items))
            profile = self.storage.profile(username)
            last_scan = str(profile.get("last_scan", "")) if profile else ""
            self.storage.update_scan_stats(username, last_scan, len(items), new_count)

        self.refresh_profiles()
        if username:
            self.select_profile(username)
        self.refresh_items()

        self.statusBar().showMessage(
            f"Staženo: {downloaded}. Chyby: {errors}. Složka: {self.download_destination}",
            7000,
        )

        if errors and self._download_errors:
            QMessageBox.warning(
                self,
                "Některé RedGIFy se nepodařilo stáhnout",
                "\n\n".join(self._download_errors[:5]),
            )

    @Slot()
    def _download_cleanup(self):
        self.set_busy(False)
        self.download_thread = None
        self.download_worker = None
        self.downloading_username = ""
        self.download_destination = ""
        self._download_errors = []

    def delete_selected_profile(self):
        username = self.selected_username()
        if not username:
            return

        result = QMessageBox.question(
            self,
            "Odstranit profil",
            f"Odstranit profil {username} ze seznamu?\n\n"
            "Stažená videa ani markery se nesmažou.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return

        self.storage.delete_profile(username)
        self.refresh_profiles()
        self.statusBar().showMessage(
            "Profil odstraněn. Videa i markery zůstaly.", 3000
        )

    def open_settings(self):
        dialog = SettingsDialog(self.storage, self)
        if dialog.exec() == QDialog.Accepted:
            self.statusBar().showMessage("Nastavení uloženo.", 2500)
