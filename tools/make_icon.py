"""生成 Yu-Mingchao Pro 的极简图标：黑色圆角方块 + 一条白色波浪线。

用 Qt 的矢量路径绘制（和界面同一套渲染，边缘干净），再导出 PNG/ICO。

用法：
    .\\.venv\\Scripts\\python.exe tools\\make_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parent.parent
ICON_PNG = ROOT / "icons" / "icon.png"
ICON_ICO = ROOT / "icons" / "icon.ico"

SIZE = 512
BACKGROUND = "#09090B"
MARK = "#FAFAFA"
RADIUS = 0.235        # 圆角（相对边长）
STROKE = 0.072        # 线宽（相对边长）
AMPLITUDE = 0.105
CYCLES = 1.15
SPAN = 0.62           # 波浪长度（相对边长）


def _wave_path(size: int) -> QPainterPath:
    path = QPainterPath()
    left = size * (0.5 - SPAN / 2)
    right = size * (0.5 + SPAN / 2)
    mid = size * 0.5
    amplitude = size * AMPLITUDE
    steps = 320
    for index in range(steps + 1):
        t = index / steps
        x = left + (right - left) * t
        y = mid + math.sin(t * math.pi * 2 * CYCLES - math.pi / 2) * amplitude
        if index == 0:
            path.moveTo(QPointF(x, y))
        else:
            path.lineTo(QPointF(x, y))
    return path


def build(size: int = SIZE) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(BACKGROUND))
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * RADIUS, size * RADIUS)

    pen = QPen(QColor(MARK))
    pen.setWidthF(size * STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_wave_path(size))
    painter.end()
    return image


def main() -> None:
    ICON_PNG.parent.mkdir(parents=True, exist_ok=True)
    image = build(SIZE)
    image.save(str(ICON_PNG))

    # ICO 由 PNG 缩放生成（Pillow）
    from PIL import Image

    png = Image.open(ICON_PNG).convert("RGBA")
    png.save(ICON_ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {ICON_PNG}")
    print(f"wrote {ICON_ICO}")


if __name__ == "__main__":
    main()
