"""登录 → 主界面的「收成一条线，再撑开」转场。

为什么用 overlay 而不是直接拉伸窗口：
    直接对原生窗口做 setGeometry 动画，Windows 每帧都要重排合成，肉眼可见地抖。
    这里用一层无边框、置顶、鼠标穿透的 overlay：
      1. 收拢阶段：把登录卡截图缩成一条 2px 的线
      2. 撑开阶段：主窗口已经在背后就绪，overlay 用一个「洞」（mask 反选）从那条线
         长到整窗大小，主界面像从缝里展开一样露出来
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from ok.ui.qt.kit import theme

_overlay: "MorphOverlay | None" = None
_collapsed_line: QRect | None = None


class MorphOverlay(QWidget):
    def __init__(self, pixmap, rect: QRect):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._pixmap = pixmap
        self._phase = "collapse"
        self._hole: QRect | None = None
        self.setGeometry(rect)
        self._line = rect

    # -- 阶段一：收成一条线 ---------------------------------------------------

    def collapse(self, duration: int = 170) -> QRect:
        start = QRect(self.geometry())
        line_height = 2
        end = QRect(start.x(), start.center().y() - line_height // 2, start.width(), line_height)
        self.show()
        self.raise_()
        self._animate(self, start, end, duration, QEasingCurve.Type.InCubic, blocking=True)
        self._line = QRect(self.geometry())
        self._phase = "idle"
        return self._line

    # -- 阶段二：用 mask 反选撑开 ---------------------------------------------

    def reveal(self, window: QWidget, duration: int = 420) -> None:
        target = window.frameGeometry()
        self.setGeometry(target)
        self._phase = "reveal"
        # 主窗口已经在背后就绪，先把它抬到 overlay 下面但保持可见
        window.raise_()
        self.raise_()

        line_global = self._line
        start = QRect(line_global.x() - target.x(), line_global.y() - target.y(),
                      line_global.width(), max(2, line_global.height()))
        end = QRect(0, 0, target.width(), target.height())

        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def on_value(value):
            t = float(value)
            hole = QRect(
                int(start.x() + (end.x() - start.x()) * t),
                int(start.y() + (end.y() - start.y()) * t),
                int(start.width() + (end.width() - start.width()) * t),
                int(start.height() + (end.height() - start.height()) * t),
            )
            self._hole = hole
            self._apply_mask(hole)

        animation.valueChanged.connect(on_value)

        def finished():
            self.close()
            self.deleteLater()
            global _overlay, _collapsed_line
            _overlay = None
            _collapsed_line = None

        animation.finished.connect(finished)
        animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def _apply_mask(self, hole: QRect) -> None:
        full = QRegion(self.rect())
        hole_region = QRegion(hole)
        self.setMask(full.subtracted(hole_region))
        self.update()

    # -- 绘制 -----------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._phase == "collapse" and self._pixmap is not None:
            radius = max(2, min(14, self.height() // 2))
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), radius, radius)
            painter.setClipPath(path)
            painter.drawPixmap(self.rect(), self._pixmap)
        elif self._phase == "reveal":
            # 洞以外用窗口底色遮住，洞由 mask 挖出（所以这里只画遮罩色）
            painter.fillRect(self.rect(), QColor(theme.color("window")))
        painter.end()

    # -- 工具 -----------------------------------------------------------------

    @staticmethod
    def _animate(target: QWidget, start: QRect, end: QRect, duration: int,
                 easing, blocking: bool = False) -> None:
        animation = QVariantAnimation(target)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(easing)

        def on_value(value):
            t = float(value)
            rect = QRect(
                int(start.x() + (end.x() - start.x()) * t),
                int(start.y() + (end.y() - start.y()) * t),
                int(start.width() + (end.width() - start.width()) * t),
                int(start.height() + (end.height() - start.height()) * t),
            )
            target.setGeometry(rect)

        animation.valueChanged.connect(on_value)
        animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

        if blocking:
            from PySide6.QtCore import QThread

            application = QApplication.instance()
            elapsed = 0
            while elapsed < duration:
                if application is not None:
                    application.processEvents()
                QThread.msleep(12)
                elapsed += 12
            target.setGeometry(end)


def begin_collapse(pixmap, rect: QRect) -> None:
    """登录成功时调用：把登录卡收成一条线，并保留 overlay 等主窗口。"""

    global _overlay, _collapsed_line
    try:
        overlay = MorphOverlay(pixmap, rect)
        _overlay = overlay
        _collapsed_line = overlay.collapse()
    except Exception:  # noqa: BLE001 - 转场失败不能挡住登录
        _overlay = None
        _collapsed_line = None


def has_pending() -> bool:
    return _overlay is not None


def reveal_into(window: QWidget) -> bool:
    """主窗口显示时调用；返回是否接管理场（True 表示不要再做窗口淡入）。"""

    if _overlay is None:
        return False
    try:
        _overlay.reveal(window)
        return True
    except Exception:  # noqa: BLE001
        _overlay.close()
        _overlay.deleteLater()
        return False
