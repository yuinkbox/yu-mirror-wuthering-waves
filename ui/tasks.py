"""任务页 v2：单列手风琴列表。

信息架构（与旧版两栏完全不同）：
    标题 + 搜索
    分段筛选：全部 / 手动 / 触发器
    ┌ 手动 12 ───────────────────────────────┐
    │ 图标  每日任务            运行中  ▶  ⌄ │  ← 点一行就地展开
    │       启动游戏、登录、领月卡…           │
    │       └ 展开后：设置表单 + 运行/重置     │
    └────────────────────────────────────────┘

数据仍来自 ok-script 的 executor，任务逻辑与配置持久化不变。
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEasingCurve, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ok import Logger, og
from ok.ui.qt.kit import Card, Label, Switch, TabStrip, theme
from ok.ui.qt.kit.icons import icon as kit_icon

from app_ui.motion import AnimatedButton, animate_height, mix
from ok.ui.qt.shell.nav_icons import nav_icon
from ok.ui.qt.tasks.config_form import ConfigForm
from ok.ui.qt.tasks.tasks_page import (
    display_name,
    monogram,
    set_trigger_enabled,
    split_leading_symbol,
    start_task,
    stop_task,
    task_status,
    visible_tasks,
)

logger = Logger.get_logger(__name__)

GLYPH = 20
LIST_MAX_WIDTH = 980


def _t(text: str) -> str:
    return QCoreApplication.translate("TasksPage", text)


def _app_tr(text: str) -> str:
    return og.app.tr(text) if og.app is not None and text else (text or "")


class TaskGlyph(QWidget):
    """20px 线性图标；没有图标时用首字。"""

    def __init__(self, task, size: int = GLYPH, parent=None):
        super().__init__(parent)
        self._task = task
        self._size = size
        self.setFixedSize(size, size)
        theme.changed.connect(self.update)

    def paintEvent(self, event):
        from PySide6.QtCore import QRect

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        source = getattr(self._task, "icon", None)
        qicon = nav_icon(source, trim_transparent=True) if source is not None else None
        if isinstance(qicon, str):
            qicon = kit_icon(qicon, "text_secondary", 1.6)
        if isinstance(qicon, QIcon) and not qicon.isNull():
            painter.drawPixmap(0, 0, qicon.pixmap(QSize(self._size, self._size), self.devicePixelRatioF()))
        else:
            symbol, name = split_leading_symbol(_app_tr(self._task.name))
            font = QFont(self.font())
            font.setPixelSize(int(self._size * 0.6))
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(theme.color("text_tertiary")))
            painter.drawText(QRect(0, 0, self._size, self._size), Qt.AlignmentFlag.AlignCenter,
                             symbol or monogram(name))
        painter.end()


class TaskHeader(QFrame):
    """可点击的标题行。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("taskHeader")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class TaskRow(QFrame):
    """一行任务 = 标题 + 可展开的设置面板。"""

    picked = Signal(object)

    def __init__(self, task, is_trigger: bool, parent=None):
        super().__init__(parent)
        self.task = task
        self.is_trigger = is_trigger
        self._expanded = False
        self._form = None
        self.setObjectName("taskRow")
        self.setProperty("expanded", False)
        self._hover_value = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(150)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        # ---- 标题行
        self.glyph = TaskGlyph(task)
        self._name_text = display_name(task)
        self._desc_text = _app_tr(getattr(task, "description", "") or "")
        self.name_label = Label(self._name_text, "body")
        self.name_label.setObjectName("taskName")
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.desc_label = Label(self._desc_text, "caption")
        self.desc_label.setObjectName("taskDesc")
        self.desc_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        text = QVBoxLayout()
        text.setSpacing(1)
        text.setContentsMargins(0, 0, 0, 0)
        text.addWidget(self.name_label)
        text.addWidget(self.desc_label)

        self.status = Label("", "caption")
        self.status.setObjectName("taskStatus")
        self.status.setVisible(False)

        if is_trigger:
            self.action = Switch()
            self.action.setChecked(bool(getattr(task, "enabled", False)))
            self.action.toggled.connect(self._on_switch)
        else:
            self.action = AnimatedButton(None, "play", "ghost", height=26, icon_only=True)
            self.action.setFixedSize(26, 26)
            self.action.clicked.connect(self._on_run)

        self.chevron = Label()
        self.chevron.setFixedSize(14, 14)

        self.header = TaskHeader()
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(10, 9, 10, 9)
        header_layout.setSpacing(12)
        header_layout.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addLayout(text, 1)
        header_layout.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        self.header.clicked.connect(lambda: self.picked.emit(self.task))

        # ---- 展开区（懒加载 ConfigForm）
        self.body = QWidget()
        self.body.setObjectName("taskBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(42, 4, 12, 12)
        self.body_layout.setSpacing(10)
        self.body.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.body)

        self.refresh()

    # -- state ---------------------------------------------------------------

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.setProperty("expanded", bool(expanded))
        self.update()
        self.chevron.setPixmap(
            kit_icon("chevron-up" if expanded else "chevron-down", "text_tertiary", 1.8)
            .pixmap(QSize(14, 14), self.devicePixelRatioF()))
        if expanded and self._form is None:
            self._build_body()
        animate_height(self.body, expanded)
        if not expanded:
            self._hover.to(0.0)

    def _build_body(self) -> None:
        self._form = ConfigForm(self.task, self.task.config, self.task.default_config,
                                self.task.config_description, self.task.config_type)
        self.body_layout.addWidget(self._form)

        self.run_button = AnimatedButton(_t("运行任务"), "play", "primary", height=30)
        self.run_button.clicked.connect(lambda: start_task(self.task, self))
        self.stop_button = AnimatedButton(_t("停止"), "stop", "secondary", height=30)
        self.stop_button.clicked.connect(lambda: stop_task(self.task))
        self.reset_button = AnimatedButton(_t("重置配置"), "reset", "ghost", height=30)
        self.reset_button.clicked.connect(self._reset_config)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addWidget(self.reset_button)
        footer.addStretch(1)
        footer.addWidget(self.stop_button)
        footer.addWidget(self.run_button)
        self.body_layout.addLayout(footer)
        self._refresh_actions()

    def _reset_config(self) -> None:
        self.task.config.reset_to_default()
        if self._form is not None:
            self._form.setParent(None)
            self._form.deleteLater()
            self._form = None
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._build_body()

    def _on_run(self) -> None:
        start_task(self.task, self)

    def _on_switch(self, enabled: bool) -> None:
        set_trigger_enabled(self.task, enabled)

    def _refresh_actions(self) -> None:
        if self.is_trigger or not hasattr(self, "run_button"):
            return
        running = bool(self.task.enabled)
        self.run_button.setVisible(not running)
        self.stop_button.setVisible(running)

    def refresh(self) -> None:
        self.glyph.update()
        self.action.blockSignals(True)
        if self.is_trigger:
            self.action.setChecked(bool(self.task.enabled))
            self.status.setVisible(False)
        else:
            running = bool(self.task.enabled)
            self.action.set_icon("stop" if running else "play")
            self.action.setToolTip(_t("停止") if running else _t("运行"))
            status = task_status(self.task, og.executor)
            if status:
                text, tone, tip = status
                self.status.setText(text)
                self.status.setProperty("tone", tone)
                self.status.setToolTip(tip or "")
                self.status.setVisible(True)
                style = self.status.style()
                style.unpolish(self.status)
                style.polish(self.status)
            else:
                self.status.setVisible(False)
        self.action.blockSignals(False)
        self._refresh_actions()

    # -- hover 动效 / 自绘背景 ------------------------------------------------

    def _on_hover(self, value) -> None:
        self._hover_value = float(value)
        self.update()

    def _animate_hover(self, target: float) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_value)
        self._hover_anim.setEndValue(float(target))
        self._hover_anim.start()

    def enterEvent(self, event):
        if not self._expanded:
            self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(0, 1, 0, -1)
        if self._expanded:
            background = QColor(theme.color("surface_alt"))
        else:
            background = mix(QColor(0, 0, 0, 0), QColor(theme.color("control_hover")), self._hover_value)
        if background.alpha() > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, 8, 8)
        if not self._expanded:
            painter.setPen(QColor(theme.color("separator")))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        available = max(80, self.width() - GLYPH - 150)
        metrics = QFontMetrics(self.name_label.font())
        self.name_label.setText(metrics.elidedText(self._name_text, Qt.TextElideMode.ElideRight, available))
        desc_metrics = QFontMetrics(self.desc_label.font())
        self.desc_label.setText(desc_metrics.elidedText(self._desc_text, Qt.TextElideMode.ElideRight, available))


