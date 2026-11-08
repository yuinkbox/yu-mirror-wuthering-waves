"""设置页：外观 + 框架的全局配置项。

全局配置（基本选项 / 游戏快捷键 / 角色配置 / 月卡 / 通知）仍然由
`ConfigSection` 渲染——配置模型是引擎的，没必须重写；页面结构是本项目的。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ok.ui.qt.common.config import cfg as ui_config
from ok.ui.qt.kit import Label, TabStrip, theme
from ok.ui.qt.kit.theme import Theme
from ok.ui.qt.settings.config_section import ConfigSection


class SettingsPage(QWidget):
    def __init__(self, global_config, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsPage")
        self.global_config = global_config

        self.title = Label("设置", "display")

        # ---- 外观
        appearance = QWidget()
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setContentsMargins(0, 0, 0, 0)
        appearance_layout.setSpacing(8)
        appearance_title = Label("外观", "subtitle")
        appearance_layout.addWidget(appearance_title)
        current = ui_config.theme_mode if ui_config.theme_mode in ("auto", "light", "dark") else "auto"
        self.theme_tabs = TabStrip()
        self.theme_tabs.add_tab("auto", "跟随系统")
        self.theme_tabs.add_tab("light", "浅色")
        self.theme_tabs.add_tab("dark", "深色")
        self.theme_tabs.current_changed.connect(self._set_theme)
        self.theme_tabs.set_current(current)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(Label("主题", "body"))
        row.addStretch(1)
        row.addWidget(self.theme_tabs)
        appearance_layout.addLayout(row)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(24)
        column.addWidget(appearance)
        for _name, config, option in self.global_config.get_all_visible_configs() if self.global_config else []:
            if option is None:
                continue
            column.addWidget(ConfigSection(config, option, title_role="subtitle"))
        column.addStretch(1)

        contents = QWidget()
        contents.setObjectName("scrollContents")
        contents.setLayout(column)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(contents)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(14)
        layout.addWidget(self.title)
        layout.addWidget(scroll, 1)
        theme.install(self)

    def _set_theme(self, key: str) -> None:
        ui_config.set_theme_mode(key)
        theme.set_theme(Theme(key))

    def dispose(self) -> None:
        pass
