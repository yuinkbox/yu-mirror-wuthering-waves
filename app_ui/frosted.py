"""磨砂玻璃（frosted glass）效果。

Qt Widgets 没有原生的背景模糊（Windows 10 也没有 Mica/Acrylic），所以自己做：

  1. 启动最早期（还没显示任何窗口）截一张全屏快照，缩放两次做高斯近似模糊
  2. 登录卡把这张模糊图裁出自己所在区域当「玻璃底」
  3. 上面叠一层半透明色（浅色主题偏白、深色偏黑）+ 1px 高光边

因为是静态快照，窗口移动/背后内容变化时不会实时更新——对一个登录框来说足够，
而且不会被 Windows 窗口合成拖慢。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

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


def refresh_snapshot() -> None:
    """重新截屏：窗口要跟着背后画面实时变化时调用（会清掉模糊缓存）。"""

    _blurred_cache.clear()
    capture_screen_snapshot()


def _blur(pixmap: QPixmap, strength: int = 30) -> QPixmap:
    """两次平滑缩放近似高斯模糊，比逐像素卷积快得多。"""

    if pixmap.isNull():
        return pixmap
    width = max(1, pixmap.width() // strength)
    height = max(1, pixmap.height() // strength)
    small = pixmap.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    return small.scaled(pixmap.width(), pixmap.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


# --- 只要一小块屏幕的快速抓取 ---------------------------------------------
# QScreen.grabWindow(0) 每次都抓整块屏幕（2560x1440 约 20~40ms），放到 300ms 的
# 定时器里会把主线程顶出卡顿。这里用 GDI 的 StretchBlt 只抓需要的那一小块，
# 耗时 1~3ms，足够每帧刷新。

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _grab_region(rect: QRect) -> QImage | None:
    """用 GDI 抓屏幕上的一个矩形（只拷这一块），返回 QImage。"""

    if rect.width() <= 0 or rect.height() <= 0:
        return None
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    screen_dc = user32.GetDC(None)
    if not screen_dc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bits = ctypes.c_void_p()
    info = _BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.bmiHeader.biWidth = rect.width()
    info.bmiHeader.biHeight = -rect.height()  # 负数 = 自上而下
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0  # BI_RGB
    bitmap = gdi32.CreateDIBSection(mem_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    if not bitmap:
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)
        return None
    old = gdi32.SelectObject(mem_dc, bitmap)
    try:
        gdi32.SetStretchBltMode(mem_dc, 4)  # HALFTONE
        gdi32.StretchBlt(
            mem_dc, 0, 0, rect.width(), rect.height(),
            screen_dc, rect.x(), rect.y(), rect.width(), rect.height(), 0x00CC0020,  # SRCCOPY
        )
        buffer = ctypes.string_at(bits, rect.width() * rect.height() * 4)
        image = QImage(buffer, rect.width(), rect.height(), QImage.Format.Format_RGB32).copy()
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)
    return image


def blurred_region(rect: QRect, strength: int = 30) -> QPixmap | None:
    """抓屏幕上一块并当场模糊，给「实时磨砂」用。"""

    image = _grab_region(rect)
    if image is None or image.isNull():
        return None
    width = max(1, image.width() // strength)
    height = max(1, image.height() // strength)
    small = image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    big = small.scaled(image.width(), image.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    return QPixmap.fromImage(big)


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
        self._tint_strength = 0.42
        self._fallback_alpha = 74
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
        # radius=0 表示「铺满整个窗口」，此时改用 DWM 自己的圆角半径（8），
        # 这样边框和窗口的圆角能对齐，不会出现直角描边卡在圆角上。
        radius = self._radius if self._radius > 0 else 8.0
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.setClipPath(path)

        dark = theme.is_dark()
        if self._backdrop is not None and not self._backdrop.isNull():
            painter.drawPixmap(0, 0, self._backdrop)
        else:
            # 没有自绘底图时不能铺不透明色——否则 DWM 的 Acrylic 背板就被
            # 一整块实色挡住，看起来是「固定的磨砂」而不是真的透。
            fallback = QColor(20, 20, 26) if dark else QColor(246, 246, 249)
            fallback.setAlpha(self._fallback_alpha)
            painter.fillRect(rect, fallback)

        tint = QColor(10, 10, 12) if dark else QColor(255, 255, 255)
        tint.setAlphaF(self._tint_strength)
        painter.fillRect(rect, tint)

        # 顶部一道很淡的光，让玻璃有厚度
        sheen = QLinearGradient(0, 0, 0, rect.height())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 42 if dark else 92))
        sheen.setColorAt(0.28, QColor(255, 255, 255, 0))
        painter.fillRect(rect, sheen)

        # 底部一点内阴影，玻璃才有体积感，不至于像一张平色卡片
        depth = QLinearGradient(0, rect.height() * 0.55, 0, rect.height())
        depth.setColorAt(0.0, QColor(0, 0, 0, 0))
        depth.setColorAt(1.0, QColor(0, 0, 0, 26 if dark else 14))
        painter.fillRect(rect, depth)

        painter.setClipping(False)
        # 外圈：先画一条极淡的暗边压住边缘，再叠一条白的发丝边，
        # 视觉上就是「一块被磨过的玻璃」，而不是贴纸。
        pen = painter.pen()
        pen.setStyle(Qt.PenStyle.SolidLine)
        pen.setWidthF(1.0)
        pen.setColor(QColor(0, 0, 0, 40) if dark else QColor(15, 20, 30, 26))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        inner = QPainterPath()
        inner.addRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), max(radius - 1, 0), max(radius - 1, 0))
        pen.setColor(QColor(255, 255, 255, 60) if dark else QColor(255, 255, 255, 190))
        painter.setPen(pen)
        painter.drawPath(inner)
        painter.end()
