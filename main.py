"""
rarons TTS Reader - Read long-form text aloud (KoboldCpp API)

- Built to work around limited context memory for KoboldCpp TTS,
  for long single-shot TTS requests.
"""

import sys

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget

from settings_tab import SettingsTab
from seed_vault_tab import SeedVaultTab
from narration_tab import NarrationTab
from stt_tab import STTTab
from stt_settings_tab import STTSettingsTab

# Titlebar info
_PROGRAMTITLE = "raron's TTS Reader v0.70 (2026.07.15)"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(_PROGRAMTITLE)
        self.resize(820, 620)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        self.settings_tab = SettingsTab()
        self.seed_vault_tab = SeedVaultTab()
        self.settings_tab.settings_loaded.connect(self.seed_vault_tab.load_from_path)

        self.narration_tab = NarrationTab(self.settings_tab, self.seed_vault_tab)
        self.stt_settings_tab = STTSettingsTab()
        self.stt_tab = STTTab(self.settings_tab, self.stt_settings_tab)

        tabs.addTab(self.narration_tab, "TTS (Narration)")
        tabs.addTab(self.stt_tab, "STT (Transcribing)")
        tabs.addTab(self.seed_vault_tab, "Seed Vault")
        tabs.addTab(self.settings_tab, "TTS Settings")
        tabs.addTab(self.stt_settings_tab, "STT Settings")

    def closeEvent(self, event):
        self.narration_tab.shutdown()
        self.stt_tab.shutdown()
        super().closeEvent(event)


# The default theme's scrollbar handle renders darker than its track,
# which reads oddly -- this swaps it: a darker track with a lighter handle.
_SCROLLBAR_QSS = """
QScrollBar:vertical {
    background: #2b2b2b;
    width: 14px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: #808080;
    min-height: 24px;
    border-radius: 5px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    background: #2b2b2b;
    height: 14px;
    margin: 0px;
}
QScrollBar::handle:horizontal {
    background: #808080;
    min-width: 24px;
    border-radius: 5px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
"""


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(_SCROLLBAR_QSS)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
