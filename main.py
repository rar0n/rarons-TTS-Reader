"""
rarons TTS Reader - Read long-form text aloud (KoboldCpp API)
                    Now also with STT - Speech To Text transcription

- Built to work around limited context memory for KoboldCpp TTS,
  for long single-shot TTS requests.
"""

import sys

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget

from tts_settings_tab import SettingsTab
from tts_seed_vault_tab import SeedVaultTab
from tts_narration_tab import NarrationTab
from stt_transcription_tab import STTTab
from stt_settings_tab import STTSettingsTab
from program_settings_tab import ProgramSettingsTab
from about_tab import AboutTab
from ui_common import DEFAULT_SETTINGS_PATH, apply_theme, load_json_dict

# Titlebar info
_PROGRAMTITLE = "raron's TTS Reader 2026.11.10"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(_PROGRAMTITLE)
        self.resize(820, 620)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        self.tts_settings_tab = SettingsTab()
        self.tts_seed_vault_tab = SeedVaultTab()
        self.tts_settings_tab.settings_loaded.connect(self.tts_seed_vault_tab.load_from_path)

        self.tts_narration_tab = NarrationTab(self.tts_settings_tab, self.tts_seed_vault_tab)
        self.stt_settings_tab = STTSettingsTab()
        self.program_settings_tab = ProgramSettingsTab()
        self.stt_transcription_tab = STTTab(self.tts_settings_tab, self.stt_settings_tab)

        tabs.addTab(self.tts_narration_tab, "TTS (Narration)")
        tabs.addTab(self.stt_transcription_tab, "STT (Transcription)")
        tabs.addTab(self.tts_seed_vault_tab, "Seed Vault")
        tabs.addTab(self.tts_settings_tab, "TTS Settings")
        tabs.addTab(self.stt_settings_tab, "STT Settings")
        tabs.addTab(self.program_settings_tab, "Program Settings")
        tabs.addTab(AboutTab(_PROGRAMTITLE), "About")

    def closeEvent(self, event):
        self.tts_narration_tab.shutdown()
        self.stt_transcription_tab.shutdown()
        self.tts_seed_vault_tab.save_layout()
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
    # Apply the saved theme before any widgets exist, so the window doesn't
    # open in one theme and flip to the other.
    apply_theme(load_json_dict(DEFAULT_SETTINGS_PATH).get("THEME", "Light"))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
