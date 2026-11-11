"""动效基础组件。

Qt 的 QSS 不支持过渡动画（`:hover` 是瞬时变色），所以这里用自绘 + 属性动画实现：

- AnimatedButton：自绘圆角按钮，hover / pressed 颜色按曲线插值（120~160ms）
- NavButton：侧边栏项，选中态也是动画过渡
- fade_in_window / slide_in_window：窗口级淡入 + 轻微上移
- fade_in_widget：控件淡入（QGraphicsOpacityEffect）
- expand / collapse：高度动画（任务行展开收起）

曲线统一用 OutCubic，时长控制在 120~240ms，接近 shadcn / Linear 的手感。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRectF,
    QVariantAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLineEdit, QSizePolicy, QWidget

from ok.ui.qt.kit import theme
from ok.ui.qt.kit.icons import icon as kit_icon

DURATION_HOVER = 190
DURATION_PRESS = 110
DURATION_WINDOW = 200
DURATION_EXPAND = 260

EASING = QEasingCurve.Type.OutQuint
SOFT_EASING = QEasingCurve.Type.OutQuad


def mix(first: QColor, second: QColor, t: float) -> QColor:
    """两色线性插值，t=0 取 first，t=1 取 second。"""

    t = max(0.0, min(1.0, t))
    return QColor(
        round(first.red() + (second.red() - first.red()) * t),
        round(first.green() + (second.green() - first.green()) * t),
        round(first.blue() + (second.blue() - first.blue()) * t),
        round(first.alpha() + (second.alpha() - first.alpha()) * t),
    )


def _token(name: str) -> QColor:
    return QColor(theme.color(name))


class _AnimatedValue:
    """把 0→1 的进度动画封装成一个小对象。"""

    def __init__(self, owner: QWidget, duration: int, easing=None):
        self.owner = owner
        self.value = 0.0
        self._anim = QVariantAnimation(owner)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(easing or EASING)
        self._anim.valueChanged.connect(self._on_value)

    def _on_value(self, value):
        self.value = float(value)
        self.owner.update()

    def to(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.value)
        self._anim.setEndValue(float(target))
        self._anim.start()


class AnimatedButton(QWidget):
    """自绘按钮：hover / pressed 有颜色过渡，支持图标和纯图标模式。"""

    clicked = Signal()

    VARIANTS = ("primary", "secondary", "ghost", "danger", "quiet")

    def __init__(self, text: str = "", icon_name: str | None = None, variant: str = "secondary",
                 height: int = 32, icon_only: bool = False, align: str = "center", parent=None):
        super().__init__(parent)
        if variant not in self.VARIANTS:
            raise ValueError(f"unknown variant: {variant}")
        self._text = text
        self._icon_name = icon_name
        self._variant = variant
        self._icon_only = icon_only
        self._align = align
        self._height = height
        self._radius = 6
        self._enabled = True
        self._hover = _AnimatedValue(self, DURATION_HOVER, QEasingCurve.Type.OutCubic)
        self._press = _AnimatedValue(self, DURATION_PRESS, QEasingCurve.Type.OutCubic)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        theme.changed.connect(self.update)

    # -- api -----------------------------------------------------------------

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.updateGeometry()
            self.update()

    def set_icon(self, icon_name: str | None) -> None:
        self._icon_name = icon_name
        self.update()

    def set_variant(self, variant: str) -> None:
        self._variant = variant
        self.update()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

    def text(self) -> str:
        return self._text

    # -- geometry ------------------------------------------------------------

    def _font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.Medium)
        return font

    def sizeHint(self):
        from PySide6.QtCore import QSize

        if self._icon_only:
            return QSize(self._height, self._height)
        metrics = QFontMetrics(self._font())
        width = metrics.horizontalAdvance(self._text) + 28
        if self._icon_name:
            width += 22
        return QSize(max(width, 72), self._height)

    # -- colors --------------------------------------------------------------

    def _colors(self) -> tuple[QColor, QColor, QColor, QColor | None]:
        """(背景, 背景hover, 文字, 边框)。"""

        text = _token("text")
        secondary = _token("text_secondary")
        control = _token("control")
        hover = _token("control_hover")
        border = _token("border_strong")
        accent = QColor(theme.color("accent"))
        on_accent = QColor(theme.color("on_accent"))
        danger = _token("danger")

        if self._variant == "primary":
            return accent, mix(accent, on_accent, 0.12), on_accent, None
        if self._variant == "danger":
            return QColor(danger.red(), danger.green(), danger.blue(), 30), \
                QColor(danger.red(), danger.green(), danger.blue(), 55), danger, None
        if self._variant == "ghost":
            return QColor(0, 0, 0, 0), hover, secondary, None
        if self._variant == "quiet":
            return QColor(0, 0, 0, 0), hover, text, None
        return control, hover, text, border

    def _current_colors(self):
        base, hover, text, border = self._colors()
        if not self._enabled:
            return (QColor(0, 0, 0, 0), QColor(0, 0, 0, 0), _token("text_disabled"), border)
        t = max(self._hover.value, self._press.value * 0.55)
        # 暗色主题下 hover 变亮、按下再深一点
        background = mix(base, hover, t)
        if self._press.value > 0:
            background = mix(background, _token("text_tertiary"), 0.12 * self._press.value)
        # 文字色也一起过渡：ghost 的次级文字在 hover 时逐渐变亮，不是瞬间换色
        if self._variant in ("ghost", "secondary"):
            text = mix(_token("text_secondary"), _token("text"), t)
        # 描边同样过渡，避免 hover 时边框「跳」一下
        if border is not None:
            border = mix(border, _token("text_tertiary"), t * 0.55)
        return background, hover, text, border

    # -- painting ------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        background, _hover, text_color, border = self._current_colors()

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if background.alpha() > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, self._radius, self._radius)
        if border is not None and self._enabled:
            pen = QPen(border)
            pen.setWidthF(1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, self._radius, self._radius)
        # 键盘焦点环（只有 Tab 导航时才画；鼠标点出来的焦点不画，免得盖在选中块上）
        if self.hasFocus() and getattr(self, "_focus_ring", False):
            ring = QColor(theme.color("accent"))
            ring.setAlpha(110)
            pen = QPen(ring)
            pen.setWidthF(1.4)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), self._radius, self._radius)

        icon_size = 15
        font = self._font()
        painter.setFont(font)
        if self._icon_only:
            if self._icon_name:
                self._draw_icon(painter, text_color, (self.width() - icon_size) / 2,
                                (self.height() - icon_size) / 2, icon_size)
            painter.end()
            return

        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(self._text)
        total = text_width + (icon_size + 6 if self._icon_name else 0)
        x = 10 if self._align == "left" else (self.width() - total) / 2
        if self._icon_name:
            self._draw_icon(painter, text_color, x, (self.height() - icon_size) / 2, icon_size)
            x += icon_size + 6
        painter.setPen(text_color)
        painter.drawText(
            QRectF(x, 0, text_width + 2, self.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._text,
        )
        painter.end()

    def _draw_icon(self, painter: QPainter, color: QColor, x: float, y: float, size: int) -> None:
        pixmap = kit_icon(self._icon_name, color.name(), 1.75).pixmap(size, size)
        painter.drawPixmap(int(x), int(y), pixmap)

    # -- events --------------------------------------------------------------

    def enterEvent(self, event):
        self._hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0.0)
        self._press.to(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._enabled:
            self._press.to(1.0)
            self._focus_ring = False
        super().mousePressEvent(event)

    def focusInEvent(self, event):
        self._focus_ring = event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        )
        super().focusInEvent(event)
        self.update()

    def mouseReleaseEvent(self, event):
        self._press.to(0.0)
        if event.button() == Qt.MouseButton.LeftButton and self._enabled and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SoftLineEdit(QLineEdit):
    """带过渡的输入框：焦点边框从灰渐变到主色，不会「啪」地切换。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._radius = 8.0
        self._focus = _AnimatedValue(self, 170, QEasingCurve.Type.OutCubic)
        self._hover = _AnimatedValue(self, 150, QEasingCurve.Type.OutCubic)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        theme.changed.connect(self.update)

    def focusInEvent(self, event):
        self._focus.to(1.0)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._focus.to(0.0)
        super().focusOutEvent(event)

    def enterEvent(self, event):
        self._hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        focus = self._focus.value
        base = QColor(theme.color("control"))
        base.setAlpha(150 if theme.is_dark() else 210)
        border = mix(_token("border"), _token("accent"), focus)
        border.setAlpha(round(150 + 105 * focus))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(base)
        painter.drawRoundedRect(rect, self._radius, self._radius)
        pen = QPen(border)
        pen.setWidthF(1.0 + 0.6 * focus)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, self._radius, self._radius)
        painter.end()
        super().paintEvent(event)


