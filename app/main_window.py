from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
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

from .database import Database
from .gallery_dl import GalleryDLError, download_post, scan_profile
from .version import APPLICATION_NAME, BUILD_VERSION


PROFILE_RE = re.compile(r"^[A-Za-z0-9._]+$")
STATUS_LABELS = {
    "new": "NOVÝ",
    "known": "Známý",
    "downloaded": "Stažený",
    "ignored": "Ignorovaný",
    "error": "Chyba",
}
FILTERS = [
    ("Všechny", None),
    ("Nové", "new"),
    ("Stažené", "downloaded"),
    ("Známé", "known"),
    ("Ignorované", "ignored"),
    ("Chyby", "error"),
]


class ScanWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, url: str, cookies_file: str):
        super().__init__()
        self.url = url
        self.cookies_file = cookies_file

    @Slot()
    def run(self):
        try:
            self.finished.emit(scan_profile(self.url, self.cookies_file))
        except Exception as exc:
            self.failed.emit(str(exc))


class DownloadWorker(QObject):
    progress = Signal(int, int, int, str)
    post_finished = Signal(int, bool, str)
    finished = Signal(int, int)

    def __init__(self, posts: list[dict], destination: str, cookies_file: str):
        super().__init__()
        self.posts = posts
        self.destination = destination
        self.cookies_file = cookies_file

    @Slot()
    def run(self):
        downloaded = 0
        errors = 0
        total = len(self.posts)
        for index, post in enumerate(self.posts, start=1):
            post_id = int(post["id"])
            shortcode = str(post["shortcode"])
            self.progress.emit(index, total, post_id, shortcode)
            try:
                download_post(str(post["post_url"]), self.destination, self.cookies_file)
                downloaded += 1
                self.post_finished.emit(post_id, True, "")
            except Exception as exc:
                errors += 1
                self.post_finished.emit(post_id, False, str(exc))
        self.finished.emit(downloaded, errors)


