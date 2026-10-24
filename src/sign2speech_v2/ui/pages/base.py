from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from sign2speech_v2.controller import Result, StudioController
from sign2speech_v2.ui.widgets import label


class Page(QWidget):
    title = "Page"

    def __init__(self, controller: StudioController, notify: Callable[[Result], None]) -> None:
        super().__init__()
        self.c = controller
        self.notify = notify
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("pageBody")
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self.layout_ = QVBoxLayout(inner)
        self.layout_.setContentsMargins(22, 18, 22, 18)
        self.layout_.setSpacing(14)
        self.layout_.addWidget(label(self.title, "h1"))

    def act(self, result: Result) -> Result:
        self.notify(result)
        self.refresh()
        return result

    def refresh(self) -> None:
        pass