class NavItem(AnimatedButton):
    """侧边栏项：选中态也是动画过渡。

    ``pill_managed = True`` 时选中背景由 Sidebar 统一画（那样才能在两个项之间
    滑过去），这里只负责文字颜色的渐变。
    """

    def __init__(self, key: str, text: str, icon_name: str, parent=None):
        super().__init__(text, icon_name, "ghost", height=34, align="left", parent=parent)
        self.key = key
        self.pill_managed = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # 侧栏比按钮更"软"一点：hover 210ms、选中 200ms，都是 OutCubic/OutQuint
        self._hover._anim.setDuration(210)
        self._selected = 0.0
        self._sel_anim = QVariantAnimation(self)
        self._sel_anim.setDuration(200)
        self._sel_anim.setEasingCurve(EASING)
        self._sel_anim.valueChanged.connect(self._on_selected)
        self._selected_target = 0.0

    def _on_selected(self, value):
        self._selected = float(value)
        self.update()

    def set_selected(self, selected: bool) -> None:
        target = 1.0 if selected else 0.0
        if abs(target - self._selected_target) < 0.01:
            return
        self._selected_target = target
        self._sel_anim.stop()
        self._sel_anim.setStartValue(self._selected)
        self._sel_anim.setEndValue(target)
        self._sel_anim.start()

    def _colors(self):
        base, hover, text, border = super()._colors()
        selected_bg = _token("control_hover")
        selected_text = _token("text")
        # hover 只用一层很淡的雾（alpha 70 上下），并且越接近「已选中」越弱：
        # 不然鼠标扫过去就是一整块灰板瞬移，看着硬。
        hover_soft = QColor(selected_bg)
        hover_soft.setAlpha(max(0, round((64 if theme.is_dark() else 78) * (1 - self._selected))))
        bg = QColor(0, 0, 0, 0) if self.pill_managed else mix(QColor(0, 0, 0, 0), selected_bg, self._selected)
        color = mix(text, selected_text, self._selected)
        return bg, hover_soft, color, None


