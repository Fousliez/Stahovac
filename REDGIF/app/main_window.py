from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .redgifs_dl import download_profile_items, existing_gif_ids, scan_profile
from .storage import Storage
from .version import APPLICATION_NAME, BUILD_VERSION


PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
FILTERS = [
    ("Všechny", "all"),
    ("Nové", "new"),
    ("Stažené", "downloaded"),
]


class SortableTableWidgetItem(QTableWidgetItem):
    def __init__(self, text: str, sort_value=None):
        super().__init__(text)
        self.sort_value = (
            text.casefold() if sort_value is None and isinstance(text, str)
            else sort_value
        )

    def __lt__(self, other):
        if isinstance(other, SortableTableWidgetItem):
            try:
                return self.sort_value < other.sort_value
            except TypeError:
                return str(self.sort_value).casefold() < str(other.sort_value).casefold()
        return super().__lt__(other)


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
        profile_url: str,
        items: list[dict],
        destination: str,
        storage: Storage,
        force: bool = False,
    ):
        super().__init__()
        self.username = username
        self.profile_url = profile_url
        self.items = items
        self.destination = destination
        self.storage = storage
        self.force = force

    @Slot()
    def run(self):
        total = len(self.items)
        completed_ids: set[str] = set()
        self.progress.emit(0, total, "")

        def file_finished(gif_id: str):
            key = gif_id.casefold()
            if key in completed_ids:
                return
            completed_ids.add(key)
            self.storage.mark(self.username, gif_id)
            self.progress.emit(len(completed_ids), total, gif_id)

        try:
            download_profile_items(
                self.profile_url,
                self.items,
                self.destination,
                force=self.force,
                progress_callback=file_finished,
            )
        except Exception as exc:
            downloaded = len(completed_ids)
            errors = max(1, total - downloaded)
            self.item_finished.emit("", False, str(exc))
            self.finished.emit(downloaded, errors)
            return

        # Pojistka pro případ, že gallery-dl úspěšně skončí, ale některý
        # dokončený soubor nevyvolá výstupní událost.
        for item in self.items:
            gif_id = str(item.get("id", ""))
            self.storage.mark(self.username, gif_id)

        self.finished.emit(total, 0)


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


class AddProfilesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Přidat RedGIFs profily")
        self.resize(620, 300)

        layout = QVBoxLayout(self)

        info = QLabel("Zadej uživatelské jméno nebo URL. Každý profil má vlastní řádek.")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(6)
        layout.addLayout(self.rows_layout)

        self.profile_edits: list[QLineEdit] = []
        for _ in range(5):
            self.add_row()

        add_row_button = QPushButton("+ Přidat řádek")
        add_row_button.clicked.connect(self.add_row)
        layout.addWidget(add_row_button, 0, Qt.AlignLeft)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Přidat")
        buttons.button(QDialogButtonBox.Cancel).setText("Zrušit")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.profile_edits:
            self.profile_edits[0].setFocus()

    def add_row(self):
        row_number = len(self.profile_edits) + 1
        row = QHBoxLayout()
        label = QLabel(f"Profil {row_number}:")
        label.setMinimumWidth(58)
        edit = QLineEdit()
        edit.setPlaceholderText("uživatelské jméno nebo https://www.redgifs.com/users/…")
        row.addWidget(label)
        row.addWidget(edit, 1)
        self.rows_layout.addLayout(row)
        self.profile_edits.append(edit)

    def values(self) -> list[str]:
        return [
            edit.text().strip()
            for edit in self.profile_edits
            if edit.text().strip()
        ]


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
        self._busy = False
        self._scan_redownload_all = False
        self._pending_redownload_username = ""
        self._pending_redownload_items: list[dict] = []
        self._download_redownload_all = False

        self._batch_mode = False
        self._batch_total_profiles = 0
        self._batch_current_position = 0
        self._batch_download_queue: list[tuple[int, str, list[dict]]] = []
        self._batch_download_results: list[dict] = []

        self._scan_batch_mode = False
        self._scan_batch_total_profiles = 0
        self._scan_batch_current_position = 0
        self._scan_batch_queue: list[tuple[int, str]] = []
        self._scan_batch_results: list[dict] = []

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

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Hledat v profilech…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(320)
        self.search_edit.setMaximumWidth(420)
        self.search_edit.setToolTip("Fulltextové hledání ve všech sloupcích profilů")
        self.search_edit.textChanged.connect(self.filter_profiles)
        top.addWidget(self.search_edit)

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
        self.scan_button = QPushButton("Projít profily")
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

        self.profile_count_label = QLabel("PROFILY: 0")
        self.profile_count_label.setObjectName("sectionTitle")
        layout.addWidget(self.profile_count_label)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["✓", "Jméno", "Profil", "Poslední kontrola", "Nové", "Staženo", "Stav"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(1, Qt.AscendingOrder)
        self.table.itemSelectionChanged.connect(self.refresh_items)
        self.table.itemSelectionChanged.connect(self.update_profile_actions)
        self.table.cellDoubleClicked.connect(self.edit_profile_name)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_profile_context_menu)
        layout.addWidget(self.table, 2)

        items_top = QHBoxLayout()

        self.items_toggle = QToolButton()
        self.items_toggle.setText("REDGIFY")
        self.items_toggle.setObjectName("sectionToggle")
        self.items_toggle.setCheckable(True)
        self.items_toggle.setChecked(False)
        self.items_toggle.setArrowType(Qt.RightArrow)
        self.items_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.items_toggle.clicked.connect(self.toggle_items_section)
        items_top.addWidget(self.items_toggle)

        items_top.addStretch(1)

        self.item_filter_label = QLabel("Zobrazit:")
        self.item_filter_label.hide()
        items_top.addWidget(self.item_filter_label)

        self.item_filter = QComboBox()
        for label, value in FILTERS:
            self.item_filter.addItem(label, value)
        self.item_filter.currentIndexChanged.connect(self.refresh_items)
        self.item_filter.hide()
        items_top.addWidget(self.item_filter)
        layout.addLayout(items_top)

        self.items_table = QTableWidget(0, 3)
        self.items_table.setHorizontalHeaderLabels(["RedGIF ID", "Stav", "Odkaz"])
        self.items_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.items_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.items_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.items_table.verticalHeader().setVisible(False)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.horizontalHeader().setStretchLastSection(True)
        self.items_table.setSortingEnabled(True)
        self.items_table.horizontalHeader().setSortIndicatorShown(True)
        self.items_table.hide()
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

        self.download_progress_bar = QProgressBar()
        self.download_progress_bar.setRange(0, 1)
        self.download_progress_bar.setValue(0)
        self.download_progress_bar.setFormat("Stahování • %p%")
        self.download_progress_bar.hide()
        layout.addWidget(self.download_progress_bar)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar(self))

    def _apply_style(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f2f3f5; color: #1f2328; font-size: 13px; }
            QLabel#title { font-size: 24px; font-weight: 800; color: #18191b; }
            QLabel#subtitle { font-size: 14px; color: #6b7078; margin-left: 8px; }
            QLabel#sectionTitle { font-size: 13px; font-weight: 800; color: #30343a; }
            QToolButton#sectionToggle {
                background: transparent; border: 0; padding: 3px 2px;
                font-size: 13px; font-weight: 800; color: #30343a;
            }
            QToolButton#sectionToggle:hover { color: #111111; }
            QLabel#hint { color: #6b7078; padding: 6px 2px; }
            QPushButton, QComboBox, QLineEdit {
                background: #ffffff; border: 1px solid #c9ccd1; border-radius: 5px;
                padding: 6px 10px; min-height: 20px;
            }
            QPushButton:hover { background: #f8f8f8; border-color: #9da2aa; }
            QPushButton:disabled { color: #969ba3; background: #eceef0; }
            QProgressBar {
                background: #ffffff; border: 1px solid #c9ccd1; border-radius: 5px;
                min-height: 24px; text-align: center; font-weight: 700;
            }
            QProgressBar::chunk { background: #7aa874; border-radius: 4px; }
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

    def toggle_items_section(self, expanded: bool):
        self.items_toggle.setArrowType(
            Qt.DownArrow if expanded else Qt.RightArrow
        )
        self.items_table.setVisible(expanded)
        self.item_filter_label.setVisible(expanded)
        self.item_filter.setVisible(expanded)

        if expanded:
            self.refresh_items()

    def selected_username(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 0)
        return str(item.data(Qt.UserRole) or "") if item else ""

    def selected_usernames(self) -> list[str]:
        selection_model = self.table.selectionModel()
        if selection_model is None:
            username = self.selected_username()
            return [username] if username else []

        usernames: list[str] = []
        rows = sorted(index.row() for index in selection_model.selectedRows())
        for row in rows:
            item = self.table.item(row, 0)
            username = str(item.data(Qt.UserRole) or "") if item else ""
            if username:
                usernames.append(username)

        if not usernames:
            username = self.selected_username()
            if username:
                usernames.append(username)
        return usernames

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

    @staticmethod
    def check_age_state(value: str) -> str:
        if not value:
            return "none"

        try:
            stamp = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return "none"

        age = datetime.now() - stamp
        if timedelta(0) <= age <= timedelta(days=30):
            return "recent"
        if age > timedelta(days=183):
            return "old"
        return "none"

    @staticmethod
    def profile_row_color(age_state: str, checked: bool):
        if age_state == "recent":
            return QColor("#c9e5d0" if checked else "#e6f4ea")
        if age_state == "old":
            return QColor("#f2cbc6" if checked else "#fce8e6")
        if checked:
            return QColor("#d9dde2")
        return None

    def apply_profile_row_color(self, row: int, age_state: str, checked: bool):
        color = self.profile_row_color(age_state, checked)
        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item is None:
                continue
            if color is None:
                item.setData(Qt.BackgroundRole, None)
            else:
                item.setBackground(color)

    def profile_download_dir(self, username: str) -> Path:
        base = self.storage.get_setting(
            "download_dir",
            str(Path.home() / "Stažené" / "RedGIF"),
        )
        return Path(base).expanduser() / username

    def edit_profile_name(self, row: int, column: int):
        if column == 0:
            return
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

    def profile_checkbox_toggled(self, username: str, checked: bool):
        username = str(username or "").strip()
        if not username:
            return

        # Stav checkboxu není součástí žádné řadicí hodnoty. Pouze uložíme
        # značku a přebarvíme aktuální řádek, bez refresh/sort/reload tabulky.
        self.storage.set_profile_checked(username, checked)

        profile = self.storage.profile(username) or {}
        age_state = self.check_age_state(str(profile.get("last_update", "")))

        needle = username.casefold()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            value = str(item.data(Qt.UserRole) or "") if item else ""
            if value.casefold() != needle:
                continue
            self.apply_profile_row_color(row, age_state, checked)
            break

    def update_profile_actions(self):
        self.open_folder_button.setEnabled(bool(self.selected_username()))

    def show_profile_context_menu(self, position):
        item = self.table.itemAt(position)
        if item is None:
            return

        row = item.row()
        self.table.selectRow(row)
        self.table.setCurrentCell(row, 0)

        menu = QMenu(self)
        open_link_action = menu.addAction("Otevřít odkaz")
        redownload_action = menu.addAction("Stáhnout znovu celý profil")
        redownload_action.setEnabled(not self._busy)
        menu.addSeparator()
        delete_action = menu.addAction("Odstranit profil")
        delete_action.setEnabled(not self._busy)
        chosen = menu.exec(self.table.viewport().mapToGlobal(position))

        if chosen == open_link_action:
            self.open_selected_profile_url()
        elif chosen == redownload_action:
            self.redownload_entire_profile()
        elif chosen == delete_action:
            self.delete_selected_profile()

    def open_selected_profile_url(self):
        username = self.selected_username()
        if not username:
            return

        profile = self.storage.profile(username)
        if profile is None:
            return

        url = str(profile.get("url", "")).strip()
        if not url:
            return

        try:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Otevřít odkaz",
                f"Odkaz se nepodařilo otevřít:\n{exc}",
            )

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
        self.profile_count_label.setText(f"PROFILY: {len(profiles)}")
        selected = self.selected_username()

        sorting_enabled = self.table.isSortingEnabled()
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()

        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(profiles))

        for row_index, profile in enumerate(profiles):
            username = str(profile.get("username", ""))
            custom_name = str(profile.get("name", ""))
            items = self.storage.load_scan(username)
            downloaded_count = self.storage.downloaded_count(username, items)
            new_count = len(self.storage.new_items(username, items))
            last_scan = str(profile.get("last_scan", ""))
            last_update = str(profile.get("last_update", ""))

            if not last_scan:
                state = "Nezkontrolováno"
            elif new_count:
                state = f"{new_count} ke stažení"
            else:
                state = "V pořádku"

            checked = bool(profile.get("checked", False))

            # První sloupec má stabilní řadicí hodnotu podle username.
            # Samotný checkbox je cellWidget, takže jeho přepnutí nikdy
            # nevstupuje do třídění tabulky a nemůže změnit pořadí řádků.
            check_item = SortableTableWidgetItem("", username.casefold())
            check_item.setData(Qt.UserRole, username)
            check_item.setFlags(
                (check_item.flags() | Qt.ItemIsEnabled)
                & ~Qt.ItemIsEditable
                & ~Qt.ItemIsSelectable
            )
            self.table.setItem(row_index, 0, check_item)

            check_holder = QWidget(self.table)
            check_holder.setStyleSheet("background: transparent;")
            check_layout = QHBoxLayout(check_holder)
            check_layout.setContentsMargins(0, 0, 0, 0)
            check_layout.setSpacing(0)

            checkbox = QCheckBox(check_holder)
            checkbox.setChecked(checked)
            checkbox.setCursor(Qt.PointingHandCursor)
            checkbox.setStyleSheet("QCheckBox { background: transparent; }")
            checkbox.toggled.connect(
                lambda state, name=username: self.profile_checkbox_toggled(
                    name,
                    bool(state),
                )
            )
            check_layout.addStretch(1)
            check_layout.addWidget(checkbox)
            check_layout.addStretch(1)
            self.table.setCellWidget(row_index, 0, check_holder)

            values = [
                custom_name,
                username,
                self.format_last_scan(last_scan),
                str(new_count),
                str(downloaded_count),
                state,
            ]
            sort_values = [
                custom_name.casefold(),
                username.casefold(),
                last_scan,
                new_count,
                downloaded_count,
                state.casefold(),
            ]

            age_state = self.check_age_state(last_update)
            for column, value in enumerate(values, start=1):
                item = SortableTableWidgetItem(value, sort_values[column - 1])
                item.setData(Qt.UserRole, username)
                if column in {4, 5}:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, column, item)

            self.apply_profile_row_color(row_index, age_state, checked)

        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, 38)
        self.table.setSortingEnabled(sorting_enabled)
        if sorting_enabled and sort_column >= 0:
            self.table.sortItems(sort_column, sort_order)
        self.table.blockSignals(False)

        if selected:
            self.select_profile(selected)
        elif profiles:
            self.table.selectRow(0)
        else:
            self.refresh_items()

        self.filter_profiles()
        self.update_profile_actions()

    def filter_profiles(self):
        needle = self.search_edit.text().strip().casefold()
        visible = 0
        first_visible = -1

        for row in range(self.table.rowCount()):
            values = []
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item is not None:
                    values.append(item.text())

            matches = not needle or needle in " ".join(values).casefold()
            self.table.setRowHidden(row, not matches)

            if matches:
                visible += 1
                if first_visible < 0:
                    first_visible = row

        total = self.table.rowCount()
        if needle:
            self.profile_count_label.setText(f"PROFILY: {visible} / {total}")
        else:
            self.profile_count_label.setText(f"PROFILY: {total}")

        current_row = self.table.currentRow()
        if current_row >= 0 and self.table.isRowHidden(current_row):
            self.table.clearSelection()
            if first_visible >= 0:
                self.table.setCurrentCell(first_visible, 0)
                self.table.selectRow(first_visible)

    def refresh_items(self):
        username = self.selected_username()
        if not username:
            self.items_table.setRowCount(0)
            return

        sorting_enabled = self.items_table.isSortingEnabled()
        sort_column = self.items_table.horizontalHeader().sortIndicatorSection()
        sort_order = self.items_table.horizontalHeader().sortIndicatorOrder()

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

        self.items_table.setSortingEnabled(False)
        self.items_table.setRowCount(len(rows))
        for row_index, (item_data, downloaded) in enumerate(rows):
            gif_id = str(item_data.get("id", ""))
            status = "Stažený" if downloaded else "NOVÝ"
            url = str(item_data.get("url", ""))
            values = [gif_id, status, url]
            sort_values = [
                gif_id.casefold(),
                1 if downloaded else 0,
                url.casefold(),
            ]
            for column, value in enumerate(values):
                item = SortableTableWidgetItem(value, sort_values[column])
                item.setData(Qt.UserRole, gif_id)
                self.items_table.setItem(row_index, column, item)

        self.items_table.resizeColumnToContents(0)
        self.items_table.resizeColumnToContents(1)
        self.items_table.resizeRowsToContents()
        self.items_table.setSortingEnabled(sorting_enabled)
        if sorting_enabled and sort_column >= 0:
            self.items_table.sortItems(sort_column, sort_order)

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
        dialog = AddProfilesDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        added = 0
        invalid: list[str] = []
        for raw in dialog.values():
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
        self._busy = busy

        # Přidání dalšího profilu ani otevření jeho složky nezasahuje do
        # právě probíhající kontroly/stahování, takže tyto akce zůstávají
        # dostupné. Blokujeme jen operace, které mohou měnit aktivní úlohu
        # nebo její nastavení.
        self.add_button.setEnabled(True)
        self.scan_button.setDisabled(busy)
        self.download_button.setDisabled(busy)
        self.delete_button.setDisabled(busy)
        self.settings_button.setDisabled(busy)
        self.update_profile_actions()

    def scan_selected_profile(self):
        usernames = self.selected_usernames()
        if (
            not usernames
            or self.scan_thread is not None
            or self.download_thread is not None
            or self._busy
        ):
            return

        if len(usernames) == 1:
            self._start_scan(usernames[0], redownload_all=False)
            return

        self._scan_batch_mode = True
        self._scan_batch_total_profiles = len(usernames)
        self._scan_batch_current_position = 0
        self._scan_batch_queue = [
            (position, username)
            for position, username in enumerate(usernames, start=1)
        ]
        self._scan_batch_results = []
        self.set_busy(True)

        self.download_progress_bar.setRange(0, max(1, len(usernames)))
        self.download_progress_bar.setValue(0)
        self.download_progress_bar.setFormat("Kontrola profilů • 0/%m • %p%")
        self.download_progress_bar.show()

        self._start_next_batch_scan()

    def _start_next_batch_scan(self):
        if not self._scan_batch_mode or self.scan_thread is not None:
            return

        if not self._scan_batch_queue:
            self._finish_scan_batch()
            return

        position, username = self._scan_batch_queue.pop(0)
        self._scan_batch_current_position = position
        self._start_scan(username, redownload_all=False)

    def _finish_scan_batch(self):
        results = sorted(
            self._scan_batch_results,
            key=lambda result: int(result.get("position", 0)),
        )
        successful = sum(1 for result in results if result.get("status") == "ok")
        failed = sum(1 for result in results if result.get("status") == "error")
        total_new = sum(
            int(result.get("new_count", 0))
            for result in results
            if result.get("status") == "ok"
        )

        failed_lines = [
            f"✗ {result.get('username', '')} — {result.get('message', 'chyba')}"
            for result in results
            if result.get("status") == "error"
        ]

        self.download_progress_bar.setRange(
            0, max(1, self._scan_batch_total_profiles)
        )
        self.download_progress_bar.setValue(self._scan_batch_total_profiles)
        self.download_progress_bar.setFormat("Kontrola dokončena • %p%")
        self.download_progress_bar.show()

        summary = (
            f"Zkontrolováno: {successful}\n"
            f"Chyba: {failed}\n"
            f"Nových RedGIFů celkem: {total_new}"
        )
        if failed_lines:
            summary += "\n\n" + "\n".join(failed_lines[:10])

        self._scan_batch_mode = False
        self._scan_batch_total_profiles = 0
        self._scan_batch_current_position = 0
        self._scan_batch_queue = []
        self._scan_batch_results = []
        self.set_busy(False)

        if failed:
            QMessageBox.warning(
                self,
                "Kontrola profilů dokončena",
                summary,
            )
        else:
            QMessageBox.information(
                self,
                "Kontrola profilů dokončena",
                summary,
            )

    def redownload_entire_profile(self):
        if self._busy:
            return

        username = self.selected_username()
        if not username:
            return

        result = QMessageBox.question(
            self,
            "Stáhnout znovu celý profil",
            f"Stáhnout znovu celý profil {username}?\n\n"
            "Program profil znovu projde a stáhne všechny nalezené RedGIFy "
            "bez ohledu na databázové markery. Existující soubory se přepíšou.\n\n"
            "Pokračovat?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return

        self._start_scan(username, redownload_all=True)

    def _start_scan(self, username: str, redownload_all: bool = False):
        if not username or self.scan_thread is not None or self.download_thread is not None:
            return

        profile = self.storage.profile(username)
        if profile is None:
            return

        self.scanning_username = username
        self._scan_redownload_all = redownload_all
        self._pending_redownload_username = ""
        self._pending_redownload_items = []
        self.set_busy(True)

        if redownload_all:
            self.statusBar().showMessage(
                f"Procházím {username} před úplným stažením…"
            )
        elif self._scan_batch_mode:
            self.statusBar().showMessage(
                f"Procházím profil {self._scan_batch_current_position}/"
                f"{self._scan_batch_total_profiles}: {username}…"
            )
            self.download_progress_bar.setFormat(
                f"Kontrola {self._scan_batch_current_position}/"
                f"{self._scan_batch_total_profiles} • {username} • %p%"
            )
        else:
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
        if new_count == 0:
            self.storage.update_last_update(username, now)

        self.refresh_profiles()
        self.select_profile(username)
        self.refresh_items()

        if self._scan_redownload_all:
            self._pending_redownload_username = username
            self._pending_redownload_items = list(items)
            self.statusBar().showMessage(
                f"Kontrola hotová: {len(items)} nalezených. "
                "Spouštím úplné stažení profilu…"
            )
            return

        if self._scan_batch_mode:
            self._scan_batch_results.append(
                {
                    "position": self._scan_batch_current_position,
                    "username": username,
                    "status": "ok",
                    "found_count": len(items),
                    "new_count": new_count,
                    "recognized": recognized,
                    "message": "",
                }
            )
            self.download_progress_bar.setValue(
                self._scan_batch_current_position
            )
            return

        extra = f", {recognized} už bylo ve složce" if recognized else ""
        self.statusBar().showMessage(
            f"Kontrola hotová: {len(items)} nalezených, {new_count} ke stažení{extra}.",
            5000,
        )

    @Slot(str)
    def _scan_failed(self, message: str):
        if self._scan_batch_mode:
            self._scan_batch_results.append(
                {
                    "position": self._scan_batch_current_position,
                    "username": self.scanning_username,
                    "status": "error",
                    "found_count": 0,
                    "new_count": 0,
                    "recognized": 0,
                    "message": message,
                }
            )
            self.download_progress_bar.setValue(
                self._scan_batch_current_position
            )
            self.statusBar().showMessage(
                f"Kontrola {self.scanning_username} se nepodařila, pokračuji…",
                2500,
            )
            return

        QMessageBox.warning(self, "Kontrola profilu", message)
        self.statusBar().showMessage("Kontrola se nepodařila.", 4000)

    @Slot()
    def _scan_cleanup(self):
        pending_username = self._pending_redownload_username
        pending_items = list(self._pending_redownload_items)
        was_scan_batch = self._scan_batch_mode
        has_more_scan_items = bool(self._scan_batch_queue)

        self.scan_thread = None
        self.scan_worker = None
        self.scanning_username = ""
        self._scan_redownload_all = False
        self._pending_redownload_username = ""
        self._pending_redownload_items = []

        if pending_username and pending_items:
            self._start_download(
                pending_username,
                pending_items,
                force=True,
                redownload_all=True,
            )
            return

        if was_scan_batch:
            if has_more_scan_items:
                QTimer.singleShot(0, self._start_next_batch_scan)
            else:
                QTimer.singleShot(0, self._finish_scan_batch)
            return

        self.set_busy(False)

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
        usernames = self.selected_usernames()
        if (
            not usernames
            or self.download_thread is not None
            or self.scan_thread is not None
            or self._busy
        ):
            return

        self._batch_mode = True
        self._batch_total_profiles = len(usernames)
        self._batch_current_position = 0
        self._batch_download_queue = []
        self._batch_download_results = []

        for position, username in enumerate(usernames, start=1):
            profile = self.storage.profile(username) or {}
            all_items = self.storage.load_scan(username)
            self.sync_existing_files(username, all_items)
            items = self.storage.new_items(username, all_items)

            if items:
                self._batch_download_queue.append((position, username, items))
                continue

            if not str(profile.get("last_scan", "")):
                self._batch_download_results.append(
                    {
                        "position": position,
                        "username": username,
                        "status": "not_scanned",
                        "downloaded": 0,
                        "errors": 0,
                        "message": "",
                    }
                )
            else:
                self._batch_download_results.append(
                    {
                        "position": position,
                        "username": username,
                        "status": "no_new",
                        "downloaded": 0,
                        "errors": 0,
                        "message": "",
                    }
                )

        self.refresh_profiles()
        self.set_busy(True)

        if not self._batch_download_queue:
            self._finish_download_batch()
            return

        self._start_next_batch_download()

    def _start_next_batch_download(self):
        if not self._batch_mode or self.download_thread is not None:
            return

        if not self._batch_download_queue:
            self._finish_download_batch()
            return

        position, username, items = self._batch_download_queue.pop(0)
        self._batch_current_position = position
        self._start_download(username, items)

    def _finish_download_batch(self):
        results = sorted(
            self._batch_download_results,
            key=lambda result: int(result.get("position", 0)),
        )

        completed = sum(1 for result in results if result.get("status") == "ok")
        failed = sum(1 for result in results if result.get("status") == "error")
        no_new = sum(1 for result in results if result.get("status") == "no_new")
        not_scanned = sum(
            1 for result in results if result.get("status") == "not_scanned"
        )

        lines: list[str] = []
        for result in results:
            username = str(result.get("username", ""))
            status = str(result.get("status", ""))
            downloaded = int(result.get("downloaded", 0))
            errors = int(result.get("errors", 0))

            if status == "ok":
                lines.append(f"✓ {username} — dokončeno, staženo: {downloaded}")
            elif status == "error":
                lines.append(
                    f"✗ {username} — nedokončeno, staženo: {downloaded}, chyby: {errors}"
                )
            elif status == "not_scanned":
                lines.append(f"! {username} — profil ještě nebyl zkontrolován")
            else:
                lines.append(f"• {username} — bez nových RedGIFů")

        self.download_progress_bar.setRange(0, max(1, self._batch_total_profiles))
        self.download_progress_bar.setValue(self._batch_total_profiles)
        self.download_progress_bar.setFormat("Dávka dokončena • %p%")
        self.download_progress_bar.show()

        summary = (
            f"Dokončeno: {completed}\n"
            f"Chyba: {failed}\n"
            f"Bez nových: {no_new}\n"
            f"Nezkontrolováno: {not_scanned}\n\n"
            + "\n".join(lines)
        )

        self._batch_mode = False
        self._batch_download_queue = []
        self._batch_download_results = []
        self._batch_total_profiles = 0
        self._batch_current_position = 0
        self.set_busy(False)

        if failed or not_scanned:
            QMessageBox.warning(
                self,
                "Stahování profilů dokončeno",
                summary,
            )
        else:
            QMessageBox.information(
                self,
                "Stahování profilů dokončeno",
                summary,
            )

    def _start_download(
        self,
        username: str,
        items: list[dict],
        force: bool = False,
        redownload_all: bool = False,
    ):
        if not username or not items or self.download_thread is not None:
            return

        destination = str(self.profile_download_dir(username))

        self.downloading_username = username
        self.download_destination = destination
        self._download_errors = []
        self._download_redownload_all = redownload_all
        self.set_busy(True)

        total = len(items)
        action = "Stahuji znovu" if redownload_all else "Stahuji"
        self.download_progress_bar.setRange(0, max(1, total))
        self.download_progress_bar.setValue(0)
        if self._batch_mode:
            self.download_progress_bar.setFormat(
                f"Profil {self._batch_current_position}/{self._batch_total_profiles} "
                f"• {username} • %v/%m • %p%"
            )
        else:
            self.download_progress_bar.setFormat(
                f"{action} %v / %m • %p%"
            )
        self.download_progress_bar.show()

        profile = self.storage.profile(username) or {}
        profile_url = str(
            profile.get("url", f"https://www.redgifs.com/users/{username}")
        )

        thread = QThread(self)
        worker = DownloadWorker(
            username,
            profile_url,
            items,
            destination,
            self.storage,
            force=force,
        )
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
        del gif_id
        action = "Stahuji znovu" if self._download_redownload_all else "Stahuji"
        self.download_progress_bar.setRange(0, max(1, total))
        self.download_progress_bar.setValue(index)
        if self._batch_mode:
            self.download_progress_bar.setFormat(
                f"Profil {self._batch_current_position}/{self._batch_total_profiles} "
                f"• {self.downloading_username} • %v/%m • %p%"
            )
        else:
            self.download_progress_bar.setFormat(
                f"{action} %v / %m • %p%"
            )

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
            if new_count == 0:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.storage.update_last_update(username, now)

        self.refresh_profiles()
        if username:
            self.select_profile(username)
        self.refresh_items()

        total = max(downloaded + errors, self.download_progress_bar.maximum())
        if errors == 0:
            self.download_progress_bar.setValue(self.download_progress_bar.maximum())
            self.download_progress_bar.setFormat(
                "Hotovo %v / %m • %p%"
            )
        else:
            self.download_progress_bar.setRange(0, max(1, total))
            self.download_progress_bar.setValue(downloaded)
            self.download_progress_bar.setFormat(
                "Dokončeno s chybami %v / %m • %p%"
            )

        if self._batch_mode:
            self._batch_download_results.append(
                {
                    "position": self._batch_current_position,
                    "username": username,
                    "status": "ok" if errors == 0 else "error",
                    "downloaded": downloaded,
                    "errors": errors,
                    "message": "\n\n".join(self._download_errors[:5]),
                }
            )
            return

        if errors and self._download_errors:
            QMessageBox.warning(
                self,
                "Některé RedGIFy se nepodařilo stáhnout",
                f"Staženo: {downloaded}. Chyby: {errors}.\n\n"
                + "\n\n".join(self._download_errors[:5]),
            )
        elif errors == 0:
            QMessageBox.information(
                self,
                "Stahování dokončeno",
                f"Stahování bylo dokončeno.\n\nStaženo: {downloaded}",
            )

    @Slot()
    def _download_cleanup(self):
        was_batch = self._batch_mode
        has_more_batch_items = bool(self._batch_download_queue)

        self.download_thread = None
        self.download_worker = None
        self.downloading_username = ""
        self.download_destination = ""
        self._download_errors = []
        self._download_redownload_all = False

        if was_batch:
            # Nezakládej další QThread ani modální QMessageBox přímo uvnitř
            # obsluhy QThread.finished. Nech Qt nejdřív dokončit cleanup
            # právě skončeného threadu a pokračuj v dalším event-loop kroku.
            if has_more_batch_items:
                QTimer.singleShot(0, self._start_next_batch_download)
            else:
                QTimer.singleShot(0, self._finish_download_batch)
            return

        self.set_busy(False)

    def delete_selected_profile(self):
        if self._busy:
            self.statusBar().showMessage(
                "Profil nejde odstranit během probíhající kontroly nebo stahování.",
                3500,
            )
            return

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

    def closeEvent(self, event: QCloseEvent):
        if self.download_thread is None:
            event.accept()
            return

        result = QMessageBox.question(
            self,
            "Probíhá stahování",
            "Právě probíhá stahování. Opravdu chcete program ukončit?\n\n"
            "Právě stahovaný soubor může zůstat nedokončený.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if result == QMessageBox.Yes:
            event.accept()
        else:
            event.ignore()
