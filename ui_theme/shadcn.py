"""把 ok-script 自带的 Qt 组件库换成 shadcn/ui 的视觉语言。

为什么用「覆盖」而不是改依赖包：
    ok-script 的界面全部由 `ok/ui/qt/kit` 的 token（色板）和 QSS 模板驱动。
    在 site-packages 里改会被下次 pip 安装覆盖，所以这里在运行时替换：
      - 色板（zinc 中性色 + 反色 primary，即 shadcn 默认主题）
      - 字体（Inter / Segoe UI Variable，13px 基准）
      - 圆角（卡片 12px、控件 6px，对应 shadcn 的 rounded-xl / rounded-md）
      - 关掉 Mica/Acrylic 毛玻璃，改成 shadcn 的纯色平面
      - primary 按钮文字色改成 on-primary（浅色白字、深色黑字）
      - 侧边栏选中项、标签页轨道改成 shadcn 的 muted 色块 / 白 pill

入口：main.py / main_debug.py / dev_preview.py 里 `apply()`，
由 config['ui_style'] 决定是否启用。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 色板

LIGHT = {
    "window": "#FFFFFF",          # background
    "sidebar": "#FAFAFA",         # sidebar / muted
    "surface": "#FFFFFF",         # card
    "surface_alt": "#FAFAFA",     # popover / muted
    "control": "#FFFFFF",         # input
    "control_hover": "#F4F4F5",   # accent (hover)
    "control_pressed": "#E4E4E7",
    "border": "#E4E4E7",          # border
    "border_strong": "#D4D4D8",   # input border
    "separator": "#E4E4E7",
    "text": "#09090B",            # foreground
    "text_secondary": "#52525B",
    "text_tertiary": "#71717A",   # muted-foreground
    "text_disabled": "#A1A1AA",
    "switch_off": "#D4D4D8",
    "scrim": "rgba(0, 0, 0, 90)",
    "shadow": "#000000",
    "success": "#16A34A",
    "warning": "#CA8A04",
    "danger": "#DC2626",
}

DARK = {
    "window": "#09090B",
    "sidebar": "#09090B",
    "surface": "#0F0F11",
    "surface_alt": "#18181B",
    "control": "#18181B",
    "control_hover": "#27272A",
    "control_pressed": "#3F3F46",
    "border": "#27272A",
    "border_strong": "#3F3F46",
    "separator": "#1F1F23",
    "text": "#FAFAFA",
    "text_secondary": "#A1A1AA",
    "text_tertiary": "#71717A",
    "text_disabled": "#52525B",
    "switch_off": "#3F3F46",
    "scrim": "rgba(0, 0, 0, 140)",
    "shadow": "#000000",
    "success": "#22C55E",
    "warning": "#EAB308",
    "danger": "#EF4444",
}

# shadcn 的 primary 是「反色」：浅色主题近黑、深色主题近白
ACCENT = {"light": "#18181B", "dark": "#FAFAFA"}
ON_ACCENT = {"light": "#FAFAFA", "dark": "#09090B"}

FONT_FAMILY = (
    '"Inter", "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif'
)
MONO_FONT_FAMILY = '"Geist Mono", "Cascadia Mono", "JetBrains Mono", Consolas, monospace'

# shadcn：card = rounded-xl(12px)，button/input = rounded-md(6px)，tabs = rounded-lg(8px)
_RADIUS_MAP = {
    "14px": "12px",
    "12px": "12px",
    "10px": "8px",
    "9px": "8px",
    "8px": "6px",
    "7px": "6px",
}


def _sub_rule(css: str, selector_pattern: str, replacement: str) -> str:
    """替换一条 `选择器 { ... }` 规则（只替换第一处）。"""

    return re.sub(selector_pattern + r"\s*\{[^}]*\}", replacement, css, count=1)


def _restyle(css: str, manager) -> str:
    """在框架生成的 QSS 上做 shadcn 化的微调。"""

    dark = manager.is_dark()
    tokens = manager.tokens()

    # 1) 圆角
    for old, new in _RADIUS_MAP.items():
        css = css.replace(f"border-radius: {old}", f"border-radius: {new}")

    # 2) primary 上的文字：浅色主题白字，深色主题黑字
    on_accent = ON_ACCENT["dark" if dark else "light"]
    css = css.replace(
        'Button[variant="primary"] QLabel#buttonText { color: #FFFFFF;',
        f'Button[variant="primary"] QLabel#buttonText {{ color: {on_accent};',
    )
    css = css.replace(
        'Button[variant="primary"] QLabel[role="kbd"] { color: #FFFFFF; background: rgba(255, 255, 255, 40); }',
        f'Button[variant="primary"] QLabel[role="kbd"] {{ color: {on_accent}; '
        f"background: rgba({_rgb(on_accent)}, 40); }}",
    )
    css = css.replace(
        'TabItem QLabel#tabCount[selected="true"] { color: #FFFFFF;',
        f'TabItem QLabel#tabCount[selected="true"] {{ color: {on_accent};',
    )

    # 3) Tabs：轨道用 muted，选中项是白色小卡片（shadcn Tabs）
    css = _sub_rule(
        css,
        r"TabStrip ",
        "TabStrip { background: %s; border: 0; border-radius: 8px; }" % tokens["control_hover"],
    )
    css = _sub_rule(
        css,
        r"TabItem:hover ",
        "TabItem:hover { background: %s; }" % tokens["control_pressed"],
    )

    # 4) 任务行选中态：muted 色块，不要描边
    css = _sub_rule(
        css,
        r'TaskRow\[selected="true"\] ',
        'TaskRow[selected="true"] { background: %s; border-color: transparent; }'
        % tokens["control_hover"],
    )

    # 5) 侧边栏选中项：6px 圆角、无描边（由 _patch_selection_painters 绘制）
    css = _sub_rule(css, r"NavItem ", "NavItem { background: transparent; border: 0; border-radius: 6px; }")

    # 6)（旧版两栏任务页的样式已移除，任务页改由 app_ui/motion.py 自绘）

    # 7) 自己的应用外壳（app_ui/window.py）
    css += """
