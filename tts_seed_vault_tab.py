"""
SeedVaultTab: a little table of remembered (voice, seed, instruction,
notes) rows, saved into the shared settings.json under "SEED_VAULT".
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QMessageBox, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QPushButton,
)

from ui_common import (
    DEFAULT_SETTINGS_PATH, STATUS_STYLE_SEED_VAULT,
    COLOR_DARK_GREEN, COLOR_DARK_RED, COLOR_DARK_BLUE,
    STANDARD_BTN_HEIGHT, btn_style, load_json_dict, save_json_merged,
    ElidingLabel,
)


class _NumericTableWidgetItem(QTableWidgetItem):
    """A QTableWidgetItem that sorts by numeric value when its text parses
    as one (e.g. seeds), falling back to plain text comparison otherwise
    -- so column-header sorting on the Seed column gives 2 < 10 instead of
    the lexicographic "10" < "2"."""

    def __lt__(self, other):
        try:
            return float(self.text()) < float(other.text())
        except (ValueError, TypeError):
            return super().__lt__(other)


# Default widths of the first three columns as a fraction of the table's
# visible width (the last column, Notes, just takes whatever is left).
_DEFAULT_COLUMN_FRACTIONS = [0.20, 0.15, 0.20]


class _VaultTable(QTableWidget):
    """QTableWidget that tells its owner whenever its width changes, so
    the owner can rescale the columns to keep their relative proportions."""

    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class SeedVaultTab(QWidget):
    """A little table of remembered (voice, seed, instruction, notes) rows.
    Rows get added from the Narration tab's "Store seed" button; this tab
    just manages the table itself (remove/copy-back/save) plus auto-loading
    from the shared settings.json on startup."""

    seed_requested = Signal(str, str, str)  # (voice, seed, instruction) -- picked to send back to the Narration tab

    def __init__(self, parent=None):
        super().__init__(parent)
        self._column_fractions = list(_DEFAULT_COLUMN_FRACTIONS)
        self._applying_widths = False
        self._build_ui()
        self._try_autoload()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.table = _VaultTable(0, 4)
        self.table.setHorizontalHeaderLabels(["Voice", "Seed", "Instruction", "Notes"])
        self.table.horizontalHeader().setStretchLastSection(True)
        # Column widths are kept as fractions of the table's width (see
        # _apply_column_widths), so they scale with the window and can be
        # saved/restored independent of the window size.
        self.table.resized.connect(self._apply_column_widths)
        self.table.horizontalHeader().sectionResized.connect(self._on_section_resized)
        self._apply_column_widths()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Double-clicking a Voice or Seed cell (not editable) sends the row
        # to the Narration tab; double-clicking Instruction/Notes still
        # edits them, as usual.
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.remove_row_btn = QPushButton("🗑 Remove row")
        self.copy_to_reader_btn = QPushButton("Copy to Narration")
        self.save_btn = QPushButton("💾 Save Table")
        self.remove_row_btn.setStyleSheet(btn_style(COLOR_DARK_RED))
        self.copy_to_reader_btn.setStyleSheet(btn_style(COLOR_DARK_BLUE))
        self.save_btn.setStyleSheet(btn_style(COLOR_DARK_GREEN))
        for btn in (self.remove_row_btn, self.copy_to_reader_btn, self.save_btn):
            btn.setFixedHeight(STANDARD_BTN_HEIGHT)
        self.remove_row_btn.clicked.connect(self._on_remove_row_clicked)
        self.copy_to_reader_btn.clicked.connect(self._on_copy_to_reader_clicked)
        self.save_btn.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self.remove_row_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.save_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.copy_to_reader_btn)
        layout.addLayout(btn_row)

        self.status_label = ElidingLabel("")
        self.status_label.setStyleSheet(STATUS_STYLE_SEED_VAULT)
        layout.addWidget(self.status_label)

    # ---- row management -----------------------------------------------------

    def add_row(self, voice: str, seed: str, instruction: str = "", notes: str = ""):
        # Sorting has to be off while a row is being built -- with it on,
        # Qt can re-sort the table in between individual setItem() calls
        # (as soon as the sort-column cell has data but others don't yet),
        # which would scatter this row's cells across the wrong row
        # indices instead of keeping them together.
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        row = self.table.rowCount()
        self.table.insertRow(row)
        voice_item = QTableWidgetItem(voice)
        seed_item = _NumericTableWidgetItem(seed)
        instruction_item = QTableWidgetItem(instruction)
        notes_item = QTableWidgetItem(notes)
        # Voice/Seed are set programmatically (from "Store seed") -- only
        # the Instruction/Notes cells should be directly editable by
        # clicking into them.
        for item in (voice_item, seed_item):
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 0, voice_item)
        self.table.setItem(row, 1, seed_item)
        self.table.setItem(row, 2, instruction_item)
        self.table.setItem(row, 3, notes_item)
        self.table.setSortingEnabled(was_sorting)

    def _gather_rows(self) -> list:
        rows = []
        for r in range(self.table.rowCount()):
            voice = self.table.item(r, 0).text() if self.table.item(r, 0) else ""
            seed = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            instruction = self.table.item(r, 2).text() if self.table.item(r, 2) else ""
            notes = self.table.item(r, 3).text() if self.table.item(r, 3) else ""
            rows.append({"voice": voice, "seed": seed, "instruction": instruction, "notes": notes})
        return rows

    def _populate_rows(self, rows: list, sort_column: int = 0, sort_order: int = 0):
        """Replaces the table's contents, then sorts by the given
        column/order (Qt.SortOrder value: 0 ascending, 1 descending)."""
        header = self.table.horizontalHeader()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for entry in rows:
            self.add_row(entry.get("voice", ""), entry.get("seed", ""), entry.get("instruction", ""), entry.get("notes", ""))
        header.setSortIndicator(sort_column, Qt.SortOrder(sort_order))
        # Turning sorting back on sorts by the header's current indicator.
        self.table.setSortingEnabled(True)

    # ---- column widths (stored as fractions of the table width) -----------

    def _apply_column_widths(self):
        width = self.table.viewport().width()
        if width <= 0:
            return
        self._applying_widths = True
        try:
            for col, fraction in enumerate(self._column_fractions):
                self.table.setColumnWidth(col, max(30, int(round(fraction * width))))
        finally:
            self._applying_widths = False

    def _on_section_resized(self, col: int, _old: int, new: int):
        """Only user drags matter here -- programmatic resizes from
        _apply_column_widths, and the stretched last column, are ignored."""
        if self._applying_widths or col >= len(self._column_fractions):
            return
        width = self.table.viewport().width()
        if width > 0:
            self._column_fractions[col] = new / width

    def _on_remove_row_clicked(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "No selection", "Select a row first.")
            return
        self.table.removeRow(row)

    def _on_copy_to_reader_clicked(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "No selection", "Select a row first.")
            return
        self._copy_row_to_reader(row)

    def _on_cell_double_clicked(self, row: int, col: int):
        if col in (0, 1):
            self._copy_row_to_reader(row)

    def _copy_row_to_reader(self, row: int):
        """Shared by the "Copy to Narration" button and row double-click."""
        voice = self.table.item(row, 0).text() if self.table.item(row, 0) else ""
        seed = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
        instruction = self.table.item(row, 2).text() if self.table.item(row, 2) else ""
        self.seed_requested.emit(voice, seed, instruction)

    # ---- save / load ----------------------------------------------------------

    def _try_autoload(self):
        self.load_from_path(DEFAULT_SETTINGS_PATH, quiet_if_empty=True)

    def load_from_path(self, path: Path, quiet_if_empty: bool = False):
        """Repopulates the table from `path`'s SEED_VAULT entry (if any).
        Public so SettingsTab's "Load Settings…" button can keep this tab
        in sync with whatever file it just loaded, instead of only ever
        reflecting the default settings.json from startup."""
        data = load_json_dict(path)
        rows = data.get("SEED_VAULT", [])
        self._load_column_fractions(data.get("SEED_VAULT_COLUMNS"))
        sort = data.get("SEED_VAULT_SORT")
        sort_column, sort_order = 0, 0
        if isinstance(sort, dict):
            try:
                sort_column = int(sort.get("column", 0))
                sort_order = int(sort.get("order", 0))
            except (TypeError, ValueError):
                sort_column, sort_order = 0, 0
            if not 0 <= sort_column < self.table.columnCount() or sort_order not in (0, 1):
                sort_column, sort_order = 0, 0
        if rows:
            self._populate_rows(rows, sort_column, sort_order)
            self.status_label.setText(f"Loaded {len(rows)} seed(s) from {path}")
        elif not quiet_if_empty:
            self.table.setRowCount(0)
            self.status_label.setText(f"No seeds found in {path}")

    def _load_column_fractions(self, fractions):
        """Applies a saved list of column width fractions, ignoring
        anything that isn't a sane list of numbers."""
        n = len(_DEFAULT_COLUMN_FRACTIONS)
        if isinstance(fractions, list) and len(fractions) == n:
            try:
                parsed = [float(f) for f in fractions]
            except (TypeError, ValueError):
                return
            if all(0.02 <= f <= 0.9 for f in parsed):
                self._column_fractions = parsed
                self._apply_column_widths()

    def _layout_settings(self) -> dict:
        header = self.table.horizontalHeader()
        return {
            "SEED_VAULT_SORT": {
                "column": header.sortIndicatorSection(),
                "order": int(header.sortIndicatorOrder().value),
            },
            "SEED_VAULT_COLUMNS": [round(f, 4) for f in self._column_fractions],
        }

    def save_layout(self):
        """Saves just the sort order and column widths (not the rows),
        so these stick between runs even without pressing "Save Table"
        -- the rows themselves stay manual-save so a bad edit can still
        be undone by reloading."""
        try:
            save_json_merged(DEFAULT_SETTINGS_PATH, self._layout_settings())
        except OSError:
            pass

    def save_to_disk(self) -> bool:
        """Overwrites settings.json's SEED_VAULT entry with the table's
        current contents. Public (no leading underscore) since the
        "💾 Save Table" button below calls this -- the only place a row
        actually gets persisted; adding a row via "Store seed" on the
        Narration tab only updates the table in memory."""
        try:
            save_json_merged(DEFAULT_SETTINGS_PATH, {"SEED_VAULT": self._gather_rows(), **self._layout_settings()})
            return True
        except OSError as e:
            QMessageBox.warning(self, "Couldn't save seed vault", str(e))
            return False

    def _on_save_clicked(self):
        if self.save_to_disk():
            self.status_label.setText(f"Saved seed vault to {DEFAULT_SETTINGS_PATH}")
