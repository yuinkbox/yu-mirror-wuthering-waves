"""关于页 —— 精简版。

去掉原版里的「相关项目」（其他游戏的 ok-* 列表）和「应用更新」（PyAppify 检查）：
保留应用信息、免责声明、外链按钮和「检查核心更新」入口。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QVBoxLayout, QWidget

from ok import og
from ok.ui.qt.kit import Card, Label, theme

from app_ui.motion import AnimatedButton

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_UPDATE_SCRIPT = _PROJECT_ROOT / "tools" / "upstream_sync.py"
_UPDATE_GUIDE = _PROJECT_ROOT / "UPSTREAM_SYNC.md"


class AboutTab(QWidget):
    """应用信息页。``update_card`` 保持为 None，框架就不会去检查更新。"""

    update_card = None
    update_finished = Signal(int, str, str)

    def __init__(self, config, exit_event=None, parent=None):
        super().__init__(parent)
        self.setObjectName("AboutTab")
        self.config = config or {}
        self.exit_event = exit_event
        self._checking = False
        self.update_finished.connect(self._on_update_checked)

        card = Card()
        icon_label = Label()
        icon_path = self.config.get("gui_icon")
        if icon_path:
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                icon_label.setPixmap(pixmap.scaled(
                    64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        icon_label.setFixedSize(64, 64)

        title = Label(self.config.get("gui_title") or "Yu-Mirror 鸣潮", "title")
        version = Label(f"v{self.config.get('version', '')}", "secondary")
        core = Label(self._core_version_text(), "secondary", word_wrap=True)
        head_text = QVBoxLayout()
        head_text.setSpacing(2)
        head_text.addWidget(title)
        head_text.addWidget(version)
        head_text.addWidget(core)

        head = QHBoxLayout()
        head.setSpacing(16)
        head.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        head.addLayout(head_text, 1)

        links = self._links()
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.check_button = AnimatedButton("检查核心更新", "refresh", "secondary", height=32)
        self.check_button.clicked.connect(self._check_core_update)
        buttons.addWidget(self.check_button)
        guide_button = AnimatedButton("更新说明", "file", "secondary", height=32)
        guide_button.clicked.connect(self._open_update_guide)
        buttons.addWidget(guide_button)
        if links.get("github"):
            buttons.addWidget(self._link_button("GitHub", links["github"], "github"))
        if links.get("faq"):
            buttons.addWidget(self._link_button("常见问题", links["faq"], "info"))
        buttons.addStretch(1)

        body = Label(
            "仅供个人学习使用，请勿商用或收费。\n"
            "使用本软件存在账号风险，请自行判断。",
            "secondary", word_wrap=True,
        )

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        layout.addLayout(head)
        layout.addWidget(body)
        layout.addLayout(buttons)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.addWidget(card)
        outer.addStretch(1)
        theme.install(self)

    # -- 核心版本 / 更新 -----------------------------------------------------

    def _core_commit(self) -> str:
        try:
            lock = json.loads((_PROJECT_ROOT / "upstream.lock.json").read_text(encoding="utf-8"))
            upstream = lock.get("upstream") or {}
            return upstream.get("last_target_short") or upstream.get("base_commit_short") or ""
        except Exception:
            return ""

    def _core_version_text(self) -> str:
        commit = self._core_commit()
        if commit:
            return f"核心代码：ok-ww @ {commit}（关于页可检查更新）"
        return "核心代码：ok-ww（AGPL-3.0）"

    def _check_core_update(self) -> None:
        if self._checking:
            return
        if not _UPDATE_SCRIPT.exists():
            QMessageBox.warning(self, "核心更新", f"找不到同步工具：\n{_UPDATE_SCRIPT}")
            return
        self._checking = True
        self.check_button.set_enabled(False)
        self.check_button.set_text("检查中…")
        threading.Thread(target=self._run_update_check, daemon=True).start()

    def _run_update_check(self) -> None:
        command = [
            sys.executable,
            str(_UPDATE_SCRIPT),
            "--check",
            "--paths", "all",
            "--json",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(_PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.update_finished.emit(completed.returncode, completed.stdout or "", completed.stderr or "")
        except Exception as error:
            try:
                self.update_finished.emit(-1, "", str(error))
            except RuntimeError:
                pass

    def _on_update_checked(self, returncode: int, stdout: str, stderr: str) -> None:
        self._checking = False
        self.check_button.set_enabled(True)
        self.check_button.set_text("检查核心更新")

        if returncode != 0:
            detail = (stderr or stdout or "未知错误").strip()
            self._show_update_dialog(
                "检查失败",
                f"无法检查上游更新：\n\n{detail[-1200:]}",
                QMessageBox.Icon.Warning,
            )
            return

        try:
            data = json.loads(stdout)
        except Exception:
            self._show_update_dialog(
                "检查结果",
                (stdout or stderr or "没有返回内容").strip()[-1600:],
                QMessageBox.Icon.Information,
            )
            return

        changes = data.get("changes") or []
        target = (data.get("target") or {}).get("sha", "")[:8]
        if data.get("up_to_date"):
            self._show_update_dialog(
                "核心已是最新",
                f"当前核心：ok-ww @ {self._core_commit() or target}\n"
                f"上游版本：{target or '未知'}\n\n没有需要应用的更新。",
                QMessageBox.Icon.Information,
            )
            return
        if data.get("error"):
            self._show_update_dialog(
                "发现上游更新",
                f"上游最新：ok-ww @ {target or '未知'}\n\n"
                f"暂时无法获取文件列表：\n{data['error']}\n\n"
                "请稍后重试，或直接运行安装目录里的「更新核心代码.bat」。",
                QMessageBox.Icon.Warning,
            )
            return

        active = [
            change for change in changes
            if change.get("status") in ("update", "add", "delete", "conflict")
        ]
        labels = {
            "update": "更新",
            "add": "新增",
            "delete": "删除",
            "conflict": "冲突",
        }

        if not active:
            self._show_update_dialog(
                "核心已是最新",
                f"当前核心：ok-ww @ {self._core_commit() or target}\n"
                f"上游版本：{target or '未知'}\n\n没有需要应用的更新。",
                QMessageBox.Icon.Information,
            )
            return

        lines = [f"发现上游 {target or '未知'} 的 {len(active)} 个变更：", ""]
        for change in active[:8]:
            label = labels.get(change.get("status"), change.get("status", "?"))
            lines.append(f"· {label}：{change.get('path', '')}")
        if len(active) > 8:
            lines.append(f"… 还有 {len(active) - 8} 个")
        if data.get("src_blocked"):
            lines.extend([
                "",
                "注意：上游依赖版本已变化，核心更新会被安全拦截。",
                "请先按「更新说明」升级依赖并验证，再应用更新。",
            ])
        lines.extend([
            "",
            "更新需要关闭软件，然后运行安装目录里的「更新核心代码.bat」。",
            "更新前会自动备份，冲突文件不会被覆盖。",
        ])
        self._show_update_dialog("发现核心更新", "\n".join(lines), QMessageBox.Icon.Information)

    def _show_update_dialog(self, title: str, text: str, icon: QMessageBox.Icon) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(icon)
        box.setText(text)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_dir = box.addButton("打开安装目录", QMessageBox.ButtonRole.ActionRole)
        guide = box.addButton("更新说明", QMessageBox.ButtonRole.ActionRole)
        box.addButton("关闭", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is open_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(_PROJECT_ROOT)))
        elif box.clickedButton() is guide:
            self._open_update_guide()

    def _open_update_guide(self) -> None:
        if _UPDATE_GUIDE.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(_UPDATE_GUIDE)))
        else:
            QMessageBox.warning(self, "更新说明", f"找不到说明文件：\n{_UPDATE_GUIDE}")

    # -- 其它 ----------------------------------------------------------------

    def _links(self) -> dict:
        raw = self.config.get("links") or {}
        return raw.get("zh_CN") or raw.get("default") or {}

    def _link_button(self, text: str, url: str, icon_name: str) -> AnimatedButton:
        button = AnimatedButton(text, icon_name, "secondary", height=32)
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        return button

