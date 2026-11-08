"""关于页 —— 精简版。

去掉原版里的「相关项目」（其他游戏的 ok-* 列表）和「应用更新」（PyAppify 检查）：
保留应用信息、两行免责声明和两个外链按钮。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ok import og
from ok.ui.qt.kit import Card, Label, theme

from app_ui.motion import AnimatedButton


class AboutTab(QWidget):
    """应用信息页。``update_card`` 保持为 None，框架就不会去检查更新。"""

    update_card = None

    def __init__(self, config, exit_event=None, parent=None):
        super().__init__(parent)
        self.setObjectName("AboutTab")
        self.config = config or {}
        self.exit_event = exit_event

        card = Card()
        icon_label = Label()
        icon_path = self.config.get("gui_icon")
        if icon_path:
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                icon_label.setPixmap(pixmap.scaled(
                    64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        icon_label.setFixedSize(64, 64)

        title = Label(self.config.get("gui_title") or "Yu-Mingchao Pro", "title")
        version = Label(f"v{self.config.get('version', '')}", "secondary")
        head_text = QVBoxLayout()
        head_text.setSpacing(2)
        head_text.addWidget(title)
        head_text.addWidget(version)

        head = QHBoxLayout()
        head.setSpacing(16)
        head.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        head.addLayout(head_text, 1)

        links = self._links()
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        if links.get("github"):
            buttons.addWidget(self._link_button("GitHub", links["github"], "github"))
        if links.get("faq"):
            buttons.addWidget(self._link_button("常见问题", links["faq"], "info"))
        buttons.addStretch(1)

        body = Label(
            "仅供个人学习使用，请勿商用或收费。\n"
            "使用本软件存在账号风险，请自行判断。",
            "secondary", word_wrap=True,
        )

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        layout.addLayout(head)
        layout.addWidget(body)
        layout.addLayout(buttons)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.addWidget(card)
        outer.addStretch(1)
        theme.install(self)

    def _links(self) -> dict:
        raw = self.config.get("links") or {}
        return raw.get("zh_CN") or raw.get("default") or {}

    def _link_button(self, text: str, url: str, icon_name: str) -> AnimatedButton:
        button = AnimatedButton(text, icon_name, "secondary", height=32)
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        return button
