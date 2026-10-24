"""Colours and the application style sheet.

Colour convention used on every page:
  green  = LIVE sensor data (what the glove is doing now)
  blue   = STORED gesture / editor (what was saved)
  amber  = LIVE DETECTION (what the current hand matches)
  red    = errors / missing hardware
"""

BG = "#14171c"
PANEL = "#1d2128"
PANEL_2 = "#252a33"
BORDER = "#323845"
TEXT = "#e6e9ef"
MUTED = "#8b93a3"
LIVE = "#3ecf8e"
STORED = "#5aa9ff"
DETECT = "#ffb547"
ERROR = "#ff5d5d"
WARN = "#ffcf5c"
SIM = "#c792ea"

STYLE = f"""
QWidget {{ color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; }}
QMainWindow, QStackedWidget, QWidget#pageBody, QDialog, QMessageBox {{ background: {BG}; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ font-size: 16pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 11pt; font-weight: 600; color: {MUTED}; letter-spacing: 1px; }}
QLabel#muted {{ color: {MUTED}; }}
QPushButton {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px; }}
QPushButton:hover {{ border-color: {STORED}; }}
QPushButton:disabled {{ color: #5b6270; }}
QPushButton#primary {{ background: #1f4f8a; border-color: {STORED}; font-weight: 600; }}
QPushButton#live {{ background: #1c5a40; border-color: {LIVE}; font-weight: 600; }}
QPushButton#danger {{ background: #5a1f24; border-color: {ERROR}; }}
QListWidget {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 5px; }}
QListWidget::item {{ padding: 4px; }}
QListWidget::item:selected {{ background: #1f4f8a; color: {TEXT}; }}
QListWidget#nav {{ background: {PANEL}; border: none; font-size: 11pt; padding-top: 8px; }}
QListWidget#nav::item {{ padding: 10px 14px; }}
QListWidget#nav::item:selected {{ background: {PANEL_2}; color: {LIVE}; border-left: 3px solid {LIVE}; }}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px; }}
QTableWidget {{ background: {PANEL}; gridline-color: {BORDER}; border: 1px solid {BORDER}; }}
QHeaderView::section {{ background: {PANEL_2}; color: {MUTED}; border: none; padding: 5px; }}
QProgressBar {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 4px; text-align: center; }}
QProgressBar::chunk {{ background: {LIVE}; border-radius: 3px; }}
QScrollArea {{ border: none; }}
"""
