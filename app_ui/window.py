"""本项目自己的应用外壳（不继承 ok-script 的 MainWindow / KitWindow）。

窗口结构完全自绘：

    ┌───────────┬──────────────────────────────────────────────┐
    │ 品牌区     │ 状态条：连接状态 · 当前任务 · 开始/暂停/停止    │
    │           ├──────────────────────────────────────────────┤
    │ 任务      │                                              │
    │ 日志      │              页面内容（堆栈）                  │
    │           │                                              │
    │ 设置      │                                              │
    │ 关于      │                                              │
    └───────────┴──────────────────────────────────────────────┘

只复用 ok-script 的引擎（TaskExecutor / StartController / Config），
视觉与信息架构全部是新的。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QSize, Qt, QTimer
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
from ok.ui.qt.shell.run_bar import compute_run_state, format_elapsed, game_display_name

from app_ui.log_page import LogPage
from app_ui.motion import AnimatedButton, NavItem, fade_in_window, slide_in

logger = Logger.get_logger(__name__)

SIDEBAR_WIDTH = 212

_SECTION_RUN = "工作台"
_SECTION_SYSTEM = "系统"


class Sidebar(QFrame):
    """品牌标记 + 导航 + 当前账号。"""

    def __init__(self, title: str, version: str, icon: QIcon, parent=None):
        super().__init__(parent)
        self.setObjectName("appSidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)

        self.brand_icon = QLabel()
        self.brand_icon.setFixedSize(26, 26)
        if icon is not None and not icon.isNull():
            self.brand_icon.setPixmap(icon.pixmap(QSize(26, 26)))

        self.nav_layout = QVBoxLayout()
        self.nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_layout.setSpacing(2)

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
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(6)
        layout.addWidget(self.brand_icon, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(16)
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
        self.nav_layout.addWidget(button)


class PageHost(QWidget):
    """承载页面栈的容器：自己管几何，方便做页面滑动动画。

    放进布局里 QStackedWidget 的位置就被布局接管了，没法动画；这里把栈从布局里
    摘出来，由容器在 resizeEvent 里设置几何，切换页面时才能安全地平移。
    """

    def __init__(self, stack: QStackedWidget, parent=None):
        super().__init__(parent)
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


class AppWindow(QWidget):
    """替换 ok-script MainWindow 的外壳。"""

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
        self.setWindowTitle(title)
        # 必须在 show 之前设为 0，否则 Windows 首次合成会直接按 1 显示，淡入看不到
        self.setWindowOpacity(0.0)
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

        layout = QHBoxLayout(self)
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

    # -- pages ---------------------------------------------------------------

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
        if widget is self.setting_placeholder:
            self._ensure_settings()
        changed = widget is not self.stack.currentWidget()
        self.stack.setCurrentWidget(widget)
        self._current = widget
        for button, page in self._nav.values():
            button.set_selected(page is widget)
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
                # 登录卡已经收成一条线，这里让主窗口在背后就绪，再由 overlay 把它「撑开」
                self.setWindowOpacity(1.0)
                from PySide6.QtCore import QTimer

                from app_ui.splash import elapsed_ms

                logger.info(f"startup: 主窗口显示（转场撑开） {elapsed_ms()}ms")
                QTimer.singleShot(0, lambda: morph.reveal_into(self))
            else:
                from app_ui.splash import elapsed_ms

                logger.info(f"startup: 主窗口显示 {elapsed_ms()}ms")
                fade_in_window(self, 220)
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
