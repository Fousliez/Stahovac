from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .downloader import (
    DownloadCancelled,
    DownloadControl,
    download_url,
    resolve_reference_cutoff,
    scan_source_with_identity,
)
from .storage import Storage
from .version import APPLICATION_NAME, BUILD_VERSION


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


class HoverRowDelegate(QStyledItemDelegate):
    """Propaguje hover stav na všechny buňky řádku, ne jen na jednu buňku."""

    def paint(self, painter, option, index):
        table = self.parent()
        if (
            isinstance(table, HoverRowTableWidget)
            and table.hover_row == index.row()
        ):
            option = QStyleOptionViewItem(option)
            option.state |= QStyle.State_MouseOver
        super().paint(painter, option, index)


class HoverRowTableWidget(QTableWidget):
    def __init__(self, rows: int, columns: int, parent=None):
        super().__init__(rows, columns, parent)
        self.hover_row = -1
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setItemDelegate(HoverRowDelegate(self))

    def mouseMoveEvent(self, event):
        row = self.indexAt(event.position().toPoint()).row()
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.hover_row != -1:
            self.hover_row = -1
            self.viewport().update()
        super().leaveEvent(event)


class DownloadWorker(QObject):
    item_started = Signal(str, int, int)
    item_progress = Signal(str, int, str, int, int, int, int)
    item_finished = Signal(str, bool, str, str)
    item_cancelled = Signal(str)
    video_downloaded = Signal(dict)
    failed = Signal(str)
    finished = Signal(int, int, bool)

    def __init__(
        self,
        urls: list[str],
        destination: str,
        archive_file: str,
        cookies_file: str,
        reference_url: str,
    ):
        super().__init__()
        self.urls = urls
        self.destination = destination
        self.archive_file = archive_file
        self.cookies_file = cookies_file
        self.reference_url = reference_url
        self.control = DownloadControl()

    def pause(self) -> bool:
        return self.control.pause()

    def resume(self) -> bool:
        return self.control.resume()

    def cancel(self) -> bool:
        return self.control.cancel()

    @Slot()
    def run(self):
        ok_count = 0
        error_count = 0
        total = len(self.urls)

        try:
            reference_timestamp, reference_date_after = resolve_reference_cutoff(
                self.reference_url,
                self.cookies_file,
                self.control,
            )
        except DownloadCancelled:
            self.finished.emit(0, 0, True)
            return
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        cancelled = False
        for index, url in enumerate(self.urls, start=1):
            if self.control.cancelled:
                cancelled = True
                break
            self.item_started.emit(url, index, total)

            def progress(
                percent: int,
                status: str,
                title: str,
                video_index: int,
                video_total: int,
            ):
                label = "Stahuji"
                if status == "finished":
                    label = "Dokončuji"
                self.item_progress.emit(
                    url,
                    percent,
                    title or label,
                    index,
                    total,
                    video_index,
                    video_total,
                )

            try:
                title = download_url(
                    url,
                    self.destination,
                    self.archive_file,
                    cookies_file=self.cookies_file,
                    progress_callback=progress,
                    completed_callback=self.video_downloaded.emit,
                    reference_timestamp=reference_timestamp,
                    reference_date_after=reference_date_after,
                    control=self.control,
                )
            except DownloadCancelled:
                cancelled = True
                self.item_cancelled.emit(url)
                break
            except Exception as exc:
                error_count += 1
                self.item_finished.emit(url, False, str(exc), "")
            else:
                ok_count += 1
                self.item_finished.emit(url, True, "", title)

        self.finished.emit(ok_count, error_count, cancelled)


class ScanSourcesWorker(QObject):
    source_started = Signal(str, int, int)
    source_finished = Signal(str, list, dict, int, int)
    source_failed = Signal(str, str, int, int)
    finished = Signal()

    def __init__(
        self,
        urls: list[str],
        cookies_file: str,
        source_meta: dict[str, dict],
    ):
        super().__init__()
        self.urls = urls
        self.cookies_file = cookies_file
        self.source_meta = source_meta

    @Slot()
    def run(self):
        total = len(self.urls)
        for index, url in enumerate(self.urls, start=1):
            self.source_started.emit(url, index, total)
            try:
                items, identity = scan_source_with_identity(
                    url,
                    self.cookies_file,
                    self.source_meta.get(url, {}),
                )
            except Exception as exc:
                self.source_failed.emit(url, str(exc), index, total)
            else:
                self.source_finished.emit(url, items, identity, index, total)
        self.finished.emit()


class BaselineScanWorker(QObject):
    finished = Signal(list, dict)
    failed = Signal(str)

    def __init__(self, url: str, cookies_file: str, source_meta: dict):
        super().__init__()
        self.url = url
        self.cookies_file = cookies_file
        self.source_meta = source_meta

    @Slot()
    def run(self):
        try:
            items, identity = scan_source_with_identity(
                self.url,
                self.cookies_file,
                self.source_meta,
            )
            self.finished.emit(items, identity)
        except Exception as exc:
            self.failed.emit(str(exc))


class MarkCurrentDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nastavit jako aktuální")
        self.resize(580, 250)

        layout = QVBoxLayout(self)
        info = QLabel(
            "Program projde celý současný obsah profilu nebo seznamu.\n\n"
            "Nic se nebude stahovat. Všechna nynější videa budou uložena "
            "jako známý obsah a při dalším stahování se přeskočí. "
            "V databázi stažených videí ale jako stažená označena nebudou."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.confirm = QCheckBox(
            "Rozumím, že současný obsah bude přeskočen."
        )
        layout.addWidget(self.confirm)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok_button = buttons.addButton(
            "Nastavit aktuální",
            QDialogButtonBox.AcceptRole,
        )
        self.ok_button.setEnabled(False)
        self.confirm.toggled.connect(self.ok_button.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AddUrlsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Přidat odkazy")
        self.resize(680, 360)

        layout = QVBoxLayout(self)
        info = QLabel(
            "Vlož odkazy na videa, profily nebo seznamy. Každý odkaz dej na samostatný řádek."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.edit = QTextEdit()
        self.edit.setPlaceholderText("https://www.pornhub.com/view_video.php?viewkey=…")
        layout.addWidget(self.edit, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Přidat")
        buttons.button(QDialogButtonBox.Cancel).setText("Zrušit")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def urls(self) -> list[str]:
        return [line.strip() for line in self.edit.toPlainText().splitlines() if line.strip()]


class NewerThanDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stáhnout novější videa")
        self.resize(650, 180)

        layout = QVBoxLayout(self)
        info = QLabel(
            "Vlož odkaz na referenční video. Z vybraného profilu nebo seznamu "
            "se stáhnou jen videa zveřejněná po něm."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.reference_edit = QLineEdit()
        self.reference_edit.setPlaceholderText(
            "https://www.pornhub.com/view_video.php?viewkey=…"
        )
        layout.addWidget(self.reference_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Stáhnout novější")
        buttons.button(QDialogButtonBox.Cancel).setText("Zrušit")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def reference_url(self) -> str:
        return self.reference_edit.text().strip()


class SettingsDialog(QDialog):
    def __init__(self, storage: Storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.setWindowTitle("Nastavení")
        self.resize(760, 240)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        default_dir = str(Path.home() / "Stažené" / "Pornhub")
        dir_row = QHBoxLayout()
        self.directory_edit = QLineEdit(
            storage.get_setting("download_dir", default_dir)
        )
        choose_dir = QPushButton("Vybrat…")
        choose_dir.clicked.connect(self.choose_directory)
        dir_row.addWidget(self.directory_edit, 1)
        dir_row.addWidget(choose_dir)
        form.addRow("Složka pro stahování:", dir_row)

        marker_default = storage.get_setting("download_dir", default_dir)
        marker_row = QHBoxLayout()
        self.marker_directory_edit = QLineEdit(
            storage.get_setting("marker_dir", marker_default)
        )
        marker_button = QPushButton("Vybrat…")
        marker_button.clicked.connect(self.choose_marker_directory)
        marker_row.addWidget(self.marker_directory_edit, 1)
        marker_row.addWidget(marker_button)
        form.addRow("Složka databáze:", marker_row)

        cookie_row = QHBoxLayout()
        self.cookies_edit = QLineEdit(storage.get_setting("cookies_file", ""))
        self.cookies_edit.setPlaceholderText("volitelné cookies.txt")
        choose_cookie = QPushButton("Vybrat…")
        choose_cookie.clicked.connect(self.choose_cookies)
        cookie_row.addWidget(self.cookies_edit, 1)
        cookie_row.addWidget(choose_cookie)
        form.addRow("Cookies soubor:", cookie_row)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_directory(self):
        current = self.directory_edit.text().strip() or str(Path.home())
        value = QFileDialog.getExistingDirectory(
            self, "Vyber složku pro stahování", current
        )
        if value:
            self.directory_edit.setText(value)

    def choose_marker_directory(self):
        current = self.marker_directory_edit.text().strip() or str(Path.home())
        value = QFileDialog.getExistingDirectory(
            self, "Vyber složku pro databázi", current
        )
        if value:
            self.marker_directory_edit.setText(value)

    def choose_cookies(self):
        current = self.cookies_edit.text().strip() or str(Path.home())
        value, _ = QFileDialog.getOpenFileName(
            self,
            "Vyber cookies.txt",
            current,
            "Textové soubory (*.txt);;Všechny soubory (*)",
        )
        if value:
            self.cookies_edit.setText(value)

    def save(self):
        self.storage.set_setting(
            "marker_dir", self.marker_directory_edit.text().strip()
        )
        self.storage.set_setting(
            "download_dir", self.directory_edit.text().strip()
        )
        self.storage.set_setting(
            "cookies_file", self.cookies_edit.text().strip()
        )
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path):
        super().__init__()
        self.storage = Storage(data_dir)
        self.download_thread: QThread | None = None
        self.download_worker: DownloadWorker | None = None
        self.scan_thread: QThread | None = None
        self.scan_worker: ScanSourcesWorker | None = None
        self._scan_errors: list[tuple[str, str]] = []
        self._scan_renames: list[dict] = []
        self.baseline_thread: QThread | None = None
        self.baseline_worker: BaselineScanWorker | None = None
        self.baseline_url = ""
        self._current_urls: list[str] = []
        self._active_download_url = ""
        self._download_paused = False
        self._paused_info_text = ""
        self._download_cancel_requested = False

        self.setWindowTitle(f"{APPLICATION_NAME} {BUILD_VERSION}")
        self.resize(1120, 760)
        self._build_ui()
        self._apply_style()
        self.refresh_category_filter()
        self.refresh_jobs()

    def _build_ui(self):
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("STAHOVAČ")
        title.setObjectName("title")
        subtitle = QLabel("Pornhub")
        subtitle.setObjectName("subtitle")
        top.addWidget(title)
        top.addWidget(subtitle)
        top.addStretch(1)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Hledat v odkazech…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(320)
        self.search_edit.setMaximumWidth(420)
        self.search_edit.textChanged.connect(self.filter_jobs)
        top.addWidget(self.search_edit)
        top.addStretch(1)

        top_right = QVBoxLayout()
        self.settings_button = QPushButton("Nastavení")
        self.settings_button.clicked.connect(self.open_settings)
        top_right.addWidget(self.settings_button)
        self.open_folder_button = QPushButton("Otevřít složku")
        self.open_folder_button.clicked.connect(self.open_download_folder)
        top_right.addWidget(self.open_folder_button)
        top.addLayout(top_right)
        layout.addLayout(top)

        category_row = QHBoxLayout()
        category_row.setSpacing(6)

        self.category_tabs = QTabBar()
        self.category_tabs.setObjectName("categoryTabs")
        self.category_tabs.setDrawBase(False)
        self.category_tabs.setExpanding(False)
        self.category_tabs.setMovable(False)
        self.category_tabs.setUsesScrollButtons(True)
        self.category_tabs.currentChanged.connect(
            lambda _index: self.filter_jobs(self.search_edit.text())
        )
        category_row.addWidget(self.category_tabs, 1)

        self.add_category_button = QPushButton("+ Kategorie")
        self.add_category_button.clicked.connect(
            lambda _checked=False: self.create_category()
        )
        category_row.addWidget(self.add_category_button)
        layout.addLayout(category_row)

        buttons = QHBoxLayout()
        self.add_button = QPushButton("+ Odkazy")
        self.scan_button = QPushButton("Projít vybrané")
        self.download_new_button = QPushButton("Stáhnout nové")
        self.download_newer_button = QPushButton("Stáhnout novější…")
        self.delete_button = QPushButton("Odstranit")
        for button in (
            self.add_button,
            self.scan_button,
            self.download_new_button,
            self.download_newer_button,
            self.delete_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.add_button.clicked.connect(self.add_urls)
        self.scan_button.clicked.connect(self.scan_selected)
        self.download_new_button.clicked.connect(self.download_new)
        self.download_newer_button.clicked.connect(self.download_newer)
        self.delete_button.clicked.connect(self.delete_selected)

        self.count_label = QLabel("ODKAZY: 0")
        self.count_label.setObjectName("sectionTitle")
        layout.addWidget(self.count_label)

        self.table = HoverRowTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Název",
                "Kategorie",
                "Odkaz",
                "Poslední kontrola",
                "Nové",
                "Staženo",
                "Celkem",
                "Stav",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        self.table.cellDoubleClicked.connect(self.open_row_url)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_job_context_menu)
        layout.addWidget(self.table, 1)

        download_controls = QHBoxLayout()
        self.pause_download_button = QPushButton("Pozastavit")
        self.cancel_download_button = QPushButton("Zrušit stahování")
        self.pause_download_button.clicked.connect(self.toggle_pause_download)
        self.cancel_download_button.clicked.connect(self.cancel_download)
        self.pause_download_button.setEnabled(False)
        self.cancel_download_button.setEnabled(False)
        self.pause_download_button.hide()
        self.cancel_download_button.hide()
        download_controls.addWidget(self.pause_download_button)
        download_controls.addWidget(self.cancel_download_button)
        download_controls.addStretch(1)
        layout.addLayout(download_controls)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Stahování • %p%")
        self.progress.hide()
        layout.addWidget(self.progress)

        self.download_info_label = QLabel("")
        self.download_info_label.setObjectName("downloadInfo")
        self.download_info_label.setWordWrap(True)
        self.download_info_label.hide()
        layout.addWidget(self.download_info_label)

        hint = QLabel(
            "„Projít vybrané“ pouze zkontroluje profil nebo seznam a spočítá nové položky. "
            "Nic nestahuje. „Stáhnout nové“ pak stáhne jen obsah, který ještě není známý. "
            "Videa přeskočená při „Nastavit jako aktuální“ se v aplikaci počítají ve sloupci "
            "Staženo, ale interně zůstávají oddělená od skutečně fyzicky stažených souborů."
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
            QLabel#downloadInfo { color: #30343a; font-weight: 700; padding: 3px 2px; }
            QPushButton, QLineEdit, QTextEdit {
                background: #ffffff; border: 1px solid #c9ccd1; border-radius: 5px;
                padding: 6px 10px; min-height: 20px;
            }
            QTabBar#categoryTabs {
                background: transparent;
            }
            QTabBar#categoryTabs::tab {
                background: #e4e6e9;
                border: 1px solid #c9ccd1;
                border-bottom: 2px solid #c9ccd1;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 8px 16px;
                margin-right: 3px;
                font-weight: 700;
                color: #4a4f56;
            }
            QTabBar#categoryTabs::tab:selected {
                background: #ffffff;
                color: #18191b;
                border-bottom: 2px solid #ffffff;
            }
            QTabBar#categoryTabs::tab:hover:!selected {
                background: #f0f1f3;
            }
            QPushButton:hover { background: #f8f8f8; border-color: #9da2aa; }
            QPushButton:disabled { color: #969ba3; background: #eceef0; }
            QMenu {
                background: #ffffff;
                border: 1px solid #bfc3c9;
                padding: 4px;
            }
            QMenu::item {
                background: transparent;
                color: #1f2328;
                padding: 7px 24px 7px 10px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #d7e7fb;
                color: #111111;
            }
            QMenu::item:disabled {
                color: #9aa0a6;
            }
            QMenu::separator {
                height: 1px;
                background: #d9dce1;
                margin: 4px 6px;
            }
            QProgressBar {
                background: #ffffff; border: 1px solid #c9ccd1; border-radius: 5px;
                min-height: 24px; text-align: center; font-weight: 700;
            }
            QProgressBar::chunk { background: #7aa874; border-radius: 4px; }
            QTableWidget {
                background: #ffffff; alternate-background-color: #f8f9fa;
                border: 1px solid #c9ccd1; gridline-color: #e1e3e6;
                selection-background-color: #d7e7fb; selection-color: #111111;
            }
            QTableWidget::item:hover {
                background: #edf2f7;
            }
            QTableWidget::item:selected {
                background: #d7e7fb;
                color: #111111;
            }
            QTableWidget::item:selected:!active {
                background: #d7e7fb;
                color: #111111;
            }
            QHeaderView::section {
                background: #e8eaed; border: 0; border-right: 1px solid #cfd2d6;
                border-bottom: 1px solid #c4c7cc; padding: 7px; font-weight: 700;
            }
            """
        )

    @staticmethod
    def format_last_check(value: str) -> str:
        if not value:
            return "Ještě nezkontrolováno"

        for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                stamp = datetime.strptime(value, fmt)
                return f"{stamp.day}/{stamp.month}/{stamp.year}"
            except ValueError:
                pass

        return value

    @staticmethod
    def last_check_sort_value(value: str) -> float:
        if not value:
            return 0.0

        for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                return datetime.strptime(value, fmt).timestamp()
            except ValueError:
                pass

        return 0.0

    def refresh_category_filter(self):
        if not hasattr(self, "category_tabs"):
            return

        current_index = self.category_tabs.currentIndex()
        current = (
            self.category_tabs.tabData(current_index)
            if current_index >= 0
            else "__all__"
        )

        self.category_tabs.blockSignals(True)
        while self.category_tabs.count():
            self.category_tabs.removeTab(0)

        index = self.category_tabs.addTab("Všechny")
        self.category_tabs.setTabData(index, "__all__")

        for category in self.storage.categories():
            index = self.category_tabs.addTab(category)
            self.category_tabs.setTabData(index, category)

        index = self.category_tabs.addTab("Bez kategorie")
        self.category_tabs.setTabData(index, "")

        wanted_index = 0
        for tab_index in range(self.category_tabs.count()):
            if self.category_tabs.tabData(tab_index) == current:
                wanted_index = tab_index
                break

        self.category_tabs.setCurrentIndex(wanted_index)
        self.category_tabs.blockSignals(False)

    def create_category(self, assign_urls: list[str] | None = None):
        value, ok = QInputDialog.getText(
            self,
            "Nová kategorie",
            "Název kategorie:",
        )
        if not ok:
            return

        category = self.storage.add_category(value)
        if not category:
            return

        if assign_urls:
            self.storage.set_category(assign_urls, category)

        self.refresh_category_filter()
        for index in range(self.category_tabs.count()):
            if self.category_tabs.tabData(index) == category:
                self.category_tabs.setCurrentIndex(index)
                break
        self.refresh_jobs()

    def refresh_jobs(self):
        jobs = self.storage.jobs()
        current = {url for url in self.selected_urls()}

        sorting_enabled = self.table.isSortingEnabled()
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()

        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(jobs))

        for row, job in enumerate(jobs):
            url = str(job.get("url") or "")
            title = str(job.get("title") or self.source_label(url))
            stored_status = str(job.get("status") or "")
            last_run = str(job.get("last_run") or "")

            if self.is_single_video_url(url):
                total_count = 0
                downloaded_count = 0
                new_count = 0
                status = stored_status or "Připraveno"
                new_text = "—"
                downloaded_text = "—"
                total_text = "—"
            else:
                total_count, downloaded_count, new_count = self.storage.scan_counts(url)
                new_text = str(new_count)
                downloaded_text = str(downloaded_count)
                total_text = str(total_count)
                if stored_status.startswith(
                    ("Stahuji", "Kontroluji", "Pozastaveno", "Ruším")
                ):
                    status = stored_status
                elif stored_status in {"Chyba", "Zrušeno"}:
                    status = stored_status
                elif not last_run:
                    status = "Nezkontrolováno"
                elif new_count:
                    status = f"{new_count} nových"
                else:
                    status = "Aktuální"

            category = str(job.get("category") or "").strip()
            values = [
                title,
                category or "—",
                url,
                self.format_last_check(last_run),
                new_text,
                downloaded_text,
                total_text,
                status,
            ]

            last_run_sort = self.last_check_sort_value(last_run)
            sort_values = [
                title.casefold(),
                category.casefold(),
                url.casefold(),
                last_run_sort,
                new_count,
                downloaded_count,
                total_count,
                status.casefold(),
            ]

            for column, value in enumerate(values):
                item = SortableTableWidgetItem(value, sort_values[column])
                item.setData(Qt.UserRole, url)
                if column in {4, 5, 6}:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, item)

            if url in current:
                self.table.selectRow(row)

        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(sorting_enabled)
        if sorting_enabled and sort_column >= 0:
            self.table.sortItems(sort_column, sort_order)
        self.table.blockSignals(False)

        self.count_label.setText(f"ODKAZY: {len(jobs)}")
        self.filter_jobs(self.search_edit.text())

    def filter_jobs(self, text: str):
        needle = text.strip().casefold()
        selected_category = "__all__"
        if hasattr(self, "category_tabs"):
            index = self.category_tabs.currentIndex()
            if index >= 0:
                selected_category = self.category_tabs.tabData(index)

        jobs_by_url = {
            str(job.get("url") or ""): job
            for job in self.storage.jobs()
        }

        for row in range(self.table.rowCount()):
            haystack = " ".join(
                self.table.item(row, col).text() if self.table.item(row, col) else ""
                for col in range(self.table.columnCount())
            ).casefold()

            row_url_item = self.table.item(row, 0)
            row_url = str(row_url_item.data(Qt.UserRole) or "") if row_url_item else ""
            category = str(
                (jobs_by_url.get(row_url) or {}).get("category") or ""
            ).strip()

            search_mismatch = bool(needle and needle not in haystack)
            category_mismatch = bool(
                selected_category != "__all__"
                and category.casefold() != str(selected_category or "").casefold()
            )
            self.table.setRowHidden(row, search_mismatch or category_mismatch)

    def selected_urls(self) -> list[str]:
        model = self.table.selectionModel()
        if model is None:
            return []
        urls: list[str] = []
        for index in model.selectedRows():
            item = self.table.item(index.row(), 0)
            value = str(item.data(Qt.UserRole) or "") if item else ""
            if value:
                urls.append(value)
        return urls

    @staticmethod
    def is_single_video_url(url: str) -> bool:
        return "view_video.php" in str(url or "").casefold()

    @staticmethod
    def source_label(url: str) -> str:
        try:
            parsed = urlparse(str(url or ""))
            parts = [part for part in parsed.path.split("/") if part]
        except ValueError:
            parts = []

        ignored = {"videos", "video", "model", "users", "user", "pornstar", "channels"}
        for part in reversed(parts):
            if part.casefold() not in ignored:
                return part
        return "Nezjištěno"


    def show_error_dialog(
        self,
        title: str,
        summary: str,
        details: str,
    ):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(summary)
        box.setInformativeText(
            "Technický výpis je dostupný přes „Zobrazit podrobnosti“."
        )
        box.setDetailedText(str(details or "Žádné další podrobnosti."))
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def show_last_error(self, url: str):
        job = self.storage.job(url) or {}
        details = str(job.get("last_error") or "").strip()
        if not details:
            QMessageBox.information(
                self,
                "Podrobnosti chyby",
                "U tohoto odkazu není uložená žádná poslední chyba.",
            )
            return

        self.show_error_dialog(
            "Podrobnosti chyby",
            f"Poslední chyba pro {self.source_label(url)}.",
            details,
        )

    @staticmethod
    def _recovery_video_urls(items: list[dict], existing) -> list[str]:
        """Vybere odolnou směs referenčních videí napříč historií profilu.

        Pornhub profilové seznamy chodí v pořadí od nejnovějších. Proto vždy
        držíme úplně nejnovější i úplně nejstarší video, několik kusů z obou
        konců a zbytek rovnoměrně přes celý profil. Nakonec doplníme starší
        referenční odkazy z minulé kontroly, pokud se do limitu ještě vejdou.
        """
        old_values = existing if isinstance(existing, list) else []
        result: list[str] = []

        def item_url(item: dict) -> str:
            webpage_url = str(item.get("webpage_url") or "").strip()
            video_id = str(item.get("id") or "").strip()
            if "view_video.php" not in webpage_url.casefold() and video_id:
                webpage_url = (
                    "https://www.pornhub.com/view_video.php?viewkey="
                    + video_id
                )
            return webpage_url

        def add_url(url: str) -> None:
            value = str(url or "").strip()
            if value and value not in result and len(result) < 30:
                result.append(value)

        count = len(items)
        if count:
            # Dva hlavní kotvící body. Když přejmenování přežije jen část
            # historie, chceme mít šanci z obou konců profilu.
            add_url(item_url(items[0]))          # nejnovější
            add_url(item_url(items[-1]))         # nejstarší

            # Několik dalších nedávných a několik opravdu starých videí.
            for index in range(1, min(5, count)):
                add_url(item_url(items[index]))
            for offset in range(2, min(6, count + 1)):
                add_url(item_url(items[-offset]))

            # Další body rozprostřeme rovnoměrně napříč celým profilem.
            # Díky tomu nejsou všechny zálohy ze stejného období.
            spread_points = min(12, count)
            if spread_points > 1:
                for step in range(spread_points):
                    index = round(step * (count - 1) / (spread_points - 1))
                    add_url(item_url(items[index]))

        # Starší uložené reference necháme jako poslední pojistku. Mohou
        # obsahovat videa, která už v aktuálním seznamu profilu nejsou.
        for value in old_values:
            add_url(value)

        return result[:30]

    def _apply_profile_scan_result(
        self,
        url: str,
        items: list[dict],
        identity: dict,
    ) -> tuple[str, dict | None]:
        old_job = self.storage.job(url) or {}
        effective_url = url
        rename_info: dict | None = None

        if bool(identity.get("renamed")):
            new_url = str(identity.get("new_url") or "").strip()
            if new_url and new_url != url:
                old_name = str(
                    old_job.get("ph_profile_name")
                    or old_job.get("title")
                    or self.source_label(url)
                ).strip()
                self.storage.replace_source_url(url, new_url)
                effective_url = new_url
                rename_info = {
                    "old_url": url,
                    "new_url": new_url,
                    "old_name": old_name,
                    "new_name": str(
                        identity.get("profile_name")
                        or self.source_label(new_url)
                    ).strip(),
                }

        current_job = self.storage.job(effective_url) or old_job
        recovery_videos = self._recovery_video_urls(
            items,
            current_job.get("recovery_videos"),
        )

        changes = {
            "recovery_videos": recovery_videos,
        }
        user_id = str(identity.get("user_id") or "").strip()
        profile_name = str(identity.get("profile_name") or "").strip()
        profile_path = str(identity.get("profile_path") or "").strip()

        if user_id:
            changes["ph_user_id"] = user_id
        if profile_name:
            changes["ph_profile_name"] = profile_name
            changes["title"] = profile_name
        if profile_path:
            changes["ph_profile_path"] = profile_path

        self.storage.update_job(effective_url, **changes)
        return effective_url, rename_info

    def _show_profile_renames(self, renames: list[dict]):
        if not renames:
            return

        blocks = []
        for change in renames:
            old_name = str(change.get("old_name") or "Původní profil")
            new_name = str(change.get("new_name") or "Nový profil")
            old_url = str(change.get("old_url") or "")
            new_url = str(change.get("new_url") or "")
            blocks.append(
                f"{old_name} → {new_name}\n"
                f"{old_url}\n→ {new_url}"
            )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Pornhub profil byl přejmenován")
        if len(renames) == 1:
            box.setText(
                "Pornhub profil změnil jméno nebo adresu. "
                "Stahovač ověřil stejné interní ID účtu a odkaz automaticky opravil."
            )
        else:
            box.setText(
                f"Pornhub změnil {len(renames)} profilů. "
                "Stahovač ověřil jejich interní ID účtů a odkazy automaticky opravil."
            )
        box.setInformativeText("\n\n".join(blocks))
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def show_job_context_menu(self, position):
        item = self.table.itemAt(position)
        if item is None:
            return

        row = item.row()
        selected_rows = {
            index.row()
            for index in self.table.selectionModel().selectedRows()
        }
        if row not in selected_rows:
            self.table.clearSelection()
            self.table.selectRow(row)
        self.table.setCurrentCell(row, 0)
        url = str(self.table.item(row, 0).data(Qt.UserRole) or "")

        menu = QMenu(self)
        open_action = menu.addAction("Otevřít odkaz")
        error_action = menu.addAction("Podrobnosti chyby…")
        job = self.storage.job(url) or {}
        error_action.setEnabled(bool(str(job.get("last_error") or "").strip()))

        category_menu = menu.addMenu("Kategorie")
        category_actions: list[tuple[object, str]] = []
        no_category_action = category_menu.addAction("Bez kategorie")
        category_actions.append((no_category_action, ""))
        current_category = str(job.get("category") or "").strip()
        if not current_category:
            no_category_action.setCheckable(True)
            no_category_action.setChecked(True)

        for category in self.storage.categories():
            action = category_menu.addAction(category)
            action.setCheckable(True)
            action.setChecked(
                category.casefold() == current_category.casefold()
            )
            category_actions.append((action, category))

        category_menu.addSeparator()
        new_category_action = category_menu.addAction("Nová kategorie…")

        menu.addSeparator()
        current_action = menu.addAction("Nastavit jako aktuální…")
        current_action.setEnabled(
            bool(url)
            and not self.is_single_video_url(url)
            and self.download_thread is None
            and self.scan_thread is None
            and self.baseline_thread is None
        )
        menu.addSeparator()
        delete_action = menu.addAction("Odstranit")
        delete_action.setEnabled(
            self.download_thread is None
            and self.scan_thread is None
            and self.baseline_thread is None
        )
        chosen = menu.exec(self.table.viewport().mapToGlobal(position))

        if chosen == open_action:
            QDesktopServices.openUrl(QUrl(url))
        elif chosen == error_action:
            self.show_last_error(url)
        elif chosen == new_category_action:
            self.create_category(self.selected_urls())
        elif any(chosen == action for action, _category in category_actions):
            category = next(
                category
                for action, category in category_actions
                if chosen == action
            )
            self.storage.set_category(self.selected_urls(), category)
            self.refresh_category_filter()
            self.refresh_jobs()
        elif chosen == current_action:
            self.set_source_current(url)
        elif chosen == delete_action:
            self.delete_selected()

    def set_source_current(self, url: str):
        if (
            not url
            or self.is_single_video_url(url)
            or self.download_thread is not None
            or self.scan_thread is not None
            or self.baseline_thread is not None
        ):
            return

        dialog = MarkCurrentDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        cookies_file = self.storage.get_setting("cookies_file", "")
        thread = QThread(self)
        worker = BaselineScanWorker(
            url,
            cookies_file,
            self.storage.job(url) or {},
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._baseline_finished)
        worker.failed.connect(self._baseline_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._baseline_cleanup)

        self.baseline_url = url
        self.baseline_thread = thread
        self.baseline_worker = worker
        self.set_busy(True)
        self.statusBar().showMessage(
            "Procházím současný obsah a vytvářím výchozí stav…"
        )
        thread.start()

    @Slot(list, dict)
    def _baseline_finished(self, items: list, identity: dict):
        url = self.baseline_url
        try:
            effective_url, rename_info = self._apply_profile_scan_result(
                url,
                items,
                identity,
            )
        except Exception as exc:
            self._baseline_failed(str(exc))
            return

        self.baseline_url = effective_url
        self.storage.save_scan(effective_url, items)
        added = self.storage.mark_known_items(effective_url, items)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.storage.update_job(
            effective_url,
            status="Aktuální",
            progress=0,
            last_run=now,
            last_error="",
        )
        self.refresh_jobs()
        self.statusBar().showMessage(
            f"Nastaveno jako aktuální: {len(items)} videí zkontrolováno, "
            f"{added} nově označeno jako staženo v aplikaci. Nic se fyzicky nestahovalo.",
            7000,
        )
        if rename_info:
            self._show_profile_renames([rename_info])

    @Slot(str)
    def _baseline_failed(self, message: str):
        url = self.baseline_url
        if url:
            self.storage.update_job(
                url,
                status="Chyba",
                progress=0,
                last_error=message,
            )
        self.show_error_dialog(
            "Nastavit jako aktuální",
            "Výchozí stav se nepodařilo vytvořit.",
            message,
        )
        self.statusBar().showMessage("Výchozí stav se nepodařilo vytvořit.", 5000)

    @Slot()
    def _baseline_cleanup(self):
        self.baseline_thread = None
        self.baseline_worker = None
        self.baseline_url = ""
        self.set_busy(False)
        self.refresh_jobs()

    def add_urls(self):
        dialog = AddUrlsDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        urls = dialog.urls()
        invalid = []
        for url in urls:
            try:
                host = (urlparse(url).hostname or "").casefold()
            except ValueError:
                host = ""
            if not host or not (host == "pornhub.com" or host.endswith(".pornhub.com")):
                invalid.append(url)
        if invalid:
            QMessageBox.warning(self, "Odkazy", "Tahle část přijímá jen odkazy z Pornhubu.")
            return

        existing = {
            str(job.get("url") or "").strip()
            for job in self.storage.jobs()
        }
        added_urls = [
            url.strip()
            for url in urls
            if url.strip() and url.strip() not in existing
        ]

        added = self.storage.add_urls(urls)
        self.refresh_jobs()

        if added_urls:
            self.table.clearSelection()
            first_row = -1
            wanted = set(added_urls)
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                row_url = str(item.data(Qt.UserRole) or "") if item else ""
                if row_url in wanted:
                    self.table.selectRow(row)
                    if first_row < 0:
                        first_row = row
            if first_row >= 0:
                self.table.setCurrentCell(first_row, 0)

        self.statusBar().showMessage(f"Přidáno odkazů: {added}", 3000)

    def delete_selected(self):
        urls = self.selected_urls()
        if not urls:
            return
        answer = QMessageBox.question(
            self,
            "Odstranit odkazy",
            f"Odstranit vybrané odkazy ({len(urls)}) ze seznamu? Stažené soubory zůstanou na disku.",
        )
        if answer != QMessageBox.Yes:
            return
        self.storage.delete_urls(urls)
        self.refresh_jobs()

    def scan_selected(self):
        if (
            self.download_thread is not None
            or self.scan_thread is not None
            or self.baseline_thread is not None
        ):
            return

        urls = [
            url
            for url in self.selected_urls()
            if not self.is_single_video_url(url)
        ]
        if not urls:
            self.statusBar().showMessage(
                "Vyber alespoň jeden profil nebo seznam. Samostatné video kontrolu nepotřebuje.",
                4000,
            )
            return

        cookies_file = self.storage.get_setting("cookies_file", "")
        thread = QThread(self)
        source_meta = {
            url: (self.storage.job(url) or {})
            for url in urls
        }
        worker = ScanSourcesWorker(urls, cookies_file, source_meta)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.source_started.connect(self._scan_source_started)
        worker.source_finished.connect(self._scan_source_finished)
        worker.source_failed.connect(self._scan_source_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._scan_cleanup)

        self.scan_thread = thread
        self.scan_worker = worker
        self._scan_errors = []
        self._scan_renames = []
        self.set_busy(True)
        self.progress.setRange(0, max(1, len(urls)))
        self.progress.setValue(0)
        self.progress.setFormat("Kontrola • 0/%m • %p%")
        self.progress.show()
        self.download_info_label.setText("Připravuji kontrolu…")
        self.download_info_label.show()
        thread.start()

    @Slot(str, int, int)
    def _scan_source_started(self, url: str, index: int, total: int):
        label = self.source_label(url)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(max(0, index - 1))
        self.progress.setFormat(f"Kontrola {index}/{total} • %p%")
        self.download_info_label.setText(
            f"Kontroluji {index}/{total} • {label}"
        )
        self.storage.update_job(url, status="Kontroluji…", progress=0)
        self.refresh_jobs()

    @Slot(str, list, dict, int, int)
    def _scan_source_finished(
        self,
        url: str,
        items: list,
        identity: dict,
        index: int,
        total: int,
    ):
        try:
            effective_url, rename_info = self._apply_profile_scan_result(
                url,
                items,
                identity,
            )
        except Exception as exc:
            self._scan_source_failed(url, str(exc), index, total)
            return

        if rename_info:
            self._scan_renames.append(rename_info)

        self.storage.save_scan(effective_url, items)
        total_count, downloaded_count, new_count = self.storage.scan_counts(
            effective_url
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = f"{new_count} nových" if new_count else "Aktuální"

        current_job = self.storage.job(effective_url) or {}
        display_title = str(
            current_job.get("ph_profile_name")
            or current_job.get("title")
            or self.source_label(effective_url)
        )
        self.storage.update_job(
            effective_url,
            title=display_title,
            status=status,
            progress=0,
            last_run=now,
            last_error="",
        )
        self.progress.setValue(index)
        self.download_info_label.setText(
            f"Kontrola {index}/{total} • {total_count} celkem • "
            f"{new_count} nových • {downloaded_count} stažených"
        )
        self.refresh_jobs()

    @Slot(str, str, int, int)
    def _scan_source_failed(
        self,
        url: str,
        message: str,
        index: int,
        total: int,
    ):
        self.storage.update_job(
            url,
            status="Chyba",
            progress=0,
            last_error=message,
        )
        self._scan_errors.append((url, message))
        self.progress.setValue(index)
        self.refresh_jobs()
        self.statusBar().showMessage(
            f"Kontrola {index}/{total} se nepodařila: {message}",
            8000,
        )

    @Slot()
    def _scan_cleanup(self):
        errors = list(self._scan_errors)
        renames = list(self._scan_renames)
        self.scan_thread = None
        self.scan_worker = None
        self._scan_errors = []
        self._scan_renames = []
        self.set_busy(False)
        self.progress.hide()
        self.download_info_label.hide()
        self.refresh_jobs()

        if errors:
            details = "\n\n".join(
                f"{self.source_label(url)}\n{url}\n{message}"
                for url, message in errors
            )
            self.show_error_dialog(
                "Kontrola dokončena s chybou",
                (
                    "Kontrola se nepodařila u jednoho odkazu."
                    if len(errors) == 1
                    else f"Kontrola se nepodařila u {len(errors)} odkazů."
                ),
                details,
            )

        if renames:
            self._show_profile_renames(renames)

    def download_new(self):
        if (
            self.download_thread is not None
            or self.scan_thread is not None
            or self.baseline_thread is not None
        ):
            return

        selected = self.selected_urls()
        if not selected:
            self.statusBar().showMessage("Nejdřív vyber odkaz.", 2500)
            return

        targets: list[str] = []
        unscanned: list[str] = []
        for url in selected:
            if self.is_single_video_url(url):
                targets.append(url)
                continue

            job = next(
                (
                    item
                    for item in self.storage.jobs()
                    if str(item.get("url") or "") == url
                ),
                {},
            )
            last_check = str(job.get("last_run") or "")
            if not last_check:
                unscanned.append(url)
                continue

            _total, _downloaded, new_count = self.storage.scan_counts(url)
            if new_count > 0:
                targets.append(url)

        if not targets:
            if unscanned:
                self.statusBar().showMessage(
                    "Nejdřív použij „Projít vybrané“. U nezkontrolovaného profilu "
                    "ještě nevíme, co je nové.",
                    5000,
                )
            else:
                self.statusBar().showMessage("Žádné nové video ke stažení.", 3000)
            return

        self.start_download(targets)

    def download_newer(self):
        urls = self.selected_urls()
        if not urls:
            self.statusBar().showMessage(
                "Vyber profil nebo seznam, ze kterého chceš stáhnout novější videa.",
                3500,
            )
            return

        dialog = NewerThanDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        reference_url = dialog.reference_url()
        try:
            host = (urlparse(reference_url).hostname or "").casefold()
        except ValueError:
            host = ""
        if (
            not reference_url
            or not (host == "pornhub.com" or host.endswith(".pornhub.com"))
            or "view_video.php" not in reference_url
        ):
            QMessageBox.warning(
                self,
                "Referenční video",
                "Vlož platný odkaz na konkrétní Pornhub video.",
            )
            return

        self.start_download(urls, reference_url=reference_url)

    def start_download(self, urls: list[str], reference_url: str = ""):
        if (
            self.download_thread is not None
            or self.scan_thread is not None
            or self.baseline_thread is not None
        ):
            return
        if not urls:
            self.statusBar().showMessage("Nejdřív vyber nebo přidej odkaz.", 2500)
            return

        default_dir = str(Path.home() / "Stažené" / "Pornhub")
        destination = self.storage.get_setting("download_dir", default_dir)
        cookies_file = self.storage.get_setting("cookies_file", "")

        thread = QThread(self)
        worker = DownloadWorker(
            urls,
            destination,
            str(self.storage.archive_file),
            cookies_file,
            reference_url,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.item_started.connect(self._item_started)
        worker.item_progress.connect(self._item_progress)
        worker.item_finished.connect(self._item_finished)
        worker.item_cancelled.connect(self._item_cancelled)
        worker.video_downloaded.connect(self._video_downloaded)
        worker.failed.connect(self._download_failed)
        worker.finished.connect(self._download_finished)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._download_cleanup)

        self.download_thread = thread
        self.download_worker = worker
        self._current_urls = list(urls)
        self._active_download_url = ""
        self._download_paused = False
        self._paused_info_text = ""
        self._download_cancel_requested = False
        for url in urls:
            self.storage.update_job(url, last_error="")
        self.set_busy(True)
        self.pause_download_button.setText("Pozastavit")
        self.pause_download_button.setEnabled(True)
        self.cancel_download_button.setEnabled(True)
        self.pause_download_button.show()
        self.cancel_download_button.show()
        self.progress.setValue(0)
        self.progress.show()
        self.download_info_label.setText(f"Videa: 0/{len(urls)} • Připravuji stahování…")
        self.download_info_label.show()
        thread.start()

    def toggle_pause_download(self):
        worker = self.download_worker
        if worker is None or self._download_cancel_requested:
            return

        if self._download_paused:
            if not worker.resume():
                self.statusBar().showMessage(
                    "Stahování se nepodařilo znovu spustit.",
                    5000,
                )
                return
            self._download_paused = False
            self.pause_download_button.setText("Pozastavit")
            if self._active_download_url:
                self.storage.update_job(
                    self._active_download_url,
                    status="Stahuji",
                )
            if self._paused_info_text:
                self.download_info_label.setText(self._paused_info_text)
            self.statusBar().showMessage("Stahování pokračuje.", 3000)
            self.refresh_jobs()
            return

        if not worker.pause():
            self.statusBar().showMessage(
                "Pozastavení stahování se nepodařilo.",
                5000,
            )
            return

        self._download_paused = True
        self._paused_info_text = self.download_info_label.text()
        self.pause_download_button.setText("Pokračovat")
        if self._active_download_url:
            self.storage.update_job(
                self._active_download_url,
                status="Pozastaveno",
            )
        self.download_info_label.setText("Stahování je pozastavené.")
        self.statusBar().showMessage(
            "Stahování pozastaveno. Tlačítkem „Pokračovat“ ho obnovíš."
        )
        self.refresh_jobs()

    def cancel_download(self):
        worker = self.download_worker
        if worker is None or self._download_cancel_requested:
            return

        self._download_cancel_requested = True
        self._download_paused = False
        self.pause_download_button.setText("Pozastavit")
        self.pause_download_button.setEnabled(False)
        self.cancel_download_button.setEnabled(False)
        worker.cancel()

        if self._active_download_url:
            self.storage.update_job(
                self._active_download_url,
                status="Ruším…",
                last_error="",
            )
        self.download_info_label.setText("Ruším stahování…")
        self.statusBar().showMessage("Ruším stahování…")
        self.refresh_jobs()

    @Slot(str, int, int)
    def _item_started(self, url: str, index: int, total: int):
        self._active_download_url = url
        if self._download_cancel_requested:
            self.storage.update_job(
                url,
                status="Ruším…",
                progress=0,
                last_error="",
            )
            self.refresh_jobs()
            return

        status = "Pozastaveno" if self._download_paused else f"Stahuji {index}/{total}"
        self.storage.update_job(
            url,
            status=status,
            progress=0,
        )
        if self._download_paused:
            self.download_info_label.setText("Stahování je pozastavené.")
        else:
            self.statusBar().showMessage(f"Stahuji {index}/{total}…")
            self.download_info_label.setText(
                f"Video {index}/{total} • připravuji…"
            )
        self.refresh_jobs()

    def _row_for_url(self, url: str) -> int:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and str(item.data(Qt.UserRole) or "") == url:
                return row
        return -1

    @Slot(str, int, str, int, int, int, int)
    def _item_progress(
        self,
        url: str,
        percent: int,
        title: str,
        url_index: int,
        url_total: int,
        video_index: int,
        video_total: int,
    ):
        self.progress.setValue(percent)

        if self._download_cancel_requested:
            return

        if self._download_paused:
            row = self._row_for_url(url)
            if row >= 0:
                self.table.item(row, 7).setText("Pozastaveno")
            return

        clean_title = "" if title in {"Stahuji", "Dokončuji"} else title
        if video_total > 1:
            info = (
                f"Odkaz {url_index}/{url_total} • "
                f"Video {video_index}/{video_total} • {percent} %"
            )
        else:
            info = f"Video {url_index}/{url_total} • {percent} %"

        if clean_title:
            info += f" • {clean_title}"
        self.download_info_label.setText(info)

        row = self._row_for_url(url)
        if row < 0:
            return
        if clean_title and self.is_single_video_url(url):
            self.table.item(row, 0).setText(clean_title)
        self.table.item(row, 7).setText("Stahuji")

    @Slot(str)
    def _item_cancelled(self, url: str):
        self.storage.update_job(
            url,
            status="Zrušeno",
            progress=0,
            last_error="",
        )
        self.statusBar().showMessage(
            "Stahování bylo zrušeno. Rozpracovaný .part soubor může yt-dlp "
            "při příštím stahování navázat.",
            7000,
        )
        self.refresh_jobs()

    @Slot(dict)
    def _video_downloaded(self, item: dict):
        self.storage.mark_download(
            str(item.get("id", "")),
            extractor=str(item.get("extractor", "")),
            title=str(item.get("title", "")),
            webpage_url=str(item.get("webpage_url", "")),
            uploader=str(item.get("uploader", "")),
            filepath=str(item.get("filepath", "")),
            source_url=str(item.get("source_url", "")),
        )

    @Slot(str, bool, str, str)
    def _item_finished(self, url: str, success: bool, message: str, title: str):
        if success:
            if self.is_single_video_url(url):
                changes = {"status": "Aktuální", "progress": 100}
                if title:
                    changes["title"] = title
            else:
                _total, _downloaded, new_count = self.storage.scan_counts(url)
                changes = {
                    "status": (
                        f"{new_count} nových" if new_count else "Aktuální"
                    ),
                    "progress": 100,
                }
            changes["last_error"] = ""
            self.storage.update_job(url, **changes)
        else:
            self.storage.update_job(
                url,
                status="Chyba",
                progress=0,
                last_error=message,
            )
            if message:
                self.statusBar().showMessage(message, 8000)
        self.refresh_jobs()

    @Slot(str)
    def _download_failed(self, message: str):
        self.download_info_label.setText("Stahování se nepodařilo spustit.")
        for url in self._current_urls:
            self.storage.update_job(
                url,
                status="Chyba",
                progress=0,
                last_error=message,
            )
        self.refresh_jobs()
        self.show_error_dialog(
            "Stahování",
            "Stahování se nepodařilo spustit.",
            message,
        )

    @Slot(int, int, bool)
    def _download_finished(
        self,
        ok_count: int,
        error_count: int,
        cancelled: bool,
    ):
        if cancelled:
            self.download_info_label.setText(
                f"Stahování zrušeno • dokončeno: {ok_count} • chyby: {error_count}"
            )
            self.statusBar().showMessage(
                f"Stahování zrušeno. Dokončeno před zrušením: {ok_count}.",
                7000,
            )
            return

        self.progress.setValue(100 if error_count == 0 else self.progress.value())
        total = ok_count + error_count
        self.download_info_label.setText(
            f"Hotovo: {ok_count}/{total} • Chyby: {error_count}"
        )
        if error_count:
            error_blocks: list[str] = []
            for url in self._current_urls:
                job = self.storage.job(url) or {}
                message = str(job.get("last_error") or "").strip()
                if message:
                    error_blocks.append(
                        f"{self.source_label(url)}\n{url}\n{message}"
                    )
            self.show_error_dialog(
                "Stahování dokončeno s chybou",
                f"Hotovo: {ok_count} • Chyby: {error_count}",
                "\n\n".join(error_blocks)
                or "Podrobnosti chyby nejsou k dispozici.",
            )
        else:
            self.statusBar().showMessage(f"Stahování hotovo. Položek: {ok_count}", 5000)

    @Slot()
    def _download_cleanup(self):
        self.download_thread = None
        self.download_worker = None
        self._current_urls = []
        self._active_download_url = ""
        self._download_paused = False
        self._paused_info_text = ""
        self._download_cancel_requested = False
        self.pause_download_button.setText("Pozastavit")
        self.pause_download_button.setEnabled(False)
        self.cancel_download_button.setEnabled(False)
        self.pause_download_button.hide()
        self.cancel_download_button.hide()
        self.set_busy(False)
        self.progress.hide()
        self.download_info_label.hide()
        self.refresh_jobs()

    def set_busy(self, busy: bool):
        for button in (
            self.add_button,
            self.scan_button,
            self.download_new_button,
            self.download_newer_button,
            self.delete_button,
            self.settings_button,
        ):
            button.setDisabled(busy)

    def closeEvent(self, event):
        if self.download_worker is not None:
            self.download_worker.cancel()
        if self.download_thread is not None and self.download_thread.isRunning():
            self.download_thread.quit()
            self.download_thread.wait(4000)
        super().closeEvent(event)

    def open_settings(self):
        SettingsDialog(self.storage, self).exec()

    def open_download_folder(self):
        default_dir = str(Path.home() / "Stažené" / "Pornhub")
        folder = Path(self.storage.get_setting("download_dir", default_dir)).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            QMessageBox.warning(self, "Složka", str(exc))

    def open_row_url(self, row: int, _column: int):
        item = self.table.item(row, 0)
        url = str(item.data(Qt.UserRole) or "") if item else ""
        if url:
            QDesktopServices.openUrl(QUrl(url))
