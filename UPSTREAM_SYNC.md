# 上游核心同步（ok-ww → Yu-Mirror）

本项目从一开始就把代码分成了两层：

| 层 | 目录 | 谁维护 |
| --- | --- | --- |
| 上游核心 | `src/`、`assets/`、`i18n/` | ok-ww 上游 |
| 本项目自定义 | `app_ui/`、`auth/`、`ui/`、`ui_theme/`、`config.py`、启动脚本 | 本项目 |

2026-11-18 核对结果：`src/`、`assets/`、`i18n/` 与上游 **f497bd84**（2026-11-05
`Update ok-script to 3.0.4`）逐字节一致，共 412 个文件，0 个本地修改。
唯一的例外是 `src/gui/CharacterCodeTab.py`——本项目没有使用它，已加入排除列表。

所以核心代码本来就能更新，缺的只是一套「同步 + 防冲突 + 备份」的机制。
`tools/upstream_sync.py` 就是这套机制。

---

## 快速使用

### 软件内入口

打开软件左侧的 **「关于」** 页，点：

- **检查核心更新**：检查上游有没有新提交，弹出结果；只检查，不会覆盖正在运行的程序。
- **更新说明**：打开本文档。

真正应用更新需要关闭软件，再双击安装目录里的 **`更新核心代码.bat`**（原因：正在运行的
Python 进程不能安全地替换自己的源码）。

### 只看有哪些更新

双击 **`检查核心更新.bat`**，或：

```powershell
.\.venv\Scripts\python.exe tools\upstream_sync.py --check --paths all
```

报告会列出：

- `[更新]` 上游改了、本地没改，可以安全覆盖；
- `[新增]` 上游新增的文件；
- `[冲突]` 本地也改过，**不会自动覆盖**，上游版本写到 `upstream_pending/`；
- `[保留(上游已删)]` 上游删除、本地默认保留（例如 `TacetEliminationTask.py`）。

### 应用更新

```powershell
# 只更新核心代码
.\.venv\Scripts\python.exe tools\upstream_sync.py --apply --paths src --yes

# 核心 + 识别素材 + 翻译
.\.venv\Scripts\python.exe tools\upstream_sync.py --apply --paths all --yes
```

也可以双击 **`更新核心代码.bat`**（等价于 `--paths all --apply --yes`）。

应用后：

1. 被覆盖的文件先备份到 `upstream_backups/<时间戳>/`；
2. 冲突文件的本地上游版本写到 `upstream_pending/<commit>/`；
3. 自动跑 `python -m compileall` 检查语法；
4. 重启软件生效。

默认保留上游删除的文件。确认要跟随上游删除，加 `--allow-deletes`。

---

## 同步记录（2026-11-18）

- 已从基线 `f497bd84` 同步到上游 `d0a59291`
  （2026-11-16 `Remove custom character and Tacet Elimination features`）
- 共应用 26 个文件：
  - 19 个更新（9 个 `src/`、10 个 `i18n/`）
  - 7 个新增（5 个 Lily/Hiyuki 模板、2 个 `src/` 文件）
  - 4 个上游删除已记录为「保留」
- 依赖已升级并验证：
  - `ok-script 3.0.4 → 3.0.5b9`
  - `onnxocr-ppocrv5 → onnxocr-ppocrv6`
  - `pyappify 1.0.13 → 1.0.20`
- 当前 `.venv` 已换成新环境；`--check` 现在应显示「没有变化」。
- 旧环境保留在 `.venv-backup-3.0.4/`，确认稳定后可删除。

> 上游 2026-11-16 的提交删除了「自定义角色」和「无音消除」。本地对应文件被保留，
> 但上游 `CharFactory.py` / `BaseCombatTask.py` 已移除挂接点；这两个功能如果还要用，
> 需要额外做兼容补丁。TacetEliminationTask 仍留在任务列表里，属于本地维护功能。

### 以后上游再升级依赖

如果下一次 `--check` 又出现依赖变化，先在副本里验证，不要直接改主目录：

```powershell
# 1. 复制整个项目（不要复制 .venv、configs、logs、cache）
#    例如复制到 D:\Downloads\gameTools\_upstream_test

# 2. 在副本里应用上游核心（工具只用标准库，系统 Python 即可）
cd D:\Downloads\gameTools\_upstream_test
py -3.12 tools\upstream_sync.py --apply --paths all --yes --allow-dep-mismatch

# 3. 在副本里建新虚拟环境，安装上游 requirements
py -3.12 tools\upstream_sync.py --check --save-requirements
py -3.12 -m venv .venv-upstream
.\.venv-upstream\Scripts\python.exe -m pip install -r requirements.upstream.txt

# 4. 用新环境启动，登录/跑一个简单任务，确认界面和战斗正常
.\.venv-upstream\Scripts\python.exe main.py
```

验证通过后，再把副本的 `src/`、`assets/`、`i18n/` 覆盖回主目录，并升级主目录的
`.venv`。验证不通过时主目录完全没动，删除副本即可。

> 如果 `pip install` 遇到 `403 Forbidden`（USTC 镜像里少数新包会跳转到清华），
> 可以单独下载新包（`pip download ... -i https://pypi.org/simple --no-deps`），先
> `pip install --no-deps *.whl`，再重跑 `requirements.upstream.txt`。

---

## 冲突怎么处理

例如本地改过 `src/task/BaseCombatTask.py`，上游也改了：

1. 工具不覆盖本地文件；
2. 上游版本写到 `upstream_pending/<commit>/src/task/BaseCombatTask.py`；
3. 用你习惯的 diff 工具对比两个文件，手动合并；
4. 合并后，本地哈希会与锁文件不同，下次检查仍显示冲突——这是预期行为。
   确认合并完成后，可以重新建立基线（见下）。

## 命令参考

| 命令 | 作用 |
| --- | --- |
| `--check` | 检查上游变更（默认模式） |
| `--apply` | 应用变更 |
| `--status` | 查看基线、最近检查、本地修改数量 |
| `--verify` | 校验本地文件与锁文件是否一致 |
| `--paths` | 选择范围：`src` / `assets` / `i18n` / `all`，可多选，默认 `src` |
| `--allow-deletes` | 允许应用上游删除（默认保留） |
| `--allow-dep-mismatch` | 依赖版本不一致时仍应用 `src` 变更（危险） |
| `--save-requirements` | 把目标 commit 的 `requirements.txt` 保存为 `requirements.upstream.txt` |
| `--json` | 机器可读输出，供界面/脚本使用 |
| `--init --base <commit>` | 重新建立同步基线（加 `--force` 覆盖旧锁） |

> GitHub API 未登录时限制约 60 次/小时；频繁检查或团队使用可先设置环境变量
> `GITHUB_TOKEN`（只读权限即可）。下载文件走 raw/镜像，不占 API 配额。

## 文件说明

| 文件/目录 | 说明 |
| --- | --- |
| `upstream.lock.json` | 同步基线：每个核心文件的上游 blob 与本地 SHA256 |
| `upstream_backups/` | 每次应用前的备份（保留最近 10 次） |
| `upstream_pending/` | 冲突文件的上游版本 |
| `tools/upstream_sync.py` | 同步工具 |
| `tools/test_upstream_sync.py` | 离线单元测试 |

运行测试：

```powershell
.\.venv\Scripts\python.exe tools\test_upstream_sync.py -v
```

## 许可

上游 ok-ww 为 **AGPL-3.0**。同步下来的 `src/`、`assets/`、`i18n/` 仍受该许可约束；
分发本软件时仍需保留 `LICENSE.txt`、`CREDITS.md` 与完整对应源码。
