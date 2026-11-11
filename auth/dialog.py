"""本地账号登录对话框（shadcn 风格，无系统边框）。

入场动效（「水感」）由三件事叠加：
  1. 卡片从 96.5% 缩放浮起到 100%（OutQuint，300ms）
  2. 卡片透明度 0 → 1（同步，250ms）
  3. 一道高光从左上扫到右下（700ms，减速），像水面反光

实现要点：卡片不交给布局管理（手动 setGeometry），才能安全地做几何动画；
窗口本身不淡入、也不移动，避免 Windows 合成抖动。
"""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ok.ui.qt.kit import Label, theme

from app_ui.frosted import FrostedCard, backdrop_for
from app_ui.motion import AnimatedButton, SoftLineEdit
from auth.store import AuthStore, session

CARD_WIDTH = 400
MARGIN = 0


def _duration(base: int) -> int:
    """调试用：YMP_ANIM_SLOW=6 可把入场动效放慢 6 倍，方便观察/录屏。"""

    import os

    try:
        factor = float(os.environ.get("YMP_ANIM_SLOW", "1"))
    except ValueError:
        factor = 1.0
    return max(1, int(base * factor))


class _Sheen(QWidget):
    """高光扫过：一条半透明白色斜带。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._progress = -0.6

    def set_progress(self, value: float) -> None:
        self._progress = float(value)
        self.update()

    def paintEvent(self, event):
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = self.width()
        band = max(60.0, width * 0.42)
        x = self._progress * (width + band * 2) - band
        gradient = QLinearGradient(x, 0, x + band, self.height() * 0.35)
        gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
        gradient.setColorAt(0.5, QColor(255, 255, 255, 44))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(self.rect(), gradient)
        painter.end()


class AuthDialog(QDialog):
    """登录 / 首次创建账号。"""

    def __init__(self, store: AuthStore, icon_path: str | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self._drag_offset: QPoint | None = None
        self._setup_mode = not store.has_users()
        self._animated_in = False
        self._backdrop = None
        self._glass_ready = False
        # 玻璃底每 300ms 重新截一次，背后内容变了也能跟上（这是它和「开机那张死图」的区别）
        self._backdrop_ready = False
        self._backdrop_timer = QTimer(self)
        self._backdrop_timer.setInterval(90)
        self._backdrop_timer.timeout.connect(self._tick_backdrop)

        self.setObjectName("authDialog")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        import os as _os
        if _os.environ.get("YMP_DIALOG_TOPMOST") == "1":
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        # 注意：这里千万不能再开 WA_TranslucentBackground——那会让 Qt 走
        # WS_EX_LAYERED 分层窗口，分层窗口上 DWM 的亚克力背板不会渲染，
        # 结果就是「一点玻璃感都没有」。背板由 paintEvent 的 Source 填充提供。
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        palette = self.palette()
        palette.setColor(palette.ColorRole.Window, QColor(0, 0, 0, 0))
        self.setPalette(palette)
        # 原生窗口创建可能先出现在默认位置；先整体不可见，定位完成后再打开
        self.setWindowOpacity(0.0)
        self.setModal(True)
        self.setFixedWidth(CARD_WIDTH + MARGIN * 2)

        self.card = FrostedCard(0, self)
        self.card.setFixedWidth(CARD_WIDTH)
        self.card.setGeometry(0, 0, CARD_WIDTH, 0)

        logo = Label()
        logo.setFixedSize(44, 44)
        if icon_path:
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                logo.setPixmap(pixmap.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))
        self.title = Label("登录", "title")
        self.subtitle = Label("", "secondary")
        self.subtitle.setWordWrap(True)

        self.username = SoftLineEdit()
        self.username.setPlaceholderText("账号")
        self.username.setText(store.last_username())
        self.password = SoftLineEdit()
        self.password.setPlaceholderText("密码")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = SoftLineEdit()
        self.confirm.setPlaceholderText("再次输入密码")
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm.setVisible(self._setup_mode)
        for field in (self.username, self.password, self.confirm):
            field.setObjectName("softInput")
            field.setMinimumHeight(38)
        # 用户名回车直接跳到密码，避免「账号填完一按回车就报错」
        self.username.returnPressed.connect(lambda: self.password.setFocus())
        self.password.returnPressed.connect(self._submit)
        self.confirm.returnPressed.connect(self._submit)

        self.error = Label("", "secondary")
        self.error.setObjectName("authError")
        self.error.setVisible(False)

        self.submit = AnimatedButton("登录", None, "primary", height=38)
        self.submit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.submit.clicked.connect(self._submit)

        self.switch = AnimatedButton("", None, "quiet", height=30)
        self.switch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.switch.clicked.connect(self._toggle_mode)

        self.close_button = AnimatedButton(None, "x", "ghost", height=28, icon_only=True)
        self.close_button.setToolTip("关闭")
        self.close_button.clicked.connect(self.reject)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(logo)
        top.addStretch(1)
        top.addWidget(self.close_button)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(12)
        layout.addLayout(top)
        layout.addSpacing(4)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)
        layout.addSpacing(6)
        layout.addWidget(self.username)
        layout.addWidget(self.password)
        layout.addWidget(self.confirm)
        layout.addWidget(self.error)
        layout.addSpacing(2)
        layout.addWidget(self.submit)
        layout.addWidget(self.switch)

        self.sheen = _Sheen(self.card)
        self.sheen.hide()

        theme.install(self)
        self._apply_mode()
        self._relayout()
        if self.username.text():
            self.password.setFocus()
        else:
            self.username.setFocus()
        self._maybe_autologin()

    def _maybe_autologin(self) -> None:
        """调试/自动化用：设置 YMP_AUTOLOGIN_USER / YMP_AUTOLOGIN_PASS 自动登录。"""

        import os

        username = os.environ.get("YMP_AUTOLOGIN_USER")
        password = os.environ.get("YMP_AUTOLOGIN_PASS")
        if not username or not password:
            return
        from PySide6.QtCore import QTimer

        self.username.setText(username)
        self.password.setText(password)
        QTimer.singleShot(600, self._submit)

    # -- 布局 ----------------------------------------------------------------

    def _relayout(self) -> None:
        """卡片手动摆位：对话框高度跟着内容走。"""

        self.card.adjustSize()
        height = self.card.sizeHint().height()
        self.card.setFixedHeight(height)
        self.setFixedSize(CARD_WIDTH + MARGIN * 2, height + MARGIN * 2)
        self.card.setGeometry(MARGIN, MARGIN, CARD_WIDTH, height)
        self.sheen.setGeometry(self.card.rect())
        # 必须在 show 之前就摆到屏幕中央：否则第一帧会画在窗口默认位置（左上角），
        # 然后再跳到中间，就是肉眼看到的「左上角闪一下」。
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center() - QPoint(self.width() // 2, self.height() // 2))

    # -- 入场动效：缩放 + 淡入 + 高光 ----------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._debug_grab()
        # 先只裁玻璃底，把入场动画推迟一个事件循环：
        # 这样第一帧一定是「窗口不可见 + 卡片透明」，等系统把它合成出来之后
        # 再打开窗口做动画，不会出现「先闪一块底色再出现登录窗」。
        from PySide6.QtCore import QTimer as _QTimer

        self._backdrop_ready = True
        # 挂 DWM 背板（和主窗口同一套）：真·实时玻璃，不需要自己截屏
        self._enable_glass()
        # 启动画面在这一刻开始淡出，和登录卡入场交叉进行
        try:
            from app_ui import splash

            splash.fade_out_now()
        except Exception:  # noqa: BLE001
            pass
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._start_entrance)

    def _debug_grab(self) -> None:
        """YMP_DEBUG_FRAMES=1 时，把登录窗出现后几百毫秒的屏幕帧存下来。"""

        import os

        if os.environ.get("YMP_DEBUG_FRAMES") != "1":
            return
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        os.makedirs("logs/frames", exist_ok=True)

        def shot(tag: str) -> None:
            pixmap = screen.grabWindow(0)
            if not pixmap.isNull():
                pixmap.save(f"logs/frames/login_{tag}.png")

        # 链式定时器：每次抓完再排下一帧，避免所有回调挤进同一个同步循环
        def chain(index: int) -> None:
            if index >= 12:
                return
            shot(f"{index:02d}")
            QTimer.singleShot(70, lambda: chain(index + 1))

        chain(0)

    def _raise_to_front(self) -> None:
        """把登录窗压到别的软件上面。

        启动画面刚消失、主窗口还没建，这一瞬间我们的进程没有前台权限，
        Qt 的 activateWindow() 常常抢不过别的窗口。Win32 允许「置顶再撤回」
        绕过前台锁，所以这里先 SetWindowPos(HWND_TOPMOST)，再还原成普通层级。
        """

        self.raise_()
        self.activateWindow()

        import sys

        if sys.platform != "win32":
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            flags = 0x0002 | 0x0001 | 0x0040  # NOMOVE | NOSIZE | SHOWWINDOW
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)  # HWND_TOPMOST
            self.raise_()
            self.activateWindow()
            user32.SetForegroundWindow(hwnd)
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)  # HWND_NOTOPMOST
        except Exception:  # noqa: BLE001 - 拿不到前台就让系统自己决定
            import logging

            logging.getLogger(__name__).debug("raise login dialog failed", exc_info=True)

    def _start_entrance(self) -> None:
        self._debug_grab()
        if self._animated_in:
            return
        self._animated_in = True

        self._raise_to_front()

        # 只用窗口级淡入：子控件的 QGraphicsOpacityEffect 在部分机器上会让
        # 窗口首帧合成异常（白块/不显示），这里全部不用。
        self.setWindowOpacity(0.0)

        final = self.card.geometry()
        scale = 0.955
        width = int(final.width() * scale)
        height = int(final.height() * scale)
        start = QRect(
            final.x() + (final.width() - width) // 2,
            final.y() + (final.height() - height) // 2 + 3,
            width,
            height,
        )

        group = QParallelAnimationGroup(self)

        grow = QPropertyAnimation(self.card, b"geometry", self)
        grow.setDuration(_duration(460))
        grow.setStartValue(start)
        grow.setEndValue(final)
        grow.setEasingCurve(QEasingCurve.Type.OutQuint)
        group.addAnimation(grow)

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(_duration(420))
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(fade)

        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

        # 高光等玻璃基本浮上来之后再扫过，避免和淡入抢注意力
        from PySide6.QtCore import QTimer

        QTimer.singleShot(int(_duration(520) * 0.45), self._play_sheen)

    def _update_backdrop(self, refresh: bool = True) -> None:
        """占位：玻璃底已经交给 DWM 的 Acrylic 背板，这里不再自绘。

        早期版本会在每次移动/点击时截屏 + 模糊再画进卡片，结果是「一碰就
        变成一张固定的磨砂图」——因为把自己的窗口也截进去了。现在整条链路
        都不用了，保留方法名只是为了别处旧调用不会炸。
        """

        self.card.set_backdrop(None)

    def _tick_backdrop(self) -> None:
        return

    def hideEvent(self, event):
        self._backdrop_timer.stop()
        super().hideEvent(event)

    def _enable_glass(self) -> None:
        """给这个无边框对话框挂上 Acrylic 背板（Windows 11 22H2+）。"""

        import sys

        if sys.platform != "win32" or self._glass_ready:
            return
        self._glass_ready = True
        try:
            import ctypes

            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            dwmapi = ctypes.windll.dwmapi

            # 关键一步：把无边框窗口的「标题栏/可调边框」样式加回去。
            # DWM 只对「像普通窗口」的窗口套用自己的圆角 + 亚克力裁剪；
            # 纯 FramelessWindowHint 的窗口它不裁，四角就会露出整块矩形背板。
            GWL_STYLE = -16
            WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
            WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00010000, 0x00080000
            user32.GetWindowLongW.restype = ctypes.c_long
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            user32.SetWindowLongW(
                hwnd,
                GWL_STYLE,
                style | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU,
            )

            acrylic = ctypes.c_int(3)  # DWMSBT_TRANSIENTWINDOW = Acrylic
            dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(acrylic), ctypes.sizeof(acrylic))

            class _Margins(ctypes.Structure):
                _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                            ("top", ctypes.c_int), ("bottom", ctypes.c_int)]

            # 从底边往下扩，把整个客户区交给 DWM 的玻璃层；用 -1 会让 DWM 在
            # 我们半透明的标题区后面多画一排系统按钮。
            margins = _Margins(0, 0, 0, 32767)
            dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))

            corner = ctypes.c_int(2)  # DWMWCP_ROUND
            dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
            color = ctypes.c_int(-2)  # DWMWA_COLOR_NONE，不要系统描边
            dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(color), ctypes.sizeof(color))

            # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0004 | 0x0020)

            # 关键：分层窗口（WS_EX_LAYERED）上 DWM 不会渲染亚克力背板。
            # Qt 在无边框窗口上有时会自动加这个位，这里显式去掉。
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            user32.GetWindowLongW.restype = ctypes.c_long
            exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if exstyle & WS_EX_LAYERED:
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle & ~WS_EX_LAYERED)
                user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0004 | 0x0020)

            # 背板已经由 DWM 提供，卡片自己就别再叠一层白纱了：
            # 否则会像「磨砂贴图」，而不是像主界面那样透出背后。
            self.card._tint_strength = 0.12
            self.card._fallback_alpha = 10
            self.card.update()
        except Exception:  # noqa: BLE001 - 老系统没有背板就直接退回半透明色
            import logging

            logging.getLogger(__name__).debug("enable acrylic for login dialog failed", exc_info=True)

    def _apply_round_mask(self) -> None:
        """不再用 mask：窗口本身全透明，圆角由卡片自己画。

        （mask + DWM 背板会在边缘合成出一条 2px 的灰边，实测无法靠调色消除；
        改为「窗口透明 + 卡片圆角」后，圆角外就是真正的桌面。）
        """

        return

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def paintEvent(self, event):
        """用和主窗口相同的方式铺背板。

        不用 ``theme.background("window")``——主题模块在某些时机会被重置成
        ``Backdrop.NONE``，那时它返回不透明色，一点下去窗口就变成一块死板。
        这里写死一个半透明底色，任何时候都让 DWM 的玻璃透上来。
        """

        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        fill = QColor(16, 17, 20, 118) if theme.is_dark() else QColor(248, 249, 252, 92)
        painter.fillRect(self.rect(), fill)
        painter.end()

    def changeEvent(self, event):
        # 窗口激活状态变化时 DWM 会重算背板，顺手把属性再设一遍（幂等、很便宜）
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isVisible():
            self._enable_glass()
            self.card.update()

    def _play_sheen(self) -> None:
        self.sheen.show()
        self.sheen.raise_()
        animation = QVariantAnimation(self)
        animation.setDuration(_duration(900))
        animation.setStartValue(-0.6)
        animation.setEndValue(1.6)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda value: self.sheen.set_progress(float(value)))

        def finished():
            self.sheen.hide()

        animation.finished.connect(finished)
        animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    # -- mode ----------------------------------------------------------------

    def _apply_mode(self) -> None:
        if self._setup_mode:
            self.title.setText("创建账号")
            self.subtitle.setText("首次使用，设置一个本地账号（之后可以换成服务器账号）")
            self.submit.set_text("创建并登录")
            self.switch.set_text("已有账号？去登录")
            self.confirm.setVisible(True)
        else:
            self.title.setText("登录")
            self.subtitle.setText("输入账号密码继续")
            self.submit.set_text("登录")
            self.switch.set_text("没有账号？创建一个")
            self.confirm.setVisible(False)
        self._set_error("")

    def _toggle_mode(self) -> None:
        self._setup_mode = not self._setup_mode
        self._apply_mode()
        self._relayout()

    # -- submit --------------------------------------------------------------

    def _set_error(self, message: str) -> None:
        changed = self.error.text() != message
        self.error.setText(message)
        self.error.setVisible(bool(message))
        if message and changed and self._animated_in:
            self._shake()

    def _shake(self) -> None:
        """密码错误时卡片轻微左右抖一下：比只弹红字更容易被注意到。"""

        final = self.card.geometry()
        animation = QVariantAnimation(self)
        animation.setDuration(380)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.Linear)

        def on_value(value):
            t = float(value)
            offset = math.sin(t * math.pi * 5) * 11 * (1 - t)
            self.card.move(final.x() + int(offset), final.y())

        def finished():
            self.card.move(final.x(), final.y())

        animation.valueChanged.connect(on_value)
        animation.finished.connect(finished)
        animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def _finish(self) -> None:
        """登录成功：把「现在屏幕上的登录卡」交给转场层，由它溶解掉。

        不能再用 ``self.card.grab()``——卡片本身是半透明的（只有 10 alpha 的
        底色 + 一点白光），抓出来几乎全透明，溶解时什么都看不到。这里直接抓
        屏幕上那块真实像素，连玻璃和背后的东西一起带走。
        """

        try:
            from app_ui import morph
            from app_ui.frosted import _grab_region

            origin = self.mapToGlobal(QPoint(MARGIN, MARGIN))
            rect = QRect(origin, self.card.size())
            image = _grab_region(rect)
            pixmap = QPixmap.fromImage(image) if image is not None and not image.isNull() else None
            self.hide()
            morph.begin_collapse(pixmap, rect)
        except Exception:  # noqa: BLE001 - 转场失败就直接关掉，不能挡住登录
            self.hide()
        self.accept()

    def _submit(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if self._setup_mode:
            if password != self.confirm.text():
                self._set_error("两次输入的密码不一致")
                return
            ok, message = self.store.create(username, password)
            if not ok:
                self._set_error(message)
                return
            session.current = username
            self._finish()
            return

        if not username or not password:
            self._set_error("请输入账号和密码")
            return
        if not self.store.verify(username, password):
            self._set_error("账号或密码不正确")
            self.password.selectAll()
            self.password.setFocus()
            return
        session.current = username
        self._finish()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._submit()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    # -- frameless drag ------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


def authenticate(icon_path: str | None = None, parent: QWidget | None = None) -> bool:
    """弹登录框；返回是否通过。

    启动画面与登录卡做**交叉过渡**：先撑满启动画面的最短展示时间，再同时
    开始「登录卡入场」和「启动画面淡出」，避免中间出现一段没有内容的硬切。
    """

    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    if application is None:
        return True
    store = AuthStore()
    dialog = AuthDialog(store, icon_path=icon_path, parent=parent)

    from app_ui import splash

    splash.ensure_min_visible()
    # 让 exec() 自己负责显示：先 show() 再 exec() 会让模态循环直接返回 rejected。
    # 启动画面的淡出在 dialog.showEvent 里触发，和登录卡入场交叉。
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    dialog.deleteLater()
    if accepted:
        import logging

        logging.getLogger(__name__).info(f"startup: 登录通过 {splash.elapsed_ms()}ms")
    return accepted
