# 换机开发 · 迁移说明

> 目标：在一台新 Windows 机器上把本项目跑起来并继续开发。
> 本文档随源码包一起分发。

## 1. 新机器需要

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11 x64 |
| Python | **3.12**（3.11/3.13 未验证；`py -3.12` 或 `python` 在 PATH 里即可） |
| 磁盘 | ≥ 3 GB（依赖约 1 GB，模板素材另算） |
| 网络 | 首次安装依赖需要（PyPI） |
| 显卡 | 无强制要求；Intel 核显/独显可用 OpenVINO 加速，N 卡建议装最新驱动 |

## 2. 解压与首次运行

1. 把 `yu-mingchao-pro-src-*.zip` 解压到**纯英文、无空格**的路径（例如 `D:\dev\yu-mingchao-pro`）。
2. 双击 **`start.bat`**：
   - 自动创建 `.venv`（Python 3.12）
   - 自动安装 `requirements.txt`（约 1 GB，只装一次）
   - 启动主程序
3. 首次启动会让你**创建本地账号**（保存在 `configs/accounts.json`，PBKDF2 加盐，不存明文）。
4. 想安静启动（无控制台窗口）：双击 `start-silent.vbs`，或桌面快捷方式。

> 如果 `start.bat` 说找不到 Python 3.12：装 Python 3.12 时勾选 “Add python.exe to PATH”，或改用
> `py -3.12 -m venv .venv` 手动建环境，再执行 `start.bat`。

## 3. 包里没有的东西（按需自取）

为了控制体积（约 40 MB），以下内容**没有**打进安装包：

| 目录 | 体积 | 什么时候需要 | 怎么恢复 |
| --- | --- | --- | --- |
| `ok_templates/` | ~839 MB | 改识别素材、跑部分测试 | 从上游仓库 <https://github.com/ok-oldking/ok-wuthering-waves> 复制该目录 |
| `tests/` | ~124 MB | 跑单元测试 | 同上，从上游仓库复制 |
| `.venv/` | ~560 MB | 不需要 | `start.bat` 首启自动创建 |
| `configs/` `logs/` `cache/` | — | 不需要 | 首次运行自动生成 |

**不含这些也能正常启动和跑任务**——运行时识别素材在 `assets/templates/`。

## 4. 开发入口

| 目的 | 命令 |
| --- | --- |
| 正式启动 | `start.bat` / `.venv\Scripts\python.exe main.py` |
| 调试模式 | `.venv\Scripts\python.exe main_debug.py` |
| 只改界面时预览（独立配置，不干扰正式） | `.venv\Scripts\python.exe dev_preview.py` |
| 直接打开某页 | `set YMP_START_PAGE=settings` 再启动（`settings`/`log`/`about`） |
| 跑测试 | `.\run_tests.ps1`（部分用例需要 `ok_templates/`） |
| 重新生成图标 | `.venv\Scripts\python.exe tools\make_icon.py` |
| 重新生成任务图标 | `.venv\Scripts\python.exe tools\make_icons.py` |

## 5. 调试开关（环境变量）

| 变量 | 作用 |
| --- | --- |
| `YMP_ANIM_SLOW=6` | 入场动效放慢 6 倍，方便观察/录屏 |
| `YMP_SPLASH_MIN_MS=2000` | 启动画面最短停留时长（毫秒） |
| `YMP_DEBUG_FRAMES=1` | 登录窗出现时把屏幕帧存到 `logs/frames/` |
| `YMP_DIALOG_TOPMOST=1` | 登录窗临时置顶（排查遮挡用） |
| `YMP_AUTOLOGIN_USER` / `YMP_AUTOLOGIN_PASS` | 自动登录（仅本地调试） |

## 6. 代码地图

```
yu-mingchao-pro/
├─ app_ui/            应用外壳：窗口/侧边栏/状态条/日志页/设置页/动效/磨砂/启动画面/转场
├─ auth/              本地账号：PBKDF2 存储 + 登录框 + 门禁
├─ ui/                任务页、关于页、页面替换 install.py
├─ ui_theme/shadcn.py shadcn 主题覆盖层（色板/字体/圆角/选中态）
├─ src/               上游 ok-ww 的任务/角色/识别逻辑（不要随意改）
├─ assets/            运行时识别素材
├─ ok_templates/      模板编辑器源图（可选，见第 3 节）
├─ tools/             图标生成脚本
├─ config.py          全局配置（标题/日志/任务注册/窗口/OCR）
├─ docs/images/       README 用的截图
└─ tests/             单元测试
```

改动集中在 `app_ui/`、`auth/`、`ui/`、`ui_theme/`；`src/`、`assets/`、`i18n/` 保持上游实现。

## 7. 已知状态

- 主窗口**没有系统托盘**、没有「计划任务」页面（重写外壳时去掉了，功能未补）。
- 登录窗在深色背景下曾报「小白块」闪烁，已做「窗口=卡片 + 窗口自绘磨砂兜底」两处修复，待验证。
- 自更新已关闭（`update_pyappify` 注释掉），避免被替换成 ok-ww 官方版。

## 8. 上游核心同步

`src/`、`assets/`、`i18n/` 是 ok-ww 上游代码，已用 `upstream.lock.json` 建立基线
（f497bd84，412 个文件，初始 0 个本地修改），并已于 2026-11-18 同步到 `d0a59291`。
同步方式：

```powershell
# 只看更新
.\.venv\Scripts\python.exe tools\upstream_sync.py --check --paths all

# 应用（默认保留上游删除的文件，先备份，冲突不覆盖）
.\.venv\Scripts\python.exe tools\upstream_sync.py --apply --paths src --yes
```

也可以双击 `检查核心更新.bat` / `更新核心代码.bat`。冲突处理、依赖升级和当前上游状态
详见 [UPSTREAM_SYNC.md](UPSTREAM_SYNC.md)。

## 9. 许可

上游 ok-ww 为 **AGPL-3.0**：自用随意；一旦分发（发安装包、公开下载、联网提供服务），
必须继续以 AGPL-3.0 开源完整代码并保留 `LICENSE.txt` / `CREDITS.md` 的署名。