class TaskSection(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title_label = Label(title, "section")
        self.count_label = Label("", "caption")
        header = QHBoxLayout()
        header.setContentsMargins(10, 0, 10, 0)
        header.setSpacing(8)
        header.addWidget(self.title_label)
        header.addWidget(self.count_label)
        header.addStretch(1)

        self.card = QFrame()
        self.card.setObjectName("taskGroup")
        self.rows_layout = QVBoxLayout(self.card)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self.card)

    def set_rows(self, rows) -> None:
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for row in rows:
            self.rows_layout.addWidget(row)
        self.count_label.setText(str(len(rows)))

    def set_visible_rows(self, visible: bool) -> None:
        self.setVisible(visible)


class TasksPage(QWidget):
    """任务主页面。"""

    state_name = "tasks_page"

    def __init__(self, parent=None, title: str = None):
        super().__init__(parent)
        self.setObjectName("TasksPage")
        self.onetime_tasks = []
        self.trigger_tasks = []
        self.rows = {}
        self.selected_task = None
        self._filter = "all"

        self.title_label = Label(title or _t("任务"), "display")
        self.count_label = Label("", "caption")
        self.search = QLineEdit()
        self.search.setPlaceholderText(_t("搜索任务…"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._apply)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.title_label)
        top.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignBottom)
        top.addStretch(1)
        top.addWidget(self.search)

        self.filters = TabStrip()
        self.filters.current_changed.connect(self._on_filter)
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.addWidget(self.filters)
        filter_row.addStretch(1)

        self.onetime_section = TaskSection(_t("手动"))
        self.trigger_section = TaskSection(_t("触发器"))
        list_body = QVBoxLayout()
        list_body.setContentsMargins(0, 0, 0, 0)
        list_body.setSpacing(0)
        list_body.addWidget(self.onetime_section)
        list_body.addWidget(self.trigger_section)
        list_body.addStretch(1)
        holder = QWidget()
        holder.setObjectName("taskListHolder")
        holder.setLayout(list_body)
        holder.setAutoFillBackground(False)

        scroll = QScrollArea()
        scroll.setObjectName("taskListScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet(
            "QScrollArea#taskListScroll { background: transparent; border: 0; }"
            " QScrollArea#taskListScroll > QWidget > QWidget { background: transparent; }")
        self.scroll = scroll

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(scroll)
        content_layout.addStretch(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 12)
        layout.setSpacing(12)
        layout.addLayout(top)
        layout.addLayout(filter_row)
        layout.addWidget(scroll, 1)
        theme.install(self)
        self.setMaximumWidth(10_000)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        from ok.ui.qt.Communicate import communicate

        communicate.task.connect(lambda *_: self._on_event())
        communicate.task_list_updated.connect(self.refresh_tasks)
        self.refresh_tasks()

    # -- data ----------------------------------------------------------------

    def has_tasks(self) -> bool:
        return bool(self.onetime_tasks or self.trigger_tasks)

    def has_task(self, task) -> bool:
        return task in self.rows

    def collect_tasks(self):
        return visible_tasks(og.executor, getattr(og, "task_manager", None))

    def refresh_tasks(self) -> None:
        self.onetime_tasks, self.trigger_tasks = self.collect_tasks()
        self.rows = {}
        onetime_rows = [self._make_row(task, False) for task in self.onetime_tasks]
        trigger_rows = [self._make_row(task, True) for task in self.trigger_tasks]
        self.onetime_section.set_rows(onetime_rows)
        self.trigger_section.set_rows(trigger_rows)
        self._build_filters()
        self.count_label.setText(f"{len(self.onetime_tasks) + len(self.trigger_tasks)} 个任务")

        selected = self.selected_task if self.selected_task in self.rows else None
        if selected is None and self.onetime_tasks:
            selected = self.onetime_tasks[0]
        # 首次构建时不展开（展开会当场构建整套设置表单，拖慢首帧）；
        # 等窗口画出来之后再自动展开第一项，顺便让展开动画可见。
        if getattr(self, "_painted_once", False):
            self.select_task(selected)
        else:
            self.selected_task = selected
            self._painted_once = True
            if selected is not None:
                QTimer.singleShot(320, lambda task=selected: self.select_task(task))
        self._apply()

    def _make_row(self, task, is_trigger: bool) -> TaskRow:
        row = TaskRow(task, is_trigger)
        row.picked.connect(self.select_task)
        self.rows[task] = row
        return row

    def _build_filters(self) -> None:
        current = self._filter
        self.filters.blockSignals(True)
        self.filters.clear()
        self.filters.add_tab("all", _t("全部"), len(self.onetime_tasks) + len(self.trigger_tasks))
        self.filters.add_tab("onetime", _t("手动"), len(self.onetime_tasks))
        self.filters.add_tab("trigger", _t("触发器"), len(self.trigger_tasks))
        self.filters.set_current(current)
        self.filters.blockSignals(False)

    def select_task(self, task) -> None:
        self.selected_task = task
        for row_task, row in self.rows.items():
            row.set_expanded(row_task is task)
        row = self.rows.get(task)
        if row is not None:
            # 展开动画结束后把整行滚进可视区，避免只露半个表单
            QTimer.singleShot(280, lambda: self.scroll.ensureWidgetVisible(row, 0, 12))

    def _on_filter(self, key: str) -> None:
        self._filter = key or "all"
        self._apply()

    def _apply(self) -> None:
        needle = self.search.text().strip().lower()

        def matches(task) -> bool:
            haystack = f"{display_name(task)} {getattr(task, 'description', '') or ''}".lower()
            return not needle or needle in haystack

        onetime_visible = False
        for task, row in self.rows.items():
            if task in self.onetime_tasks:
                shown = self._filter in ("all", "onetime") and matches(task)
                row.setVisible(shown)
                onetime_visible = onetime_visible or shown
            else:
                shown = self._filter in ("all", "trigger") and matches(task)
                row.setVisible(shown)
        trigger_visible = any(
            not row.isHidden() for task, row in self.rows.items() if task in self.trigger_tasks)
        self.onetime_section.setVisible(bool(self.onetime_tasks) and onetime_visible)
        self.trigger_section.setVisible(bool(self.trigger_tasks) and trigger_visible)

    # -- events --------------------------------------------------------------

    def _on_event(self) -> None:
        for row in self.rows.values():
            row.refresh()

    def _tick(self) -> None:
        if self.isVisible():
            self._on_event()

    def dispose(self) -> None:
        self.timer.stop()
        try:
            from ok.ui.qt.Communicate import communicate

            communicate.task_list_updated.disconnect(self.refresh_tasks)
        except (RuntimeError, TypeError):
            pass
