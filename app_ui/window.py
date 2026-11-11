"""本项目自己的应用外壳。

窗口结构：

    ┌──────────────────────────────────────────────────────────┐
    │ 标题条：图标 · 名称 · 最小/最大/关闭（kit TitleBar）          │
    ├───────────┬──────────────────────────────────────────────┤
    │ 品牌区     │ 状态条：连接状态 · 当前任务 · 开始/暂停/停止    │
    │           ├──────────────────────────────────────────────┤
    │ 任务      │                                              │
    │ 日志      │              页面内容（堆栈）                  │
    │           │                                              │
    │ 设置      │                                              │
    │ 关于      │                                              │
    └───────────┴──────────────────────────────────────────────┘

外层用 kit 的 `KitWindow`：无边框 + DWM 背板（Mica/Acrylic）+ 原生
`WM_NCHITTEST` 命中测试，所以贴边分屏、Snap 布局、窗口阴影、圆角全都保留。
视觉与信息架构仍然是本项目自己的。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QRect, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QFontMetrics, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ok import Logger, og
from ok.ui.qt.kit import Label, theme
from ok.ui.qt.kit.icons import icon as kit_icon
from ok.ui.qt.kit.theme import set_style_property
from ok.ui.qt.kit.window import KitWindow
from ok.ui.qt.shell.run_bar import compute_run_state, format_elapsed, game_display_name

from app_ui.log_page import LogPage
from app_ui.motion import AnimatedButton, NavItem, fade_in_window, slide_in

logger = Logger.get_logger(__name__)

SIDEBAR_WIDTH = 212

_SECTION_RUN = "工作台"
_SECTION_SYSTEM = "系统"


class Sidebar(QFrame):
    """导航 + 当前账号（品牌图标已交给窗口标题条，这里不再重复）。"""

    def __init__(self, title: str, version: str, icon: QIcon, parent=None):
        super().__init__(parent)
        self.setObjectName("appSidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)

        self.nav_layout = QVBoxLayout()
        self.nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_layout.setSpacing(2)

        # 侧栏共用一块「选中玻璃」，在各项之间滑动（比每项自己淡入淡出更顺）
        self._items: list[NavItem] = []
        self._selected_item: NavItem | None = None
        self._pill_rect = QRect()
        self._pill_anim = QVariantAnimation(self)
        self._pill_anim.setDuration(260)
        self._pill_anim.setEasingCurve(QEasingCurve.Type.OutQuint)
        self._pill_anim.valueChanged.connect(self._on_pill)

        self.avatar = QLabel("?")
        self.avatar.setObjectName("userAvatar")
        self.avatar.setFixedSize(24, 24)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.user_label = Label("", "secondary")
        self.user_label.setObjectName("userName")
        self.user_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.logout_button = QPushButton()
        self.logout_button.setObjectName("ghostButton")
        self.logout_button.setFixedSize(26, 26)
        self.logout_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.logout_button.setToolTip("退出登录")
        self.logout_button.setIcon(kit_icon("power", "text_tertiary", 1.7))
        self.logout_button.clicked.connect(lambda: self.on_logout and self.on_logout())
        self.on_logout = None

        user_row = QHBoxLayout()
        user_row.setContentsMargins(4, 0, 0, 0)
        user_row.setSpacing(8)
        user_row.addWidget(self.avatar)
        user_row.addWidget(self.user_label, 1)
        user_row.addWidget(self.logout_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(6)
        layout.addLayout(self.nav_layout)
        layout.addStretch(1)
        layout.addLayout(user_row)

    def set_user(self, username: str) -> None:
        initial = (username or "?")[:1].upper()
        self.avatar.setText(initial)
        metrics = QFontMetrics(self.user_label.font())
        self.user_label.setText(metrics.elidedText(username or "", Qt.TextElideMode.ElideRight, 110))
        self.user_label.setToolTip(username or "")

    def add_section(self, text: str) -> None:
        label = Label(text, "section")
        label.setContentsMargins(8, 10, 0, 4)
        self.nav_layout.addWidget(label)

    def add_item(self, button: NavItem) -> None:
        button.pill_managed = True
        self._items.append(button)
        self.nav_layout.addWidget(button)

    # -- 滑动选中块 ----------------------------------------------------------

    def select(self, item: NavItem | None, animate: bool = True) -> None:
        """把选中块移到 ``item`` 上，并同步各项的文字颜色。"""

        if item is None:
            return
        for button in self._items:
            button.set_selected(button is item)
        self._selected_item = item
        target = item.geometry()
        if not animate or self._pill_rect.isNull():
            self._pill_anim.stop()
            self._pill_rect = target
            self.update()
            return
        self._pill_anim.stop()
        self._pill_anim.setStartValue(self._pill_rect)
        self._pill_anim.setEndValue(target)
        self._pill_anim.start()

    def _on_pill(self, value) -> None:
        self._pill_rect = value if isinstance(value, QRect) else QRect(value)
        self.update()

    def _sync_pill(self) -> None:
        item = self._selected_item
        if item is None or item.parent() is not self:
            return
        rect = item.geometry()
        if rect != self._pill_rect:
            self._pill_rect = rect
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pill_rect.isNull() or self._selected_item is None:
            return
        from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self._pill_rect.adjusted(0, 1, -1, -1)
        radius = 8.0
        dark = theme.is_dark()

        # 底：极淡的一层，先垫出「有东西浮起来」的感觉
        shadow = QColor(0, 0, 0, 26 if not dark else 60)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawRoundedRect(rect.adjusted(0, 1.5, 0, 1.5), radius, radius)

        # 主体：上浅下深的竖向渐变，比纯色更像玻璃
        fill = QColor(theme.color("control_hover"))
        fill.setAlpha(246 if not dark else 226)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, fill.lighter(104))
        gradient.setColorAt(1.0, fill)
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, radius, radius)

        # 顶部一道内高光
        highlight = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        highlight.setColorAt(0.0, QColor(255, 255, 255, 150 if not dark else 26))
        highlight.setColorAt(0.55, QColor(255, 255, 255, 0))
        painter.setBrush(highlight)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        # 发丝边，压住边缘不让它糊
        edge = QColor(255, 255, 255, 190 if not dark else 38)
        painter.setPen(QPen(edge, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_pill()


class PageHost(QWidget):
    """承载页面栈的容器：自己管几何，方便做页面滑动动画。

    放进布局里 QStackedWidget 的位置就被布局接管了，没法动画；这里把栈从布局里
    摘出来，由容器在 resizeEvent 里设置几何，切换页面时才能安全地平移。
    """

    def __init__(self, stack: QStackedWidget, parent=None):
        super().__init__(parent)
        self.setObjectName("appPageHost")
        self._stack = stack
        stack.setParent(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._stack.setGeometry(self.rect())


class StatusBar(QFrame):
    """顶部状态条：连接/提示 + 当前任务 + 主操作。"""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setObjectName("appStatusBar")
        self.setFixedHeight(56)
        self.config = config or {}

        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.status_label = Label("", "secondary")
        self.status_label.setObjectName("runStatus")
        self.task_label = Label("", "caption")
        self.elapsed_label = Label("", "caption")
        self.task_label.setVisible(False)
        self.elapsed_label.setVisible(False)

        left = QHBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.dot)
        left.addWidget(self.status_label)
        left.addSpacing(8)
        left.addWidget(self.task_label)
        left.addWidget(self.elapsed_label)
        left.addStretch(1)

        self.start_button = AnimatedButton("开始", None, "primary", height=32)
        self.start_button.clicked.connect(self._toggle)

        self.stop_button = AnimatedButton("停止当前任务", None, "ghost", height=32)
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self._stop_task)

        right = QHBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.stop_button)
        right.addWidget(self.start_button)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 0, 18, 0)
        layout.setSpacing(12)
        layout.addLayout(left, 1)
        layout.addLayout(right)

        self._state = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    # -- state ---------------------------------------------------------------

    def _starting(self) -> bool:
        controller = getattr(getattr(og, "app", None), "start_controller", None)
        return bool(controller is not None and getattr(controller, "starting", False))

    def _compute(self):
        executor = getattr(og, "executor", None)
        if executor is None:
            return None
        return compute_run_state(
            executor,
            getattr(og, "device_manager", None),
            self._starting(),
            game_display_name(self.config),
            self.config.get("gui_title") or "",
        )

    def _connected(self) -> bool:
        device_manager = getattr(og, "device_manager", None)
        try:
            device = device_manager.get_preferred_device() if device_manager else None
            return bool(device is not None and device.get("connected"))
        except Exception:  # noqa: BLE001
            return False

    def refresh(self) -> None:
        state = self._compute()
        self._state = state
        connected = self._connected()
        self.dot.setStyleSheet(
            "border-radius: 4px; background: %s;" % ("#22C55E" if connected else "#A1A1AA"))

        if state is None:
            self.status_label.setText("引擎未就绪")
            self.start_button.set_enabled(False)
            return

        message = state.message or ("游戏已连接" if connected else "游戏未连接")
        self.status_label.setText(message)
        self.start_button.set_enabled(state.button != "disabled")
        labels = {"start": "开始", "pause": "暂停", "cancel": "取消启动", "disabled": "开始"}
        self.start_button.set_text(labels.get(state.button, "开始"))
        self.start_button.set_variant("primary" if state.button == "start" else "secondary")

        task = state.task
        running = task is not None
        self.stop_button.setVisible(running)
        self.task_label.setVisible(running)
        self.elapsed_label.setVisible(running)
        if running:
            self.task_label.setText(str(og.app.tr(task.name) if og.app is not None else task.name))
            started = getattr(task, "start_time", 0) or 0
            self.elapsed_label.setText(format_elapsed(time.time() - started) if started else "")

    # -- actions -------------------------------------------------------------

    def _toggle(self) -> None:
        state = self._state or self._compute()
        button = getattr(state, "button", "start")
        if button == "cancel":
            og.app.start_controller.cancel_start()
        elif button == "pause":
            og.executor.pause()
        elif button == "start":
            og.app.start_controller.start()
        QTimer.singleShot(300, self.refresh)

    def _stop_task(self) -> None:
        og.executor.stop_current_task()
        QTimer.singleShot(300, self.refresh)


class AppWindow(KitWindow):
    """替换 ok-script MainWindow 的外壳（壳是 kit 的玻璃窗口，内容是自己的）。"""

    def __init__(self, app, config, ok_config, icon, title, version, debug=False, about=None,
                 exit_event=None, global_config=None, executor=None, handler=None):
        super().__init__()
        self.app = app
        self.config = config or {}
        self.ok_config = ok_config
        self.executor = executor
        self.handler = handler
        self.global_config = global_config
        self.exit_event = exit_event
        self.shown = False
        self._pages = []
        self._current = None

        application = QApplication.instance()
        if application is not None:
            application.setApplicationDisplayName("")

        self.setObjectName("appRoot")
        # 让 Qt 的窗口表面带 alpha，DWM 的 Acrylic 背板才会从我们画的半透明色里透出来
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 标题条由 KitWindow 的 TitleBar 负责（图标 / 名称 / 窗口按钮）
        self.setWindowTitle(title)
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)

        from ui.tasks import TasksPage

        self.tasks_page = TasksPage()
        self.log_page = LogPage()
        # 设置页很重（外观 + 全部全局配置表单），首次打开时再建
        self.settings_page = None
        self.setting_placeholder = QWidget()
        self.setting_tab = self.setting_placeholder
        from ui.about import AboutTab

        self.about_page = AboutTab(config, exit_event=exit_event)

        self.sidebar = Sidebar(title, version, icon)
        self.stack = QStackedWidget()
        self.stack.setObjectName("appStack")
        self.status_bar = StatusBar(self.config)

        from auth.store import session

        self.sidebar.on_logout = self.logout
        self.sidebar.set_user(session.current or "")

        self._nav = {}
        self.sidebar.add_section(_SECTION_RUN)
        self._add_page("tasks", "任务", "tasks", self.tasks_page)
        self._add_page("log", "日志", "terminal", self.log_page)
        self.sidebar.add_section(_SECTION_SYSTEM)
        self._add_page("settings", "设置", "gear", self.setting_placeholder)
        self._add_page("about", "关于", "info", self.about_page)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        content.addWidget(self.status_bar)
        self.page_host = PageHost(self.stack)
        content.addWidget(self.page_host, 1)
        content_holder = QWidget()
        content_holder.setObjectName("appContent")
        content_holder.setLayout(content)

        layout = QHBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(content_holder, 1)

        theme.install(self)
        self.switch_to(self.tasks_page)
        # 开发/截图用：YMP_START_PAGE=settings 直接打开指定页
        import os

        start_page = os.environ.get("YMP_START_PAGE")
        if start_page and start_page in self._nav:
            self.switch_to(self._nav[start_page][1])
        self.resize(1200, 800)
        self.setMinimumSize(1000, 680)
        # 主窗口也先摆到屏幕中央再显示，避免第一帧出现在默认位置后跳一下
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.center() - self.rect().center())
        # 框架在窗口构造后立刻调用 windowHandle().screen() 取 DPI，
        # 普通 QWidget 在 show 之前没有原生句柄，这里先创建出来。
        self.create()
        # 句柄建好之后 Qt 才真正把图标交给窗口类，前面设的一次可能还挂在旧的类上，
        # 这里补一次，任务栏按钮才不会是一张白纸。
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)

    # -- pages ---------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """铺一层半透明色调，剩下的交给 DWM 背板透上来。"""

        from PySide6.QtGui import QColor, QPainter

        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        dark = theme.is_dark()
        # 只铺一层很薄的雾；透过去的模糊桌面由 DWM 的 Acrylic 背板提供，
        # 颜色铺太厚会把背板糊成一块死白，看着就像磨砂贴图。
        painter.fillRect(self.rect(), QColor(16, 17, 20, 74) if dark else QColor(248, 249, 252, 44))

    def _add_page(self, key: str, text: str, icon_name: str, widget: QWidget) -> None:
        button = NavItem(key, text, icon_name)
        button.clicked.connect(lambda target=widget: self.switch_to(target))
        self.sidebar.add_item(button)
        self._nav[key] = (button, widget)
        self._pages.append(widget)
        self.stack.addWidget(widget)

    def _ensure_settings(self) -> None:
        """第一次打开设置页时才构建（含全部全局配置表单）。"""

        if self.settings_page is not None:
            return
        from app_ui.settings_page import SettingsPage

        self.settings_page = SettingsPage(self.global_config)
        layout = QVBoxLayout(self.setting_placeholder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.settings_page)
        self.setting_tab = self.settings_page

    def switch_to(self, widget) -> None:
        if widget is None:
            return
        button = next((button for button, page in self._nav.values() if page is widget), None)
        # 先让选中块动起来，再做可能很重的页面构建（设置页要建整张表单）
        self.sidebar.select(button)
        if widget is self.setting_placeholder:
            self._ensure_settings()
        changed = widget is not self.stack.currentWidget()
        self.stack.setCurrentWidget(widget)
        self._current = widget
        if changed and getattr(self, "shown", False):
            slide_in(self.stack, 14, 180)

    def show_task(self, task) -> bool:
        if task is None or not self.tasks_page.has_task(task):
            return False
        self.switch_to(self.tasks_page)
        self.tasks_page.select_task(task)
        return True

    # -- window --------------------------------------------------------------

    def logout(self) -> None:
        """退出登录：隐藏窗口，重新弹登录框。"""

        from auth import authenticate
        from auth.store import session

        self.hide()
        session.current = None
        if authenticate(icon_path=self.config.get("gui_icon")):
            self.sidebar.set_user(session.current or "")
            self.show()
            self.bring_to_front()
        else:
            application = QApplication.instance()
            if application is not None:
                application.quit()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def set_window_size(self, width, height, min_width, min_height) -> None:
        self.setMinimumSize(min_width, min_height)
        self.resize(width, height)

    def update_ok_config(self) -> None:
        """框架在切换语言时会调用；本窗口没有需要回写的几何配置。"""

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_animated_in", False):
            self._animated_in = True
            from app_ui import morph

            if morph.has_pending():
                # 登录卡还在 overlay 上，这里让主窗口在背后就绪，
                # 内容轻轻上浮一点，再由 overlay 把卡片「溶解」掉。

                from app_ui.motion import slide_in
                from app_ui.splash import elapsed_ms

                logger.info(f"startup: 主窗口显示（光束转场） {elapsed_ms()}ms")
                slide_in(self.stack, 18, 260)
                QTimer.singleShot(0, lambda: morph.reveal_into(self))
            else:
                from app_ui.splash import elapsed_ms

                logger.info(f"startup: 主窗口显示 {elapsed_ms()}ms")
                fade_in_window(self, 220)
            # 空转时预热设置页，避免第一次点「设置」时构建表单卡住选中块动画
            QTimer.singleShot(1600, self._ensure_settings)
        shot = __import__("os").environ.get("YMP_UI_SHOT")
        if shot:
            # 开发/截图用：窗口显示 3 秒后把自身渲染成 PNG（不做 DWM 合成，只看排版）

            QTimer.singleShot(3000, lambda: self.grab().save(shot))
        self.shown = True

    def dispose(self) -> None:
        self.status_bar.timer.stop()
        for page in self._pages:
            dispose = getattr(page, "dispose", None)
            if callable(dispose):
                try:
                    dispose()
                except Exception:  # noqa: BLE001
                    logger.exception(f"dispose failed for {page}")
