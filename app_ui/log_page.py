"""日志页：自己实现的实时日志面板。

- 直接 tail `logs/ok-script.log`，不依赖框架的日志页
- 支持关键字过滤、暂停/继续、自动滚到底、打开日志目录
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from ok import og
from ok.ui.qt.kit import Label, theme

from app_ui.motion import AnimatedButton

MAX_LINES = 4000


class LogPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LogPage")
        self._paused = False
        self._offset = 0
        self._lines: list[str] = []

        self.title = Label("日志", "display")
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("过滤关键字…")
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(240)
        self.filter.textChanged.connect(self._render)

        self.pause_button = AnimatedButton("暂停", "pause", "secondary", height=28)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.open_button = AnimatedButton("打开目录", "folder", "ghost", height=28)
        self.open_button.clicked.connect(self._open_folder)

        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.filter)
        top.addWidget(self.pause_button)
        top.addWidget(self.open_button)

        self.view = QPlainTextEdit()
        self.view.setObjectName("logView")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(14)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        theme.install(self)

        self.timer = QTimer(self)
        self.timer.setInterval(600)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._log_file = self._resolve_log_file()
        self._seed()

    # -- file ----------------------------------------------------------------

    def _resolve_log_file(self) -> Path:
        config = getattr(og, "config", None) or {}
        configured = config.get("log_file") or "logs/ok-script.log"
        return Path(configured) if os.path.isabs(configured) else Path.cwd() / configured

    def _seed(self) -> None:
        """启动时把已有内容读进来。"""

        if not self._log_file.is_file():
            return
        try:
            with self._log_file.open("r", encoding="utf-8", errors="replace") as file:
                file.seek(0, os.SEEK_END)
                self._offset = file.tell()
                file.seek(max(0, self._offset - 256 * 1024))
                self._lines = file.read().splitlines()[-MAX_LINES:]
        except OSError:
            return
        self._render()

    def _poll(self) -> None:
        if self._paused or not self._log_file.is_file():
            return
        try:
            size = self._log_file.stat().st_size
            if size < self._offset:
                self._offset = 0
                self._lines = []
            if size == self._offset:
                return
            with self._log_file.open("r", encoding="utf-8", errors="replace") as file:
                file.seek(self._offset)
                chunk = file.read()
                self._offset = file.tell()
        except OSError:
            return
        for line in chunk.splitlines():
            self._lines.append(line)
        if len(self._lines) > MAX_LINES:
            self._lines = self._lines[-MAX_LINES:]
        self._render()

    # -- ui ------------------------------------------------------------------

    def _render(self) -> None:
        needle = self.filter.text().strip().lower()
        lines = [line for line in self._lines if not needle or needle in line.lower()]
        text = "\n".join(lines)
        if self.view.toPlainText() == text:
            return
        scrollbar = self.view.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        self.view.setPlainText(text)
        if at_bottom or needle:
            scrollbar.setValue(scrollbar.maximum())

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.pause_button.set_icon("play" if self._paused else "pause")
        self.pause_button.setText("继续" if self._paused else "暂停")

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._log_file.parent)))

    def dispose(self) -> None:
        self.timer.stop()
