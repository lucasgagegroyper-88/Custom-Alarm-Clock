from PySide6.QtGui import QColor, QPalette


def apply_theme(app, theme):
    dark = theme == "dark"
    bg, panel, field, text, muted, border = ("#101522", "#192132", "#202b3e", "#edf2ff", "#a1afca", "#34405a") if dark else ("#f2f5fb", "#ffffff", "#f8faff", "#17233a", "#53647d", "#d6deeb")
    accent = "#88a3ff" if dark else "#305bd7"
    app.setStyle("Fusion")
    palette = QPalette()
    for role, value in [(QPalette.ColorRole.Window, bg), (QPalette.ColorRole.WindowText, text), (QPalette.ColorRole.Base, panel), (QPalette.ColorRole.AlternateBase, field), (QPalette.ColorRole.Text, text), (QPalette.ColorRole.Button, field), (QPalette.ColorRole.ButtonText, text), (QPalette.ColorRole.Highlight, accent), (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, panel), (QPalette.ColorRole.ToolTipText, text)]:
        palette.setColor(role, QColor(value))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(muted))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(muted))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{ font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 10pt; }}
        QMainWindow, QDialog {{ background: {bg}; }}
        QLabel#title {{ font-size: 24pt; font-weight: 650; color:#17365D; background:rgba(242,245,251,225); border-radius:10px; padding:2px 14px; }}
        QLabel#clock {{ font-size: 35pt; font-weight: 600; color:{accent}; }}
        QLabel#section {{ font-size: 14pt; font-weight: 600; }}
        QLabel#hint {{ color:{muted}; }}
        QLabel#error {{ color:{'#ffaca8' if dark else '#a3262d'}; padding:8px; }}
        QFrame#card {{ background:{panel}; border:1px solid {border}; border-radius:12px; }}
        QPushButton {{ background:{field}; color:{text}; border:1px solid {border}; border-radius:7px; padding:8px 13px; }}
        QPushButton:hover {{ border-color:{accent}; }}
        QPushButton:pressed {{ background:{panel}; }}
        QPushButton:disabled {{ color:{muted}; }}
        QPushButton#primary {{ background:{accent}; color:{'#111827' if dark else '#ffffff'}; border:none; font-weight:600; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QComboBox {{ background:{field}; border:1px solid {border}; border-radius:5px; padding:5px; min-height:22px; selection-background-color:{accent}; }}
        QTableWidget, QListWidget {{ background:{panel}; alternate-background-color:{field}; border:1px solid {border}; border-radius:8px; gridline-color:{border}; }}
        QHeaderView::section {{ background:{field}; color:{muted}; border:none; padding:10px; font-weight:600; }}
        QTabWidget::pane {{ border:1px solid {border}; border-radius:7px; }}
        QTabBar::tab {{ background:{field}; padding:10px 15px; border-bottom:2px solid transparent; }}
        QTabBar::tab:selected {{ border-bottom-color:{accent}; color:{accent}; }}
        QGroupBox {{ border:1px solid {border}; border-radius:8px; margin-top:16px; padding:14px; }}
        QGroupBox::title {{ subcontrol-origin:margin; left:12px; color:{accent}; }}
        QScrollArea {{ border:none; }}
        QMenu {{ background:{panel}; color:{text}; border:1px solid {border}; padding:6px; }}
        QMenu::item {{ padding:7px 18px; }}
        QMenu::item:selected {{ background:{field}; }}
        QToolTip {{ background:{panel}; color:{text}; border:1px solid {border}; }}
    """)


def translucent_surfaces(theme, alpha=0.72):
    """Panel / field colours with alpha, so an embedded banner shows through dashboard panels."""
    panel, field = ("#192132", "#202b3e") if theme == "dark" else ("#ffffff", "#f8faff")
    def rgba(value):
        c = QColor(value)
        return f"rgba({c.red()},{c.green()},{c.blue()},{int(255 * alpha)})"
    return rgba(panel), rgba(field)