class SettingsDialog(QDialog):
    def __init__(self, database: Database, parent=None):
        super().__init__(parent)
        self.db = database
        self.setWindowTitle("Nastavení")
        self.resize(680, 160)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        cookie_row = QHBoxLayout()
        self.cookies_edit = QLineEdit(self.db.get_setting("cookies_file"))
        cookie_button = QPushButton("Vybrat…")
        cookie_button.clicked.connect(self.choose_cookies)
        cookie_row.addWidget(self.cookies_edit, 1)
        cookie_row.addWidget(cookie_button)
        form.addRow("cookies.txt:", cookie_row)

        default_dir = str(Path.home() / "Stažené" / "Instagram")
        dir_row = QHBoxLayout()
        self.directory_edit = QLineEdit(self.db.get_setting("download_dir", default_dir))
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

    def choose_cookies(self):
        current = self.cookies_edit.text().strip() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Vyber cookies.txt",
            current,
            "Cookies (*.txt);;Všechny soubory (*)",
        )
        if path:
            self.cookies_edit.setText(path)

    def choose_directory(self):
        current = self.directory_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Vyber složku pro stahování", current)
        if path:
            self.directory_edit.setText(path)

    def save(self):
        self.db.set_setting("cookies_file", self.cookies_edit.text().strip())
        self.db.set_setting("download_dir", self.directory_edit.text().strip())
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path):
        super().__init__()
        self.data_dir = data_dir
        self.db = Database(data_dir / "stahovac.sqlite3")
        self.scan_thread: QThread | None = None
        self.scan_worker: ScanWorker | None = None
        self.download_thread: QThread | None = None
        self.download_worker: DownloadWorker | None = None
        self.scanning_profile_id: int | None = None
        self.downloading_profile_id: int | None = None
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
        subtitle = QLabel("Instagram archiv")
        subtitle.setObjectName("subtitle")
        top.addWidget(title)
        top.addWidget(subtitle)
        top.addStretch(1)
        self.settings_button = QPushButton("Nastavení")
        self.settings_button.clicked.connect(self.open_settings)
        top.addWidget(self.settings_button)
        layout.addLayout(top)

        buttons = QHBoxLayout()
        self.add_button = QPushButton("+ Profil")
        self.scan_button = QPushButton("Projít profil")
        self.download_button = QPushButton("Stáhnout nové")
        self.known_button = QPushButton("Nové → známé")
        self.delete_button = QPushButton("Odstranit profil")
        for button in (
            self.add_button,
            self.scan_button,
            self.download_button,
            self.known_button,
            self.delete_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.add_button.clicked.connect(self.add_profile)
        self.scan_button.clicked.connect(self.scan_selected_profile)
        self.download_button.clicked.connect(self.download_new_posts)
        self.known_button.clicked.connect(self.mark_selected_known)
        self.delete_button.clicked.connect(self.delete_selected_profile)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Profil", "Poslední kontrola", "Nové", "Celkem", "Stav"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 270)
        self.table.setColumnWidth(1, 185)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 80)
        self.table.itemSelectionChanged.connect(self.refresh_posts)
        layout.addWidget(self.table, 2)

        posts_top = QHBoxLayout()
        posts_label = QLabel("PŘÍSPĚVKY")
        posts_label.setObjectName("sectionTitle")
        posts_top.addWidget(posts_label)
        posts_top.addStretch(1)
        posts_top.addWidget(QLabel("Zobrazit:"))
        self.post_filter = QComboBox()
        for label, value in FILTERS:
            self.post_filter.addItem(label, value)
        self.post_filter.currentIndexChanged.connect(self.refresh_posts)
        posts_top.addWidget(self.post_filter)
        layout.addLayout(posts_top)

        post_buttons = QHBoxLayout()
        self.open_post_button = QPushButton("Otevřít post")
        self.ignore_post_button = QPushButton("Ignorovat vybrané")
        self.known_post_button = QPushButton("Označit jako známé")
        post_buttons.addWidget(self.open_post_button)
        post_buttons.addWidget(self.ignore_post_button)
        post_buttons.addWidget(self.known_post_button)
        post_buttons.addStretch(1)
        layout.addLayout(post_buttons)

        self.open_post_button.clicked.connect(self.open_selected_post)
        self.ignore_post_button.clicked.connect(lambda: self.set_selected_posts_status("ignored"))
        self.known_post_button.clicked.connect(lambda: self.set_selected_posts_status("known"))

        self.posts_table = QTableWidget(0, 4)
        self.posts_table.setHorizontalHeaderLabels(["Post", "Nalezeno", "Stav", "Odkaz"])
        self.posts_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.posts_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.posts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.posts_table.verticalHeader().setVisible(False)
        self.posts_table.setAlternatingRowColors(True)
        self.posts_table.setColumnWidth(0, 190)
        self.posts_table.setColumnWidth(1, 180)
        self.posts_table.setColumnWidth(2, 120)
        self.posts_table.horizontalHeader().setStretchLastSection(True)
        self.posts_table.cellDoubleClicked.connect(lambda *_args: self.open_selected_post())
        layout.addWidget(self.posts_table, 3)

        hint = QLabel(
            "První průchod vytvoří výchozí stav. Další průchody ukážou jen nově objevené posty. "
            "Když později smažeš stažený soubor z disku, post zůstává v evidenci jako zpracovaný."
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

    def selected_profile_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        return int(value) if value is not None else None

    def selected_post_ids(self) -> list[int]:
        rows = sorted({index.row() for index in self.posts_table.selectedIndexes()})
        result: list[int] = []
        for row in rows:
            item = self.posts_table.item(row, 0)
            if item is None:
                continue
            value = item.data(Qt.UserRole)
            if value is not None:
                result.append(int(value))
        return result

    def refresh_profiles(self):
        rows = list(self.db.profiles())
        selected_id = self.selected_profile_id()
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        select_row = -1
        for row_index, profile in enumerate(rows):
            new_count = int(profile["new_posts"] or 0)
            total_count = int(profile["total_posts"] or 0)
            state = "Nové příspěvky" if new_count else "V pořádku"
            values = [
                profile["username"],
                profile["last_scan_at"] or "Ještě nezkontrolováno",
                str(new_count),
                str(total_count),
                state,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, int(profile["id"]))
                if column in {2, 3}:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, column, item)
            if selected_id == int(profile["id"]):
                select_row = row_index
        self.table.blockSignals(False)
        if select_row >= 0:
            self.table.selectRow(select_row)
        elif rows:
            self.table.selectRow(0)
        else:
            self.refresh_posts()

    def refresh_posts(self):
        profile_id = self.selected_profile_id()
        if profile_id is None:
            self.posts_table.setRowCount(0)
            return
        status = self.post_filter.currentData()
        posts = list(self.db.posts_for_profile(profile_id, status))
        self.posts_table.setRowCount(len(posts))
        for row_index, post in enumerate(posts):
            values = [
                post["shortcode"],
                post["discovered_at"],
                STATUS_LABELS.get(str(post["status"]), str(post["status"])),
                post["post_url"],
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, int(post["id"]))
                self.posts_table.setItem(row_index, column, item)
        self.posts_table.resizeRowsToContents()

    def add_profile(self):
        value, ok = QInputDialog.getText(
            self,
            "Přidat Instagram profil",
            "Uživatelské jméno nebo URL profilu:",
        )
        if not ok:
            return
        raw = value.strip().rstrip("/")
        if not raw:
            return
        if "instagram.com/" in raw.lower():
            username = raw.split("instagram.com/", 1)[1].split("/", 1)[0].strip()
        else:
            username = raw.lstrip("@").strip()
        if not PROFILE_RE.match(username):
            QMessageBox.warning(self, "Profil", "Tohle nevypadá jako platné Instagram jméno.")
            return
        url = f"https://www.instagram.com/{username}/"
        try:
            profile_id = self.db.add_profile(username, url)
        except Exception as exc:
            QMessageBox.warning(self, "Profil", f"Profil se nepodařilo přidat:\n{exc}")
            return
        self.refresh_profiles()
        self.select_profile(profile_id)
        self.statusBar().showMessage(f"Přidán profil @{username}", 2500)

    def select_profile(self, profile_id: int):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and int(item.data(Qt.UserRole)) == int(profile_id):
                self.table.selectRow(row)
                self.table.setCurrentCell(row, 0)
                return

    def set_busy(self, busy: bool):
        for button in (
            self.add_button,
            self.scan_button,
            self.download_button,
            self.known_button,
            self.delete_button,
            self.settings_button,
        ):
            button.setDisabled(busy)

    def scan_selected_profile(self):
        profile_id = self.selected_profile_id()
        if profile_id is None or self.scan_thread is not None or self.download_thread is not None:
            return
        profile = self.db.profile(profile_id)
        if profile is None:
            return
        self.scanning_profile_id = profile_id
        self.set_busy(True)
        self.statusBar().showMessage(f"Procházím @{profile['username']}…")

        thread = QThread(self)
        worker = ScanWorker(profile["url"], self.db.get_setting("cookies_file"))
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
    def _scan_finished(self, posts: list):
        profile_id = self.scanning_profile_id
        if profile_id is None:
            return
        profile = self.db.profile(profile_id)
        first_scan = bool(profile is not None and not profile["first_scan_done"])
        total, new_count = self.db.register_scan(profile_id, posts)
        self.refresh_profiles()
        self.select_profile(profile_id)
        self.refresh_posts()
        if first_scan:
            self.statusBar().showMessage(
                f"První průchod: uloženo {total} existujících postů jako známé.",
                5000,
            )
        else:
            self.statusBar().showMessage(
                f"Kontrola hotová: {total} nalezených, {new_count} nových.",
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
        self.scanning_profile_id = None

    def mark_selected_known(self):
        profile_id = self.selected_profile_id()
        if profile_id is None:
            return
        self.db.mark_all_known(profile_id)
        self.refresh_profiles()
        self.select_profile(profile_id)
        self.refresh_posts()
        self.statusBar().showMessage("Nové posty označeny jako známé.", 2500)

    def set_selected_posts_status(self, status: str):
        post_ids = self.selected_post_ids()
        if not post_ids:
            self.statusBar().showMessage("Nejdřív vyber příspěvek.", 2200)
            return
        self.db.set_posts_status(post_ids, status)
        profile_id = self.selected_profile_id()
        self.refresh_profiles()
        if profile_id is not None:
            self.select_profile(profile_id)
        self.refresh_posts()
        self.statusBar().showMessage(
            f"Upraveno příspěvků: {len(post_ids)}.",
            2200,
        )

    def download_new_posts(self):
        profile_id = self.selected_profile_id()
        if profile_id is None or self.download_thread is not None or self.scan_thread is not None:
            return
        profile = self.db.profile(profile_id)
        posts = [dict(row) for row in self.db.posts_for_profile(profile_id, "new")]
        if profile is None or not posts:
            self.statusBar().showMessage("Žádné nové posty ke stažení.", 2500)
            return

        base = self.db.get_setting("download_dir", str(Path.home() / "Stažené" / "Instagram"))
        destination = str(Path(base).expanduser() / profile["username"])
        cookies = self.db.get_setting("cookies_file")
        self.downloading_profile_id = profile_id
        self.download_destination = destination
        self._download_errors = []
        self.set_busy(True)

        thread = QThread(self)
        worker = DownloadWorker(posts, destination, cookies)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._download_progress)
        worker.post_finished.connect(self._download_post_finished)
        worker.finished.connect(self._download_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._download_cleanup)
        self.download_thread = thread
        self.download_worker = worker
        thread.start()

    @Slot(int, int, int, str)
    def _download_progress(self, index: int, total: int, _post_id: int, shortcode: str):
        self.statusBar().showMessage(f"Stahuji {index}/{total}: {shortcode}…")

    @Slot(int, bool, str)
    def _download_post_finished(self, post_id: int, success: bool, message: str):
        self.db.set_post_status(post_id, "downloaded" if success else "error")
        if message:
            self._download_errors.append(message)

    @Slot(int, int)
    def _download_finished(self, downloaded: int, errors: int):
        profile_id = self.downloading_profile_id
        self.refresh_profiles()
        if profile_id is not None:
            self.select_profile(profile_id)
        self.refresh_posts()
        self.statusBar().showMessage(
            f"Staženo: {downloaded}. Chyby: {errors}. Složka: {self.download_destination}",
            7000,
        )
        if errors and self._download_errors:
            QMessageBox.warning(
                self,
                "Některé posty se nepodařilo stáhnout",
                "\n\n".join(self._download_errors[:5]),
            )

    @Slot()
    def _download_cleanup(self):
        self.set_busy(False)
        self.download_thread = None
        self.download_worker = None
        self.downloading_profile_id = None
        self.download_destination = ""
        self._download_errors = []

    def open_selected_post(self):
        post_ids = self.selected_post_ids()
        if not post_ids:
            return
        post = self.db.post(post_ids[0])
        if post is not None:
            QDesktopServices.openUrl(QUrl(str(post["post_url"])))

    def delete_selected_profile(self):
        profile_id = self.selected_profile_id()
        if profile_id is None:
            return
        profile = self.db.profile(profile_id)
        if profile is None:
            return
        result = QMessageBox.question(
            self,
            "Odstranit profil",
            f"Odstranit @{profile['username']} a jeho evidenci z programu?\n\n"
            "Stažené soubory na disku se nesmažou.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
        self.db.delete_profile(profile_id)
        self.refresh_profiles()
        self.statusBar().showMessage("Profil odstraněn. Soubory na disku zůstaly.", 3000)

    def open_settings(self):
        dialog = SettingsDialog(self.db, self)
        if dialog.exec() == QDialog.Accepted:
            self.statusBar().showMessage("Nastavení uloženo.", 2500)
