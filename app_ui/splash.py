"""启动画面 + 早期应用初始化。

为什么需要它：
    ok-script 在 `OK(config)` 构造阶段会同步初始化设备、OCR（OpenVINO）、18 个任务，
    这期间屏幕上什么都没有，用户看到的就是「双击没反应」。

进度条是**分段推进**的：启动阶段还没有常驻事件循环，所以每次推进都用
processEvents() 手动泵消息 + 小步插值，保证读条是平滑走过去的，而不是跳一下。
"""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

_start_time = time.time()


def elapsed_ms() -> int:
    return int((time.time() - _start_time) * 1000)


def ensure_application() -> QApplication:
    """建 QApplication（DPI 设置必须在实例创建前）。"""

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    application = QApplication.instance()
    if application is None:
        application = QApplication(sys.argv)
    application.setApplicationName("Yu-Mirror 鸣潮")
    application.setApplicationDisplayName("")
    return application


def patch_init_app_config() -> None:
    """让 ok-script 复用我们已经建好的 QApplication。"""

    import importlib

    module = importlib.import_module("ok.ui.qt.util.app")
    original = module.init_app_config

    def init_app_config():
        application = QApplication.instance()
        if application is None:
            return original()
        from ok.ui.qt.common.config import cfg
        from ok.ui.qt.kit import Theme, theme

        locale = module.install_translator(application)
        theme.set_theme(Theme(cfg.theme_mode))
        theme.set_backdrop(cfg.backdrop)
        return application, locale

    module.init_app_config = init_app_config


class _Progress(QWidget):
    """细长的读条：底轨 + 已加载部分。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._value = 0.0

    def value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 26))
        painter.drawRoundedRect(QRectF(self.rect()), 1.5, 1.5)
        width = self.width() * self._value
        if width > 0.5:
            painter.setBrush(QColor("#FAFAFA"))
            painter.drawRoundedRect(QRectF(0, 0, width, self.height()), 1.5, 1.5)
        painter.end()


class Splash(QWidget):
    """启动窗：logo + 名称 + 进度条。"""

    def __init__(self, icon_path: str | None = None):
        super().__init__()
        self.setObjectName("splash")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(360, 168)

        card = QFrame()
        card.setObjectName("splashCard")
        self.card = card
        card.setStyleSheet(
            "QFrame#splashCard { background: #0A0A0B; border: 1px solid #27272A; border-radius: 14px; }")

        logo = QLabel()
        logo.setFixedSize(44, 44)
        logo.setStyleSheet("background: transparent;")
        if icon_path:
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                logo.setPixmap(pixmap.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))
        name = QLabel("Yu-Mirror 鸣潮")
        name.setStyleSheet("color: #FAFAFA; font-size: 15px; font-weight: 600; background: transparent;")
        self.caption = QLabel("正在启动…")
        self.caption.setStyleSheet("color: #A1A1AA; font-size: 12px; background: transparent;")
        self.progress = _Progress()

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(name)
        text.addWidget(self.caption)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(logo)
        head.addLayout(text, 1)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(18)
        layout.addLayout(head)
        layout.addWidget(self.progress)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

    # -- 显示 / 进度 ---------------------------------------------------------

    def show_centered(self) -> None:
        """立即以不透明状态显示。

        启动阶段还没有事件循环（正在同步 import/初始化），动画不会推进，
        窗口会停在 opacity=0 变成「看不见的启动画面」，所以这里不淡入，
        直接 show + 泵一次消息把第一帧画出来。
        """

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            self.move(geometry.center() - QPoint(self.width() // 2, self.height() // 2))
        # 原生窗口创建时可能先在默认位置露一帧：先全透明，定位完再打开
        self.setWindowOpacity(0.0)
        self.progress.set_value(0.02)
        self.show()
        self.setWindowOpacity(1.0)
        self.raise_()

    def set_progress(self, value: float, caption: str | None = None, animate: bool = True) -> None:
        """把读条平滑推到 value（0~1）。

        没有事件循环时靠 processEvents 手动泵，所以必须小步插值 + 睡眠。
        """

        if caption is not None:
            self.caption.setText(caption)
        start = self.progress.value()
        target = max(0.0, min(1.0, float(value)))
        if not animate or target <= start:
            self.progress.set_value(target)
            self._pump(1)
            return

        from PySide6.QtCore import QThread

        steps = 10
        duration_ms = 120
        for index in range(1, steps + 1):
            eased = 1 - (1 - index / steps) ** 2
            self.progress.set_value(start + (target - start) * eased)
            self._pump(1)
            QThread.msleep(max(1, duration_ms // steps))

    def _pump(self, times: int = 1) -> None:
        application = QApplication.instance()
        if application is not None:
            for _ in range(times):
                application.processEvents()

    def close_animated(self) -> None:
        """卡片级淡出（比 windowOpacity 细腻），期间继续泵消息以推进登录卡入场。"""

        if not self.isVisible():
            return
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        steps = 24
        for index in range(1, steps + 1):
            progress = 1 - (1 - index / steps) ** 2
            self.setWindowOpacity(1.0 - progress)
            self._pump(1)
            QThread.msleep(8)
        self.hide()


_splash: Splash | None = None
_splash_shown_at: float = 0.0
_MIN_VISIBLE_MS = 700


def _min_visible_ms() -> int:
    import os

    try:
        return int(os.environ.get("YMP_SPLASH_MIN_MS", _MIN_VISIBLE_MS))
    except ValueError:
        return _MIN_VISIBLE_MS


def show_splash(icon_path: str | None = None) -> Splash:
    global _splash, _splash_shown_at
    _splash = Splash(icon_path)
    _splash.show_centered()
    _splash_shown_at = time.time()
    return _splash


def set_progress(value: float, caption: str | None = None) -> None:
    if _splash is not None:
        _splash.set_progress(value, caption)


def ensure_min_visible() -> None:
    """先把启动画面撑满最短展示时间（期间泵消息，读条/窗口不会假死）。"""

    instance = _splash
    if instance is None:
        return
    from PySide6.QtCore import QThread

    remaining = _min_visible_ms() - int((time.time() - _splash_shown_at) * 1000)
    while remaining > 0:
        instance._pump(1)
        QThread.msleep(16)
        remaining -= 16


def fade_out_now() -> None:
    """立刻开始淡出，并在淡出期间泵消息。

    这样登录卡的入场动画和启动画面的淡出**同时发生**，不会先黑一下再弹窗。
    """

    global _splash
    instance = _splash
    if instance is None:
        return
    _splash = None
    instance._pump(1)
    instance.close_animated()
    instance.deleteLater()


def close_splash() -> None:
    """撑满最短时间再淡出。"""

    ensure_min_visible()
    fade_out_now()
