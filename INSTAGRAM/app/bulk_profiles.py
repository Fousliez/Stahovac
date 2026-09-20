from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)


PROFILE_RE = re.compile(r"^[A-Za-z0-9._]+$")
SEPARATOR_RE = re.compile(r"[\n,;]+")


class BulkProfileDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Přidat Instagram profily")
        self.resize(610, 390)

        layout = QVBoxLayout(self)
        title = QLabel("Přidej jeden nebo více Instagram profilů")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(title)

        hint = QLabel(
            "Každý profil dej na samostatný řádek. Můžeš vložit uživatelské jméno, "
            "@jméno nebo celou URL. Fungují také čárky a středníky."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "např.\n"
            "profil_jedna\n"
            "@profil.dva\n"
            "https://www.instagram.com/profil_tri/"
        )
        layout.addWidget(self.editor, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Přidat profily")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> list[str]:
        return [
            part.strip()
            for part in SEPARATOR_RE.split(self.editor.toPlainText())
            if part.strip()
        ]


def _username_from_value(value: str) -> str | None:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return None

    lowered = raw.lower()
    marker = "instagram.com/"
    if marker in lowered:
        start = lowered.find(marker) + len(marker)
        rest = raw[start:]
        username = rest.split("/", 1)[0]
        username = username.split("?", 1)[0].split("#", 1)[0]
    else:
        username = raw

    username = username.lstrip("@").strip()
    if not username or not PROFILE_RE.fullmatch(username):
        return None
    return username


def bulk_add_profiles(self) -> None:
    dialog = BulkProfileDialog(self)
    if dialog.exec() != QDialog.Accepted:
        return

    values = dialog.values()
    if not values:
        return

    existing = {
        str(profile["username"]).strip().casefold()
        for profile in self.db.profiles()
    }
    seen: set[str] = set()
    added: list[tuple[int, str]] = []
    duplicates: list[str] = []
    invalid: list[str] = []
    errors: list[str] = []

    for value in values:
        username = _username_from_value(value)
        if username is None:
            invalid.append(value)
            continue

        key = username.casefold()
        if key in existing or key in seen:
            duplicates.append(username)
            continue

        url = f"https://www.instagram.com/{username}/"
        try:
            profile_id = self.db.add_profile(username, url)
        except Exception as exc:
            errors.append(f"@{username}: {exc}")
            continue

        seen.add(key)
        existing.add(key)
        added.append((profile_id, username))

    self.refresh_profiles()
    if added:
        self.select_profile(added[-1][0])

    parts = [f"Přidáno: {len(added)}"]
    if duplicates:
        parts.append(f"už existovalo / duplicitní: {len(duplicates)}")
    if invalid:
        parts.append(f"neplatné: {len(invalid)}")
    if errors:
        parts.append(f"chyby: {len(errors)}")
    summary = "; ".join(parts) + "."
    self.statusBar().showMessage(summary, 5000)

    if invalid or errors:
        details: list[str] = [summary]
        if invalid:
            details.append("\nNeplatné položky:\n" + "\n".join(invalid[:20]))
        if errors:
            details.append("\nChyby při ukládání:\n" + "\n".join(errors[:20]))
        QMessageBox.warning(self, "Přidání profilů", "".join(details))


def install_bulk_profile_add(main_window_class) -> None:
    """Nahradí původní dialog pro jeden profil dávkovým zadáváním profilů."""
    main_window_class.add_profile = bulk_add_profiles
