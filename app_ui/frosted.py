"""磨砂玻璃（frosted glass）效果。

Qt Widgets 没有原生的背景模糊（Windows 10 也没有 Mica/Acrylic），所以自己做：

  1. 启动最早期（还没显示任何窗口）截一张全屏快照，缩放两次做高斯近似模糊
  2. 登录卡把这张模糊图裁出自己所在区域当「玻璃底」
  3. 上面叠一层半透明色（浅色主题偏白、深色偏黑）+ 1px 高光边

因为是静态快照，窗口移动/背后内容变化时不会实时更新——对一个登录框来说足够，
而且不会被 Windows 窗口合成拖慢。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame

from ok.ui.qt.kit import theme

_snapshot: QPixmap | None = None


def capture_screen_snapshot() -> None:
    """在显示任何窗口之前截一张屏幕，作为玻璃底。"""

    global _snapshot
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return
    geometry = screen.geometry()
    pixmap = screen.grabWindow(0, 0, 0, geometry.width(), geometry.height())
    if not pixmap.isNull():
        _snapshot = pixmap


def snapshot() -> QPixmap | None:
    return _snapshot


def _blur(pixmap: QPixmap, strength: int = 18) -> QPixmap:
    """两次平滑缩放近似高斯模糊，比逐像素卷积快得多。"""

    if pixmap.isNull():
        return pixmap
    width = max(1, pixmap.width() // strength)
    height = max(1, pixmap.height() // strength)
    small = pixmap.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    return small.scaled(pixmap.width(), pixmap.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


_blurred_cache: dict[tuple[int, int, int, int], QPixmap] = {}


def backdrop_for(rect: QRect) -> QPixmap | None:
    """取屏幕快照里对应区域并模糊（按尺寸缓存）。"""

    if _snapshot is None:
        return None
    key = (rect.x(), rect.y(), rect.width(), rect.height())
    cached = _blurred_cache.get(key)
    if cached is not None:
        return cached
    crop = _snapshot.copy(rect)
    if crop.isNull():
        return None
    blurred = _blur(crop)
    _blurred_cache[key] = blurred
    return blurred


class FrostedCard(QFrame):
    """磨砂玻璃卡片：模糊背景 + 半透明着色 + 细高光边。"""

    def __init__(self, radius: int = 14, parent=None):
        super().__init__(parent)
        self.setObjectName("frostedCard")
        self._radius = radius
        self._backdrop: QPixmap | None = None
        self._tint_strength = 0.80
        # 让 QSS 不再画背景，我们自己画
        self.setStyleSheet("QFrame#frostedCard { background: transparent; border: 0; }")
        theme.changed.connect(self.update)

    def set_backdrop(self, pixmap: QPixmap | None) -> None:
        self._backdrop = pixmap
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), self._radius, self._radius)
        painter.setClipPath(path)

        dark = theme.is_dark()
        if self._backdrop is not None and not self._backdrop.isNull():
            painter.drawPixmap(0, 0, self._backdrop)
        else:
            painter.fillRect(rect, QColor(20, 20, 26) if dark else QColor(240, 240, 244))

        tint = QColor(10, 10, 12) if dark else QColor(255, 255, 255)
        tint.setAlphaF(self._tint_strength)
        painter.fillRect(rect, tint)

        # 顶部一道很淡的光，让玻璃有厚度
        sheen = QLinearGradient(0, 0, 0, rect.height())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 26 if dark else 60))
        sheen.setColorAt(0.35, QColor(255, 255, 255, 0))
        painter.fillRect(rect, sheen)

        painter.setClipping(False)
        border = QColor(255, 255, 255, 26) if dark else QColor(255, 255, 255, 200)
        pen = painter.pen()
        pen.setColor(border)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()