QWidget#appRoot, QWidget#appContent { background: %(window)s; }
QFrame#appSidebar { background: %(sidebar)s; border-right: 1px solid %(border)s; }
QFrame#appStatusBar { background: %(window)s; border-bottom: 1px solid %(border)s; }
QStackedWidget#appStack { background: transparent; }
""" % {
        "window": tokens["window"], "sidebar": tokens["sidebar"], "border": tokens["border"],
    }

    # 8) 任务列表（手风琴）与登录框
    css += """
QFrame#taskGroup { background: transparent; border: 1px solid %(border)s; border-radius: 10px; }
QLabel#taskName { font-size: 13.5px; font-weight: 500; }
QLabel#taskDesc { color: %(text_tertiary)s; font-size: 12px; }
QLabel#taskStatus { color: %(text_secondary)s; font-size: 12px; }
QFrame#authCard { background: %(window)s; border: 1px solid %(border_strong)s; border-radius: 14px; }
QLabel#authError { color: %(danger)s; font-size: 12px; }
QLabel#userAvatar {
    background: %(hover)s; color: %(text_secondary)s; border-radius: 12px;
    font-size: 11px; font-weight: 600;
}
""" % {
        "border": tokens["border"],
        "hover": tokens["control_hover"],
        "text_secondary": tokens["text_secondary"], "text_tertiary": tokens["text_tertiary"],
        "window": tokens["window"], "border_strong": tokens["border_strong"],
        "danger": tokens["danger"],
    }

    return css


def _rgb(hex_color: str) -> str:
    value = hex_color.lstrip("#")
    return ", ".join(str(int(value[i : i + 2], 16)) for i in (0, 2, 4))


def _force_flat_backdrop(theme_module) -> None:
    """shadcn 是纯色平面，不要 Mica/Acrylic 毛玻璃。"""

    original = theme_module.ThemeManager.set_backdrop

    def set_backdrop(self, backdrop):
        return original(self, theme_module.Backdrop.NONE)

    theme_module.ThemeManager.set_backdrop = set_backdrop


def _patch_selection_painters() -> None:
    """侧边栏 / 标签页的「选中」是自绘的，改成 shadcn 的柔和色块。"""

    import importlib

    from PySide6.QtCore import QPoint, QRectF
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QFrame, QWidget

    from ok.ui.qt.kit.motion import paint_rounded

    navigation = importlib.import_module("ok.ui.qt.kit.navigation")
    tabs = importlib.import_module("ok.ui.qt.kit.tabs")
    theme_module = importlib.import_module("ok.ui.qt.kit.theme")

    def nav_paint(self, event):
        QWidget.paintEvent(self, event)
        rect = self._indicator.rect()
        if rect is None:
            return
        viewport = self._scroll.viewport()
        top = viewport.mapTo(self, QPoint(0, 0)).y()
        item = self._indicator.item()
        if self._indicator.is_animating() or item.parentWidget() is self:
            clip = QRectF(0, top, self.width(), self.height() - top)
        else:
            clip = QRectF(0, top, self.width(), viewport.height())
        rect = rect.intersected(clip)
        if rect.isEmpty():
            return
        paint_rounded(self, rect, 6, QColor(theme_module.theme.color("control_hover")))

    def tab_paint(self, event):
        QFrame.paintEvent(self, event)
        rect = self._indicator.rect()
        if rect is None:
            return
        if self._look == "underline":
            paint_rounded(self, rect, 1, QColor(theme_module.theme.color("accent")))
            return
        paint_rounded(
            self,
            rect,
            6,
            QColor(theme_module.theme.color("surface")),
            QColor(theme_module.theme.color("border")),
        )

    navigation.NavigationView.paintEvent = nav_paint
    tabs.TabStrip.paintEvent = tab_paint


def apply() -> None:
    """在创建主窗口之前调用。"""

    import importlib

    # 注意：ok.ui.qt.kit 包的 __init__ 把 `theme` 导出成了单例，
    # 必须走 importlib 才能拿到 theme / qss 两个真模块。
    kit_theme = importlib.import_module("ok.ui.qt.kit.theme")
    kit_qss = importlib.import_module("ok.ui.qt.kit.qss")

    kit_theme.LIGHT_TOKENS.clear()
    kit_theme.LIGHT_TOKENS.update(LIGHT)
    kit_theme.DARK_TOKENS.clear()
    kit_theme.DARK_TOKENS.update(DARK)

    kit_theme.FONT_FAMILY = FONT_FAMILY
    kit_theme.MONO_FONT_FAMILY = MONO_FONT_FAMILY
    # qss.py 在 import 时就把字体名绑定了，要一起替换
    kit_qss.FONT_FAMILY = FONT_FAMILY
    kit_qss.MONO_FONT_FAMILY = MONO_FONT_FAMILY

    original_build = kit_qss.build_stylesheet

    def build(manager):
        return _restyle(original_build(manager), manager)

    kit_qss.build_stylesheet = build

    _force_flat_backdrop(kit_theme)
    _patch_selection_painters()

    # primary 用 shadcn 的反色，并且锁住（设置页的强调色选择器不再生效）
    def accent_color(self):
        from PySide6.QtGui import QColor

        return QColor(ACCENT["dark" if self.is_dark() else "light"])

    kit_theme.ThemeManager.accent = accent_color
    kit_theme.ThemeManager.set_accent = lambda self, color: None
