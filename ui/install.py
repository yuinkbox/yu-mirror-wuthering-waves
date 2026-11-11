"""把框架自带的页面/窗口替换成本项目自己的实现。

ok-script 在构造主窗口时才 import 页面类（函数内 import），所以只要在那之前
替换模块属性，就会用上我们的实现——不需要改 site-packages。

替换内容：
  - ok.ui.qt.MainWindow.MainWindow       -> app_ui.window.AppWindow  （整个外壳）
  - ok.ui.qt.tasks.tasks_page.TasksPage  -> ui.tasks.TasksPage       （任务页）
  - ok.ui.qt.about.AboutTab.AboutTab     -> ui.about.AboutTab        （关于页）
  - 任务图标不再按分类着色
"""

from __future__ import annotations

from ok import Logger

logger = Logger.get_logger(__name__)


def install() -> None:
    import importlib

    _apply_app_identity()

    # 1) 整个主窗口外壳
    main_window_mod = importlib.import_module("ok.ui.qt.MainWindow")
    from app_ui.window import AppWindow

    main_window_mod.MainWindow = AppWindow

    # 2) 任务页（即使外壳里也会用到 TasksPage 的实例）
    tasks_mod = importlib.import_module("ok.ui.qt.tasks.tasks_page")
    from ui.tasks import TasksPage

    tasks_mod.TasksPage = TasksPage
    tasks_mod._category_tint = lambda source, key, fallback=None: source
    tasks_mod.task_icon = lambda task, is_trigger=False: _plain_icon(task, is_trigger)

    # 3) 关于页
    about_mod = importlib.import_module("ok.ui.qt.about.AboutTab")
    from ui.about import AboutTab

    about_mod.AboutTab = AboutTab

    logger.info("custom UI installed: AppWindow shell, TasksPage, AboutTab, monochrome icons")

    _install_auth_gate()


def _apply_app_identity() -> None:
    """任务栏图标 / 分组：给进程一个自己的 AppUserModelID，并把 app 图标挂上去。

    KitWindow 是无边框窗口，Windows 会拿「窗口类」的图标去画任务栏按钮；光给
    窗口 setWindowIcon 有时不生效，所以这里同时设 QApplication 图标和 AUMID。
    """

    import ctypes
    import os

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(base, "icons", "app.ico")
    application = QApplication.instance()
    if application is not None and os.path.isfile(icon_path):
        application.setWindowIcon(QIcon(icon_path))
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("yuinkbox.YuMirror.WutheringWaves")
    except Exception:  # noqa: BLE001 - 旧系统上没有这个接口，忽略
        logger.debug("SetCurrentProcessExplicitAppUserModelID failed", exc_info=True)


def _install_auth_gate() -> None:
    """在主窗口显示前插入账号登录。"""

    from ok import App

    original = App.show_main_window

    def show_main_window(self):
        from auth import authenticate
        from app_ui import splash

        splash.set_progress(1.0, "准备就绪")
        logger.info(f"startup: 初始化完成 {splash.elapsed_ms()}ms，切换到登录")
        icon_path = (getattr(self, "config", None) or {}).get("gui_icon")
        if not authenticate(icon_path=icon_path):
            from PySide6.QtWidgets import QApplication

            application = QApplication.instance()
            if application is not None:
                application.quit()
            return
        original(self)

    App.show_main_window = show_main_window
    logger.info("auth gate installed")


def _plain_icon(task, is_trigger=False):
    """复用框架的图标解析，但去掉分类颜色。"""

    import importlib

    tasks_mod = importlib.import_module("ok.ui.qt.tasks.tasks_page")
    source = getattr(task, "icon", None)
    if source is None:
        return None
    resolved = tasks_mod.nav_icon(source, trim_transparent=True)
    if resolved == tasks_mod.FALLBACK:
        return None
    return resolved
