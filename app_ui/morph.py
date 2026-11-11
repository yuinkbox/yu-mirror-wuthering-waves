"""登录 → 主界面的「收成一束光，再撑开」转场。

流程（三段都有连续的缓动，不留硬切）：
    1. 收拢：登录卡像百叶窗一样合上（按原尺寸裁剪，不缩放，文字不会被压糊），
       同时越来越淡，最后收成一条中间的亮光带
    2. 亮起来：t 过一半后中心浮出一团横向拉长的柔光，叠在两端渐隐的光带上，
       看起来是「一束光被收起来」而不是「一张卡片被压扁」
    3. 撑开：主窗口已在背后就绪，overlay 用一个「洞」从那条光带长到整窗大小，
       洞快涨满时 curtain 透明度顺势降到 0，主窗口的玻璃背板平滑接上
"""

from __future__ import annotations

import os

from PySide6.QtCore import QEasingCurve, QPoint, QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QRadialGradient, QRegion)
from PySide6.QtWidgets import QApplication, QWidget

from ok.ui.qt.kit import theme

_overlay: "MorphOverlay | None" = None
_collapsed_line: QRect | None = None

BAR_HEIGHT = 3
_GLOW_SPRITE = None


def _glow_sprite():
    """一张柔和的白色光斑（带 alpha），用缩放贴图代替叠矩形，边缘才不会出色阶。"""

    global _GLOW_SPRITE
    if _GLOW_SPRITE is None:
        size = 192
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        gradient = QRadialGradient(size / 2.0, size / 2.0, size / 2.0)
        gradient.setColorAt(0.00, QColor(255, 255, 255, 255))
        gradient.setColorAt(0.28, QColor(255, 255, 255, 150))
        gradient.setColorAt(0.60, QColor(255, 255, 255, 46))
        gradient.setColorAt(1.00, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(0, 0, size, size)
        painter.end()
        _GLOW_SPRITE = image
    return _GLOW_SPRITE


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
        self._curtain = 1.0
        self._glow = 0.0
        self._fade = 1.0
        self._inner = QRect(self.rect())
        self.setGeometry(rect)
        self._line = rect

    # -- 阶段一：收成一条光带 -------------------------------------------------

    def collapse(self, duration: int = 300) -> QRect:
        """把登录卡收成一束光。

        注意窗口本身**不缩小**——如果让窗口跟着变矮到 3px，光晕就没有地方画了。
        窗口保持卡片大小，只让里面那束光由整张卡收成一条，周围留白给光晕。
        """

        from PySide6.QtCore import QThread

        duration = int(os.environ.get("YMP_MORPH_MS", duration) or duration)
        width, height = self.width(), self.height()
        bar_width = max(80, int(width * 0.68))
        start = QRect(0, 0, width, height)
        end = QRect(
            (width - bar_width) // 2,
            height // 2 - BAR_HEIGHT // 2,
            bar_width,
            BAR_HEIGHT,
        )
        self.show()
        self.raise_()

        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def on_value(value):
            t = float(value)
            self._inner = QRect(
                int(start.x() + (end.x() - start.x()) * t),
                int(start.y() + (end.y() - start.y()) * t),
                max(1, int(start.width() + (end.width() - start.width()) * t)),
                max(1, int(start.height() + (end.height() - start.height()) * t)),
            )
            # 光在最后 40% 最盛，收拢完成时光带是亮的
            self._glow = min(1.0, max(0.0, (t - 0.50) / 0.40))
            # 卡片一边收一边淡出，最后只剩光，不会看到一块被压扁的白色方块
            self._fade = 1.0 if t < 0.45 else max(0.0, 1.0 - (t - 0.45) / 0.40)
            self.update()

        animation.valueChanged.connect(on_value)
        animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

        application = QApplication.instance()
        elapsed = 0
        while elapsed < duration:
            if application is not None:
                application.processEvents()
            QThread.msleep(10)
            elapsed += 10

        self._inner = QRect(end)
        self._glow = 1.0
        self._line = QRect(self.mapToGlobal(end.topLeft()), end.size())
        self._phase = "idle"
        self.update()
        return self._line

    # -- 阶段二：用 mask 反选撑开 ---------------------------------------------

    def reveal(self, window: QWidget, duration: int = 520) -> None:
        target = window.frameGeometry()
        self.setGeometry(target)
        self._phase = "reveal"

        # 主窗口已经在背后就绪，先把它抬到 overlay 下面但保持可见
        window.raise_()
        self.raise_()

        line_global = self._line
        start = QRect(
            line_global.x() - target.x(),
            line_global.y() - target.y(),
            max(BAR_HEIGHT, line_global.width()),
            max(BAR_HEIGHT, line_global.height()),
        )
        end = QRect(0, 0, target.width(), target.height())

        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        # OutQuint：起手快、尾巴很长，撑到最后是「慢慢铺满」而不是撞到墙上
        animation.setEasingCurve(QEasingCurve.Type.OutQuint)

        def on_value(value):
            t = float(value)
            hole = QRect(
                int(start.x() + (end.x() - start.x()) * t),
                int(start.y() + (end.y() - start.y()) * t),
                int(start.width() + (end.width() - start.width()) * t),
                int(start.height() + (end.height() - start.height()) * t),
            )
            self._hole = hole
            # 洞快涨满时，把遮蔽层顺势淡掉，主窗口的玻璃是「化」进来的
            fade_from = 0.68
            self._curtain = 1.0 if t <= fade_from else max(0.0, 1.0 - (t - fade_from) / (1.0 - fade_from))
            self._apply_mask(hole)

        animation.valueChanged.connect(on_value)

        def finished():
            # 先藏再关：close() 时 Qt 会把最后一帧（mask 反选出的那条缝）再刷一次
            self.hide()
            self.setMask(QRegion())
            self.close()
            self.deleteLater()
            global _overlay, _collapsed_line
            _overlay = None
            _collapsed_line = None

        animation.finished.connect(finished)
        animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def _apply_mask(self, hole: QRect) -> None:
        full = QRegion(self.rect())
        # 洞用圆角，撑开时四个角是圆的，不是刀切出来的直角
        path = QPainterPath()
        path.addRoundedRect(QRectF(hole), 12, 12)
        hole_region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(full.subtracted(hole_region))
        self.update()

    # -- 绘制 -----------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._phase in ("collapse", "idle"):
            # 光束每帧都在缩小，先把整层清干净，避免上一帧的光晕残留
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            self._paint_beam(painter)
        elif self._phase == "reveal":
            self._paint_curtain(painter)
        else:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.end()

    def _paint_beam(self, painter: QPainter) -> None:
        """收拢中的卡片 + 一束越收越亮的光。"""

        glow = max(0.0, min(1.0, self._glow))
        rect = QRectF(self._inner)
        height = max(1.0, rect.height())
        radius = min(BAR_HEIGHT if height <= BAR_HEIGHT * 2 else 14, height / 2)

        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        painter.save()
        painter.setClipPath(path)
        # 关键：不缩放截图。缩放会把卡片文字压成一团糊，像被挤扁。
        # 改成「百叶窗」：截图按原尺寸居中画，窗口变矮时只露出中间一条缝。
        # 越接近收拢完成，卡片本身越淡，最后只剩纯光。
        if self._pixmap is not None and not self._pixmap.isNull() and glow < 0.999:
            painter.setOpacity(max(0.0, min(1.0, self._fade)))
            x = int(rect.center().x() - self._pixmap.width() / 2)
            y = int(rect.center().y() - self._pixmap.height() / 2)
            painter.drawPixmap(x, y, self._pixmap)
            painter.setOpacity(1.0)
        painter.restore()

        if glow <= 0.01:
            return

        # 横向两端渐隐、中间最亮 —— 这是「一束光」而不是一根灰线的关键
        core = QLinearGradient(rect.left(), 0, rect.right(), 0)
        core.setColorAt(0.0, QColor(255, 255, 255, 0))
        core.setColorAt(0.18, QColor(255, 255, 255, int(150 * glow)))
        core.setColorAt(0.5, QColor(255, 255, 255, int(235 * glow)))
        core.setColorAt(0.82, QColor(255, 255, 255, int(150 * glow)))
        core.setColorAt(1.0, QColor(255, 255, 255, 0))

        # 中心的辉光：贴一张柔和的径向光斑，横向拉长成「一束光」的形状。
        # 用几层矩形叠会看到横向色阶，贴图是平滑的。
        bloom_w = rect.width() * 0.62 + 40.0
        bloom_h = 10.0 + 26.0 * glow
        bloom = QRectF(
            rect.center().x() - bloom_w / 2.0,
            rect.center().y() - bloom_h,
            bloom_w,
            bloom_h * 2.0,
        )
        painter.save()
        painter.setOpacity(0.85 * glow)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(bloom, _glow_sprite())
        painter.restore()

        # 光带本体压在最上面
        core_path = QPainterPath()
        core_path.addRoundedRect(rect, radius, radius)
        painter.fillPath(core_path, QBrush(core))

    def _paint_curtain(self, painter: QPainter) -> None:
        """遮罩层：洞以外的地方盖住主窗口，让「撑开」这件事看得见。"""

        base = QColor(theme.color("window"))
        alpha = int(255 * max(0.0, min(1.0, self._curtain)))
        if alpha <= 0:
            return
        base.setAlpha(alpha)
        painter.fillRect(self.rect(), base)

        # 洞的边沿加一圈很淡的光，撑开时像有一道光在边界上走
        if self._hole is not None and self._hole.width() > 8 and self._hole.height() > 8:
            path = QPainterPath()
            path.addRoundedRect(QRectF(self._hole).adjusted(1.0, 1.0, -1.0, -1.0), 11, 11)
            pen = painter.pen()
            pen.setWidthF(1.4)
            pen.setColor(QColor(255, 255, 255, int(70 * self._curtain)))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

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
                max(1, int(start.width() + (end.width() - start.width()) * t)),
                max(1, int(start.height() + (end.height() - start.height()) * t)),
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
                QThread.msleep(10)
                elapsed += 10
            target.setGeometry(end)


def begin_collapse(pixmap, rect: QRect) -> None:
    """登录成功时调用：把登录卡收成一条光带，并保留 overlay 等主窗口。"""

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

