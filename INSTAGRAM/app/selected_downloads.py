from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QPushButton


def _layout_with_widget(layout, target):
    """Najde vnořený layout, který přímo obsahuje zadaný widget."""
    if layout is None:
        return None
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is target:
            return layout
        child = item.layout()
        if child is not None:
            found = _layout_with_widget(child, target)
            if found is not None:
                return found
    return None


def _download_selected_posts(self) -> None:
    if self.download_thread is not None or self.scan_thread is not None:
        return

    profile_id = self.selected_profile_id()
    if profile_id is None:
        self.statusBar().showMessage("Nejdřív vyber profil.", 2500)
        return

    post_ids = self.selected_post_ids()
    if not post_ids:
        self.statusBar().showMessage("Nejdřív vyber jeden nebo více příspěvků.", 3000)
        return

    profile = self.db.profile(profile_id)
    if profile is None:
        return

    posts: list[dict] = []
    for post_id in post_ids:
        row = self.db.post(post_id)
        if row is None:
            continue
        if int(row["profile_id"]) != int(profile_id):
            continue
        posts.append(dict(row))

    if not posts:
        self.statusBar().showMessage("Vybrané příspěvky už nejsou dostupné v databázi.", 3000)
        return

    base = self.db.get_setting(
        "download_dir",
        str(Path.home() / "Stažené" / "Instagram"),
    )
    destination = str(Path(base).expanduser() / profile["username"])
    cookies = self.db.get_setting("cookies_file")

    self.downloading_profile_id = profile_id
    self.download_destination = destination
    self._download_errors = []
    self.set_busy(True)

    # Import až při použití, aby tento modul mohl být nainstalovaný jako patch
    # nad MainWindow bez kruhového importu při startu aplikace.
    from app.main_window import DownloadWorker

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


def install_selected_downloads(main_window_class) -> None:
    """Přidá tlačítko pro stažení libovolných označených příspěvků."""
    if getattr(main_window_class, "_selected_downloads_installed", False):
        return

    original_build_ui = main_window_class._build_ui
    original_set_busy = main_window_class.set_busy

    def build_ui(self):
        original_build_ui(self)

        self.download_selected_button = QPushButton("Stáhnout vybrané")
        self.download_selected_button.clicked.connect(self.download_selected_posts)

        root_layout = self.centralWidget().layout()
        target_layout = _layout_with_widget(root_layout, self.open_post_button)
        if target_layout is not None:
            insert_at = target_layout.indexOf(self.open_post_button) + 1
            target_layout.insertWidget(insert_at, self.download_selected_button)

    def set_busy(self, busy: bool):
        original_set_busy(self, busy)
        button = getattr(self, "download_selected_button", None)
        if button is not None:
            button.setDisabled(busy)

    main_window_class._build_ui = build_ui
    main_window_class.set_busy = set_busy
    main_window_class.download_selected_posts = _download_selected_posts
    main_window_class._selected_downloads_installed = True