# ---------------------------------------------------------------- 窗口/页面


def fade_in_window(window: QWidget, duration: int = DURATION_WINDOW) -> None:
    """窗口淡入。

    只动 windowOpacity，不动窗口位置——移动原生窗口会让 Windows 重排合成，
    在低端显卡上就是明显的抖动，这也是上一版「生硬」的来源之一。

    注意：有 DWM 背板（Mica/Acrylic）时窗口是半透明的，改 windowOpacity
    会让 Windows 把窗口变成分层窗口，毛玻璃直接失效，所以那种情况下
    改成只淡入内容。
    """

    try:
        from ok.ui.qt.kit import theme as _kit_theme
        from ok.ui.qt.kit.theme import Backdrop

        if _kit_theme.backdrop() != Backdrop.NONE:
            fade_in_widget(window, duration)
            return
    except Exception:  # noqa: BLE001
        pass

    window.setWindowOpacity(0.0)
    fade = QPropertyAnimation(window, b"windowOpacity", window)
    fade.setDuration(duration)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setEasingCurve(SOFT_EASING)
    fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def fade_out_window(window: QWidget, duration: int = 160, on_done=None) -> None:
    fade = QPropertyAnimation(window, b"windowOpacity", window)
    fade.setDuration(duration)
    fade.setStartValue(window.windowOpacity())
    fade.setEndValue(0.0)
    fade.setEasingCurve(QEasingCurve.Type.InQuad)
    if on_done is not None:
        fade.finished.connect(on_done)
    fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def slide_in(widget: QWidget, offset: int = 12, duration: int = 180) -> None:
    """横向滑入：只移动控件位置，不重绘整页。

    比 QGraphicsOpacityEffect 便宜得多（后者每帧把整页渲染成位图，
    在 1200×800 上会有肉眼可见的掉帧和文字发虚）。
    """

    end = widget.pos()
    start = type(end)(end.x() + offset, end.y())
    widget.move(start)
    animation = QPropertyAnimation(widget, b"pos", widget)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setEasingCurve(EASING)
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def fade_in_widget(widget: QWidget, duration: int = 160) -> None:
    """控件淡入（只适用于小区域，整页请用 slide_in）。"""

    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    effect.setOpacity(0.0)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(SOFT_EASING)

    def cleanup():
        widget.setGraphicsEffect(None)

    animation.finished.connect(cleanup)
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def animate_height(widget: QWidget, expanded: bool, duration: int = DURATION_EXPAND,
                   fade_content: bool = True) -> None:
    """展开/收起：高度用 QParallelAnimationGroup 同时做内容淡入（Qt 标准动画组）。"""

    if expanded:
        widget.setVisible(True)
        # 先让布局跑一遍，量出真实高度，避免第一帧从 0 突然跳到全高
        widget.adjustSize()
        target = widget.sizeHint().height()
        start = 0
    else:
        target = 0
        start = widget.height()

    widget.setMaximumHeight(start)

    group = QParallelAnimationGroup(widget)
    height_anim = QPropertyAnimation(widget, b"maximumHeight")
    height_anim.setDuration(duration)
    height_anim.setStartValue(start)
    height_anim.setEndValue(target)
    height_anim.setEasingCurve(QEasingCurve.Type.OutQuart)
    group.addAnimation(height_anim)

    effect = None
    if expanded and fade_content:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        effect.setOpacity(0.0)
        fade_anim = QPropertyAnimation(effect, b"opacity")
        fade_anim.setDuration(max(120, duration - 80))
        fade_anim.setStartValue(0.0)
        fade_anim.setEndValue(1.0)
        fade_anim.setEasingCurve(SOFT_EASING)
        group.addAnimation(fade_anim)

    def finished():
        if effect is not None:
            widget.setGraphicsEffect(None)
        if expanded:
            widget.setMaximumHeight(16777215)
        else:
            widget.setVisible(False)

    group.finished.connect(finished)
    group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
