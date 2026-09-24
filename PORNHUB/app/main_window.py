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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .downloader import download_url, resolve_reference_cutoff, scan_url_items
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


class DownloadWorker(QObject):
    item_started = Signal(str, int, int)
    item_progress = Signal(str, int, str, int, int, int, int)
    item_finished = Signal(str, bool, str, str)
    video_downloaded = Signal(dict)
    failed = Signal(str)
    finished = Signal(int, int)

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

    @Slot()
    def run(self):
        ok_count = 0
        error_count = 0
        total = len(self.urls)

        try:
            reference_timestamp, reference_date_after = resolve_reference_cutoff(
                self.reference_url,
                self.cookies_file,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        for index, url in enumerate(self.urls, start=1):
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
                )
            except Exception as exc:
                error_count += 1
                self.item_finished.emit(url, False, str(exc), "")
            else:
                ok_count += 1
                self.item_finished.emit(url, True, "", title)

        self.finished.emit(ok_count, error_count)


class BaselineScanWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, url: str, cookies_file: str):
        super().__init__()
        self.url = url
        self.cookies_file = cookies_file

    @Slot()
    def run(self):
        try:
            self.finished.emit(scan_url_items(self.url, self.cookies_file))
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
        self.baseline_thread: QThread | None = None
        self.baseline_worker: BaselineScanWorker | None = None
        self.baseline_url = ""
        self._current_urls: list[str] = []

        self.setWindowTitle(f"{APPLICATION_NAME} {BUILD_VERSION}")
        self.resize(1120, 760)
        self._build_ui()
        self._apply_style()
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

        buttons = QHBoxLayout()
        self.add_button = QPushButton("+ Odkazy")
        self.download_selected_button = QPushButton("Stáhnout vybrané")
        self.download_all_button = QPushButton("Stáhnout vše")
        self.download_newer_button = QPushButton("Stáhnout novější…")
        self.delete_button = QPushButton("Odstranit")
        for button in (
            self.add_button,
            self.download_selected_button,
            self.download_all_button,
            self.download_newer_button,
            self.delete_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.add_button.clicked.connect(self.add_urls)
        self.download_selected_button.clicked.connect(self.download_selected)
        self.download_all_button.clicked.connect(self.download_all)
        self.download_newer_button.clicked.connect(self.download_newer)
        self.delete_button.clicked.connect(self.delete_selected)

        self.count_label = QLabel("ODKAZY: 0")
        self.count_label.setObjectName("sectionTitle")
        layout.addWidget(self.count_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Název", "Odkaz", "Stav", "Průběh", "Poslední kontrola"]
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
            "Každé dokončené video se ukládá do databáze PORNHUB_MARKERY.db ve zvolené "
            "složce databáze. Eviduje ID, název, URL, profil/uploadera, cestu k souboru "
            "a datum stažení. Databáze zároveň drží pomocný yt-dlp archiv v synchronizaci."
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
            QPushButton, QComboBox, QLineEdit, QTextEdit {
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
            title = str(job.get("title") or "Nezjištěno")
            url = str(job.get("url") or "")
            status = str(job.get("status") or "Připraveno")
            progress = int(job.get("progress") or 0)
            last_run = str(job.get("last_run") or "")

            values = [
                title,
                url,
                status,
                f"{progress} %",
                self.format_last_check(last_run),
            ]

            last_run_sort = self.last_check_sort_value(last_run)

            sort_values = [
                title.casefold(),
                url.casefold(),
                status.casefold(),
                progress,
                last_run_sort,
            ]

            for column, value in enumerate(values):
                item = SortableTableWidgetItem(value, sort_values[column])
                item.setData(Qt.UserRole, url)
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
        for row in range(self.table.rowCount()):
            haystack = " ".join(
                self.table.item(row, col).text() if self.table.item(row, col) else ""
                for col in range(self.table.columnCount())
            ).casefold()
            self.table.setRowHidden(row, bool(needle and needle not in haystack))

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

    def show_job_context_menu(self, position):
        item = self.table.itemAt(position)
        if item is None:
            return

        row = item.row()
        self.table.selectRow(row)
        self.table.setCurrentCell(row, 0)
        url = str(self.table.item(row, 0).data(Qt.UserRole) or "")

        menu = QMenu(self)
        open_action = menu.addAction("Otevřít odkaz")
        current_action = menu.addAction("Nastavit jako aktuální…")
        current_action.setEnabled(
            bool(url)
            and not self.is_single_video_url(url)
            and self.download_thread is None
            and self.baseline_thread is None
        )
        menu.addSeparator()
        delete_action = menu.addAction("Odstranit")
        delete_action.setEnabled(
            self.download_thread is None and self.baseline_thread is None
        )
        chosen = menu.exec(self.table.viewport().mapToGlobal(position))

        if chosen == open_action:
            QDesktopServices.openUrl(QUrl(url))
        elif chosen == current_action:
            self.set_source_current(url)
        elif chosen == delete_action:
            self.delete_selected()

    def set_source_current(self, url: str):
        if (
            not url
            or self.is_single_video_url(url)
            or self.download_thread is not None
            or self.baseline_thread is not None
        ):
            return

        dialog = MarkCurrentDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        cookies_file = self.storage.get_setting("cookies_file", "")
        thread = QThread(self)
        worker = BaselineScanWorker(url, cookies_file)
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

    @Slot(list)
    def _baseline_finished(self, items: list):
        url = self.baseline_url
        added = self.storage.mark_known_items(url, items)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.storage.update_job(
            url,
            status="Aktuální",
            progress=0,
            last_run=now,
        )
        self.refresh_jobs()
        self.statusBar().showMessage(
            f"Nastaveno jako aktuální: {len(items)} videí zkontrolováno, "
            f"{added} nově uloženo jako známých. Nic se nestahovalo.",
            7000,
        )

    @Slot(str)
    def _baseline_failed(self, message: str):
        QMessageBox.warning(self, "Nastavit jako aktuální", message)
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
        added = self.storage.add_urls(urls)
        self.refresh_jobs()
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

    def download_selected(self):
        self.start_download(self.selected_urls())

    def download_all(self):
        self.start_download([str(job.get("url") or "") for job in self.storage.jobs() if job.get("url")])

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
        if self.download_thread is not None or self.baseline_thread is not None:
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
        self.set_busy(True)
        self.progress.setValue(0)
        self.progress.show()
        self.download_info_label.setText(f"Videa: 0/{len(urls)} • Připravuji stahování…")
        self.download_info_label.show()
        thread.start()

    @Slot(str, int, int)
    def _item_started(self, url: str, index: int, total: int):
        self.storage.update_job(
            url,
            status=f"Stahuji {index}/{total}",
            progress=0,
            last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
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
        if clean_title:
            self.table.item(row, 0).setText(clean_title)
        self.table.item(row, 2).setText("Stahuji")
        self.table.item(row, 3).setText(f"{percent} %")

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
            changes = {"status": "Hotovo", "progress": 100}
            if title:
                changes["title"] = title
            self.storage.update_job(url, **changes)
        else:
            self.storage.update_job(url, status="Chyba", progress=0)
            if message:
                self.statusBar().showMessage(message, 8000)
        self.refresh_jobs()

    @Slot(str)
    def _download_failed(self, message: str):
        self.download_info_label.setText("Stahování se nepodařilo spustit.")
        QMessageBox.warning(self, "Stahování", message)

    @Slot(int, int)
    def _download_finished(self, ok_count: int, error_count: int):
        self.progress.setValue(100 if error_count == 0 else self.progress.value())
        total = ok_count + error_count
        self.download_info_label.setText(
            f"Hotovo: {ok_count}/{total} • Chyby: {error_count}"
        )
        if error_count:
            QMessageBox.warning(
                self,
                "Stahování dokončeno",
                f"Hotovo: {ok_count}\nChyby: {error_count}\n\nPodrobnost poslední chyby je ve stavovém řádku.",
            )
        else:
            self.statusBar().showMessage(f"Stahování hotovo. Položek: {ok_count}", 5000)

    @Slot()
    def _download_cleanup(self):
        self.download_thread = None
        self.download_worker = None
        self._current_urls = []
        self.set_busy(False)
        self.progress.hide()
        self.refresh_jobs()

    def set_busy(self, busy: bool):
        for button in (
            self.add_button,
            self.download_selected_button,
            self.download_all_button,
            self.download_newer_button,
            self.delete_button,
            self.settings_button,
        ):
            button.setDisabled(busy)

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
