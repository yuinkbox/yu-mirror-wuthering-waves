#!/usr/bin/env python3
"""Yu-Mirror 上游核心同步工具。

这个项目把自己的界面（app_ui/、auth/、ui/、ui_theme/）和上游 ok-ww 的核心
（src/、assets/、i18n/）分开。本工具只同步核心目录，不动界面和配置。

设计目标：
  * 不要求安装 Git；
  * 以 upstream.lock.json 记录「上次同步时每个文件的本地哈希」；
  * 只有本地文件自上次同步后没被改过，才自动覆盖；
  * 本地改过的文件 = 冲突，绝不静默覆盖；
  * 上游删除的文件默认保留（例如本项目的 TacetEliminationTask）；
  * 应用前备份，应用后做 Python 语法检查。

用法见 UPSTREAM_SYNC.md。
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
LOCK_NAME = "upstream.lock.json"
DEFAULT_REPO = "ok-oldking/ok-wuthering-waves"
DEFAULT_BRANCH = "master"
DEFAULT_BASE = "f497bd8400372ac514b810700241a663ff53b79f"
MANAGED_ROOTS = ("src/", "assets/", "i18n/")
DEFAULT_EXCLUDED = ("src/gui/CharacterCodeTab.py",)
DEP_WATCH = ("ok-script", "ok-pyside-minimal", "onnxocr-ppocrv5", "onnxocr-ppocrv6", "pyappify")
PROJECT_ROOT = Path(__file__).resolve().parents[1]

STATUS_LABELS = {
    "update": "更新",
    "add": "新增",
    "conflict": "冲突",
    "delete": "删除",
    "delete-preserve": "保留(上游已删)",
    "noop": "无需操作",
}


class SyncError(RuntimeError):
    """可以直接展示给用户的错误。"""


class FetchError(SyncError):
    pass


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha(data: bytes) -> str:
    """Git 的对象哈希，用于校验从镜像下载的内容。"""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def short(sha: str | None) -> str:
    return (sha or "")[:8]


def human_size(size: int | None) -> str:
    if not size:
        return "0 B"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def is_excluded(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/"):
            if path.startswith(pattern):
                return True
        elif any(ch in pattern for ch in "*?["):
            if fnmatch.fnmatch(path, pattern):
                return True
        elif path == pattern:
            return True
    return False


def in_roots(path: str, roots: tuple[str, ...] | list[str]) -> bool:
    return any(path.startswith(root) for root in roots)


def roots_from_names(names: list[str]) -> tuple[str, ...]:
    mapping = {
        "src": ("src/",),
        "assets": ("assets/",),
        "i18n": ("i18n/",),
        "all": MANAGED_ROOTS,
    }
    result: list[str] = []
    for name in names:
        for root in mapping[name]:
            if root not in result:
                result.append(root)
    return tuple(result)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".upstream-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# GitHub 访问（API + raw 多镜像回退）
# ---------------------------------------------------------------------------

class GitHubFetcher:
    API_ROOT = "https://api.github.com/repos"

    def __init__(self, repo: str, timeout: int = 30, token: str | None = None):
        self.repo = repo
        self.timeout = timeout
        self.token = token or os.environ.get("GITHUB_TOKEN")

    def _get(self, url: str, *, api: bool = False) -> bytes:
        headers = {
            "User-Agent": "YuMirror-UpstreamSync/1.0",
            "Accept": "application/vnd.github+json" if api else "*/*",
        }
        if api and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        context = ssl.create_default_context()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            body = ""
            try:
                body = error.read(600).decode("utf-8", "replace")
            except Exception:
                pass
            if error.code == 403 and "rate limit" in body.lower():
                raise FetchError(
                    "GitHub API 速率限制已用完。请设置 GITHUB_TOKEN 后重试，"
                    "或等一小时后再检查。"
                ) from error
            raise FetchError(f"HTTP {error.code}: {url}\n{body}") from error
        except Exception as error:
            raise FetchError(f"{type(error).__name__}: {error}\n{url}") from error

    def _api(self, endpoint: str) -> dict:
        url = f"{self.API_ROOT}/{self.repo}/{endpoint}"
        try:
            return json.loads(self._get(url, api=True))
        except FetchError as first_error:
            # gh-proxy 通常能转发 API；只在网络错误时尝试，避免绕过速率限制。
            proxy_url = "https://gh-proxy.com/" + url
            try:
                return json.loads(self._get(proxy_url, api=True))
            except Exception:
                raise first_error

    def commit(self, ref: str) -> dict:
        # 分支/标签优先走 Atom feed：不占 GitHub API 配额，限流时也能判断
        # “有没有新提交”。40 位 SHA 没有 feed，直接用 API。
        if not re.fullmatch(r"[0-9a-fA-F]{40}", ref):
            try:
                return self.latest_commit(ref)
            except FetchError:
                pass
        data = self._api(f"commits/{urllib.parse.quote(ref, safe='')}")
        commit = data.get("commit", {})
        message = (commit.get("message") or "").splitlines()
        return {
            "sha": data["sha"],
            "date": commit.get("author", {}).get("date", ""),
            "message": message[0] if message else "",
        }

    def latest_commit(self, ref: str) -> dict:
        """通过 commits/<ref>.atom 读取最新提交，避免 API 限流。"""
        quoted = urllib.parse.quote(ref, safe="")
        urls = [
            f"https://gh-proxy.com/https://github.com/{self.repo}/commits/{quoted}.atom",
            f"https://github.com/{self.repo}/commits/{quoted}.atom",
        ]
        last_error: Exception | None = None
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        for url in urls:
            try:
                root = ET.fromstring(self._get(url))
                entry = root.find("atom:entry", namespace)
                if entry is None:
                    continue
                identifier = entry.findtext("atom:id", default="", namespaces=namespace) or ""
                sha = identifier.rsplit("/", 1)[-1]
                if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                    continue
                title = " ".join((entry.findtext("atom:title", default="", namespaces=namespace) or "").split())
                updated = entry.findtext("atom:updated", default="", namespaces=namespace) or ""
                return {"sha": sha.lower(), "date": updated, "message": title}
            except Exception as error:
                last_error = error
        raise FetchError(f"无法读取上游提交 feed：{last_error}")

    def tree(self, sha: str) -> dict[str, dict]:
        data = self._api(f"git/trees/{sha}?recursive=1")
        if data.get("truncated"):
            raise FetchError("上游目录树过大，GitHub API 返回了截断结果，无法安全同步。")
        return {
            item["path"]: {"blob": item["sha"], "size": item.get("size", 0)}
            for item in data.get("tree", [])
            if item.get("type") == "blob"
        }

    def raw(self, ref: str, path: str) -> bytes:
        quoted = urllib.parse.quote(path, safe="/")
        candidates = [
            f"https://raw.githubusercontent.com/{self.repo}/{ref}/{quoted}",
            f"https://gh-proxy.com/https://raw.githubusercontent.com/{self.repo}/{ref}/{quoted}",
            f"https://cdn.jsdelivr.net/gh/{self.repo}@{ref}/{quoted}",
        ]
        last_error: Exception | None = None
        for url in candidates:
            try:
                data = self._get(url)
                if data.lstrip().lower().startswith(b"<!doctype html") or data.lstrip().lower().startswith(b"<html"):
                    raise FetchError("镜像返回了 HTML 错误页")
                return data
            except Exception as error:
                last_error = error
                time.sleep(0.2)
        raise FetchError(f"无法下载 {path}（已尝试 {len(candidates)} 个镜像）：{last_error}")

    def raw_text(self, ref: str, path: str) -> str:
        return self.raw(ref, path).decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 锁文件
# ---------------------------------------------------------------------------

def lock_path(root: Path) -> Path:
    return root / LOCK_NAME


def load_lock(root: Path) -> dict:
    path = lock_path(root)
    if not path.exists():
        raise SyncError(
            f"未找到 {LOCK_NAME}。首次使用请先初始化：\n"
            f"  python tools/upstream_sync.py --init --base {DEFAULT_BASE}"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SyncError(f"{LOCK_NAME} 不是合法 JSON：{error}") from error
    if data.get("schema") != SCHEMA:
        raise SyncError(f"{LOCK_NAME} 版本不兼容（schema={data.get('schema')}）。")
    data.setdefault("files", {})
    data.setdefault("preserved_deletions", {})
    data.setdefault("excluded", list(DEFAULT_EXCLUDED))
    return data


def save_lock(root: Path, lock: dict) -> None:
    payload = json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write(lock_path(root), payload.encode("utf-8"))


# ---------------------------------------------------------------------------
# 本地文件 / 变更计划
# ---------------------------------------------------------------------------

def collect_local_hashes(
    root: Path,
    roots: tuple[str, ...] | list[str],
    excluded: tuple[str, ...] | list[str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for root_name in roots:
        base = root / root_name.rstrip("/")
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
                continue
            if is_excluded(rel, excluded):
                continue
            result[rel] = sha256_file(path)
    return result


@dataclass
class Change:
    path: str
    kind: str  # added / modified / deleted
    base_blob: str | None
    target_blob: str | None
    target_size: int
    local_sha: str | None
    expected_sha: str | None
    status: str = ""
    reason: str = ""


def build_changes(
    base_files: dict,
    target_tree: dict[str, dict],
    roots: tuple[str, ...] | list[str],
    excluded: tuple[str, ...] | list[str],
    preserved: dict,
    local_hashes: dict[str, str],
) -> list[Change]:
    paths = set(base_files) | set(target_tree)
    changes: list[Change] = []
    for path in sorted(paths):
        if not in_roots(path, roots) or is_excluded(path, excluded):
            continue
        base_entry = base_files.get(path) or {}
        base_blob = base_entry.get("upstream_blob")
        target_entry = target_tree.get(path) or {}
        target_blob = target_entry.get("blob")
        if base_blob == target_blob:
            continue
        if target_blob is None and path in preserved:
            continue
        if base_blob is None:
            kind = "added"
        elif target_blob is None:
            kind = "deleted"
        else:
            kind = "modified"
        changes.append(
            Change(
                path=path,
                kind=kind,
                base_blob=base_blob,
                target_blob=target_blob,
                target_size=int(target_entry.get("size") or 0),
                local_sha=local_hashes.get(path),
                expected_sha=base_entry.get("local_sha256"),
            )
        )
    return changes


def classify_changes(changes: list[Change], allow_deletes: bool) -> list[Change]:
    for change in changes:
        if change.kind == "deleted":
            if change.local_sha is None:
                change.status = "noop"
                change.reason = "本地已经不存在"
            elif not allow_deletes:
                change.status = "delete-preserve"
                change.reason = "上游删除，但本工具默认保留"
            elif change.local_sha == change.expected_sha:
                change.status = "delete"
                change.reason = "上游删除，本地未修改"
            else:
                change.status = "conflict"
                change.reason = "上游删除，但本地已修改"
        else:
            if change.local_sha is None:
                change.status = "add"
                change.reason = "上游新增，本地不存在"
            elif change.local_sha == change.expected_sha:
                change.status = "update"
                change.reason = "上游修改，本地未修改"
            else:
                change.status = "conflict"
                change.reason = "本地已修改，不能自动覆盖"
    return changes


# ---------------------------------------------------------------------------
# requirements / config 兼容性检查
# ---------------------------------------------------------------------------

def parse_pins(text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^\s;]+)")
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def dependency_report(root: Path, fetcher: GitHubFetcher, ref: str, quiet: bool = False) -> list[str]:
    local_path = root / "requirements.txt"
    if not local_path.exists():
        return []
    try:
        target_text = fetcher.raw_text(ref, "requirements.txt")
    except SyncError as error:
        if not quiet:
            print(f"[警告] 无法读取上游 requirements.txt：{error}")
        return []
    local = parse_pins(read_text(local_path))
    target = parse_pins(target_text)
    blockers: list[str] = []
    for package in DEP_WATCH:
        old = local.get(package)
        new = target.get(package)
        if old != new:
            blockers.append(f"{package}: {old or '-'} -> {new or '-'}")
    if blockers and not quiet:
        print("\n[依赖变化]")
        for item in blockers:
            print("  !", item)
        print("  上游核心代码可能依赖新的接口；默认拒绝应用 src/ 变更。")
        print("  建议先在项目副本里升级依赖并验证，再加 --allow-dep-mismatch。")
    return blockers


def parse_task_config(text: str) -> dict[str, set[tuple[str, str]]]:
    result: dict[str, set[tuple[str, str]]] = {"onetime_tasks": set(), "trigger_tasks": set()}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return result
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "config" for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key_node, value_node in zip(node.value.keys, node.value.values):
            if not isinstance(key_node, ast.Constant) or key_node.value not in result:
                continue
            try:
                pairs = ast.literal_eval(value_node)
                result[key_node.value] = {(str(a), str(b)) for a, b in pairs}
            except Exception:
                continue
    return result


def config_report(root: Path, fetcher: GitHubFetcher, ref: str, quiet: bool = False) -> tuple[set, set]:
    local_path = root / "config.py"
    if not local_path.exists():
        return set(), set()
    try:
        target_text = fetcher.raw_text(ref, "config.py")
    except SyncError as error:
        if not quiet:
            print(f"[警告] 无法读取上游 config.py：{error}")
        return set(), set()
    local = parse_task_config(read_text(local_path))
    target = parse_task_config(target_text)
    local_all = local["onetime_tasks"] | local["trigger_tasks"]
    target_all = target["onetime_tasks"] | target["trigger_tasks"]
    added = target_all - local_all
    removed = local_all - target_all
    if (added or removed) and not quiet:
        print("\n[任务注册变化]")
        for module, cls in sorted(added):
            print(f"  + {module}.{cls}（上游新增，本地 config.py 未注册）")
        for module, cls in sorted(removed):
            print(f"  - {module}.{cls}（本地仍注册，上游已删除）")
        if added:
            print("  更新后新任务不会自动出现在界面，需要手动加入 config.py 的任务列表。")
        if removed:
            print("  如果保留对应的本地文件，原有任务仍可继续使用，但需要自行维护。")
    return added, removed


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace, root: Path) -> int:
    target = lock_path(root)
    if target.exists() and not args.force:
        raise SyncError(f"{LOCK_NAME} 已存在。要重新建立基线，请加 --force。")
    repo = args.repo or DEFAULT_REPO
    fetcher = GitHubFetcher(repo, timeout=args.timeout)
    print(f"读取上游基线 {args.base} ...")
    commit = fetcher.commit(args.base)
    tree = fetcher.tree(commit["sha"])
    excluded = list(dict.fromkeys([*DEFAULT_EXCLUDED, *args.exclude]))
    local = collect_local_hashes(root, MANAGED_ROOTS, excluded)
    files: dict[str, dict] = {}
    missing: list[str] = []
    modified: list[str] = []
    for path, entry in sorted(tree.items()):
        if not in_roots(path, MANAGED_ROOTS) or is_excluded(path, excluded):
            continue
        local_sha = local.get(path)
        if local_sha is None:
            missing.append(path)
        else:
            disk_blob = git_blob_sha((root / path).read_bytes())
            if disk_blob != entry["blob"]:
                modified.append(path)
        files[path] = {
            "upstream_blob": entry["blob"],
            "local_sha256": local_sha,
            "synced_from": short(commit["sha"]),
        }
    lock = {
        "schema": SCHEMA,
        "upstream": {
            "repo": repo,
            "branch": args.branch,
            "base_commit": commit["sha"],
            "base_commit_short": short(commit["sha"]),
            "base_date": commit["date"],
            "base_message": commit["message"],
            "last_checked_commit": None,
            "last_checked_at": None,
            "last_apply_at": None,
        },
        "managed_roots": list(MANAGED_ROOTS),
        "excluded": excluded,
        "files": files,
        "preserved_deletions": {},
    }
    save_lock(root, lock)
    print(f"已写入 {LOCK_NAME}")
    print(f"  基线: {short(commit['sha'])} {commit['date']} {commit['message']}")
    print(f"  管理文件: {len(files)} 个（src/assets/i18n）")
    print(f"  本地缺失: {len(missing)} 个" + (f"（{', '.join(missing[:5])}）" if missing else ""))
    print(f"  与基线不一致: {len(modified)} 个")
    if modified:
        print("  注意：这些文件会被视为本地修改，之后同步时不会自动覆盖。")
    return 0


# ---------------------------------------------------------------------------
# 检查 / 应用
# ---------------------------------------------------------------------------

def build_plan(root: Path, lock: dict, fetcher: GitHubFetcher, args: argparse.Namespace):
    roots = roots_from_names(args.paths)
    excluded = tuple(lock.get("excluded") or DEFAULT_EXCLUDED)
    target = fetcher.commit(args.ref)
    target_tree = fetcher.tree(target["sha"])
    local = collect_local_hashes(root, roots, excluded)
    changes = build_changes(
        lock["files"], target_tree, roots, excluded,
        lock.get("preserved_deletions", {}), local,
    )
    classify_changes(changes, args.allow_deletes)
    return roots, target, target_tree, local, changes


def print_plan(lock: dict, roots: tuple[str, ...], target: dict, changes: list[Change]) -> None:
    upstream = lock.get("upstream", {})
    print("\nYu-Mirror 核心同步")
    print(f"  上游: {upstream.get('repo', DEFAULT_REPO)}")
    print(f"  基线: {short(upstream.get('base_commit'))} {upstream.get('base_date', '')}")
    print(f"  目标: {short(target['sha'])} {target.get('date', '')} {target.get('message', '')}")
    print(f"  范围: {', '.join(roots)}")
    counts = {status: 0 for status in STATUS_LABELS}
    for change in changes:
        counts[change.status] = counts.get(change.status, 0) + 1
    parts = [f"{STATUS_LABELS[key]} {value}" for key, value in counts.items() if value]
    print(f"  结果: {'，'.join(parts) if parts else '没有变化'}")
    active = [change for change in changes if change.status not in ("noop",)]
    if not active:
        return
    print()
    for change in active:
        prefix = {
            "update": "~",
            "add": "+",
            "conflict": "!",
            "delete": "-",
            "delete-preserve": "=",
        }.get(change.status, "?")
        size = f"  {human_size(change.target_size)}" if change.target_size else ""
        print(f"  {prefix} [{STATUS_LABELS[change.status]}] {change.path}{size}")
        if change.status in ("conflict", "delete-preserve"):
            print(f"      {change.reason}")


def confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes", "是")
    except (EOFError, KeyboardInterrupt):
        return False


def backup_file(root: Path, path: Path, backup_root: Path) -> None:
    if not path.exists():
        return
    rel = path.relative_to(root)
    destination = backup_root / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)


def prune_backups(root: Path, keep: int = 10) -> None:
    backup_root = root / "upstream_backups"
    if not backup_root.exists():
        return
    directories = sorted(
        (path for path in backup_root.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old in directories[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def write_pending(root: Path, target_short: str, path: str, data: bytes) -> Path:
    destination = root / "upstream_pending" / target_short / path
    atomic_write(destination, data)
    return destination


def cmd_check(args: argparse.Namespace, root: Path) -> int:
    lock = load_lock(root)
    fetcher = GitHubFetcher(args.repo or lock.get("upstream", {}).get("repo", DEFAULT_REPO), timeout=args.timeout)
    roots = roots_from_names(args.paths)
    upstream = lock.get("upstream", {})
    last_target = upstream.get("last_target_commit")
    last_roots = set(upstream.get("last_target_roots") or [])

    # 先用 Atom feed 判断“有没有新提交”。这一步不占 GitHub API 配额，
    # 限流时也能正常回答“已是最新”。
    latest = None
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.ref):
        try:
            latest = fetcher.latest_commit(args.ref)
        except FetchError:
            latest = None

    if latest and last_target and latest["sha"] == last_target and set(roots) <= last_roots:
        target = latest
        changes: list[Change] = []
        if args.json:
            print(json.dumps({
                "ok": True,
                "up_to_date": True,
                "light": True,
                "base": upstream.get("base_commit"),
                "target": target,
                "roots": list(roots),
                "changes": [],
                "dependency_changes": [],
                "config_added": [],
                "config_removed": [],
                "src_blocked": False,
            }, ensure_ascii=False, indent=2))
            return 0
        print_plan(lock, roots, target, changes)
        print(f"\n已是最新核心（ok-ww @ {short(target['sha'])}），没有需要应用的更新。")
        return 0

    try:
        roots, target, _tree, _local, changes = build_plan(root, lock, fetcher, args)
    except FetchError as error:
        if not latest:
            raise
        if args.json:
            print(json.dumps({
                "ok": False,
                "up_to_date": False,
                "light": True,
                "error": str(error),
                "base": upstream.get("base_commit"),
                "target": latest,
                "roots": list(roots),
                "changes": [],
            }, ensure_ascii=False, indent=2))
            return 0
        print(f"\n发现上游新提交 {short(latest['sha'])}，但暂时无法获取文件列表：\n{error}")
        print("\n请稍后重试，或直接运行「更新核心代码.bat」。")
        return 0

    src_changes = [change for change in changes if change.path.startswith("src/") and change.status in ("update", "add", "delete")]

    if args.save_requirements:
        try:
            data = fetcher.raw(target["sha"], "requirements.txt")
            atomic_write(root / "requirements.upstream.txt", data)
            if not args.json:
                print(f"已保存上游依赖清单: requirements.upstream.txt（{target['sha'][:8]}）")
        except SyncError as error:
            print(f"[警告] 无法保存上游 requirements.txt：{error}")

    if args.json:
        config_added, config_removed = config_report(root, fetcher, target["sha"], quiet=True)
        blockers = dependency_report(root, fetcher, target["sha"], quiet=True)
        print(json.dumps({
            "ok": True,
            "up_to_date": False,
            "light": False,
            "base": lock.get("upstream", {}).get("base_commit"),
            "target": target,
            "roots": list(roots),
            "changes": [change.__dict__ for change in changes],
            "dependency_changes": blockers,
            "config_added": sorted(".".join(item) for item in config_added),
            "config_removed": sorted(".".join(item) for item in config_removed),
            "src_blocked": bool(src_changes and blockers),
        }, ensure_ascii=False, indent=2))
        return 0

    print_plan(lock, roots, target, changes)
    config_report(root, fetcher, target["sha"])
    blockers = dependency_report(root, fetcher, target["sha"])
    if src_changes and blockers:
        print("\n[安全拦截] 上游 src/ 有变更，但依赖版本不一致；--apply 默认会拒绝。")
    elif changes:
        print("\n运行 --apply 应用变更。默认保留上游删除的文件。")
    return 0


def cmd_apply(args: argparse.Namespace, root: Path) -> int:
    lock = load_lock(root)
    fetcher = GitHubFetcher(args.repo or lock.get("upstream", {}).get("repo", DEFAULT_REPO), timeout=args.timeout)
    roots, target, _tree, _local, changes = build_plan(root, lock, fetcher, args)
    print_plan(lock, roots, target, changes)
    config_report(root, fetcher, target["sha"])
    blockers = dependency_report(root, fetcher, target["sha"])

    actionable = [change for change in changes if change.status in ("update", "add", "delete")]
    conflicts = [change for change in changes if change.status == "conflict"]
    preserved_changes = [change for change in changes if change.status == "delete-preserve"]

    # 冲突文件先落到 upstream_pending/，这样即使没有可自动应用的变更，
    # 用户也能拿到上游版本做手动合并。
    pending: list[tuple[Change, Path]] = []
    for change in conflicts:
        if not change.target_blob:
            continue
        try:
            data = fetcher.raw(target["sha"], change.path)
            if git_blob_sha(data) != change.target_blob:
                raise FetchError("下载内容哈希不匹配")
            pending_path = write_pending(root, short(target["sha"]), change.path, data)
            pending.append((change, pending_path))
        except SyncError as error:
            print(f"[警告] 无法保存冲突文件 {change.path}: {error}")

    if not actionable:
        if preserved_changes:
            preserved = lock.setdefault("preserved_deletions", {})
            for change in preserved_changes:
                preserved[change.path] = {
                    "upstream_blob": change.base_blob,
                    "local_sha256": change.local_sha,
                    "noted_at": now_iso(),
                }
            save_lock(root, lock)
            print(f"\n已记录保留 {len(preserved_changes)} 个上游删除文件（本地文件未改动）。")
        print("\n没有需要自动应用的变更。")
        if pending:
            print("冲突文件的上游版本已写入 upstream_pending/：")
            for change, pending_path in pending:
                print(f"    {change.path} -> {pending_path.relative_to(root)}")
        return 0

    src_changes = [change for change in actionable if change.path.startswith("src/")]
    if src_changes and blockers and not args.allow_dep_mismatch:
        print("\n[已拒绝] 上游 src/ 依赖版本与本项目不一致。")
        print("确认已在副本中升级依赖并验证后，再加 --allow-dep-mismatch 重试。")
        return 1

    if not args.yes and not confirm("\n确认应用这些变更？[y/N] "):
        print("已取消。")
        return 0

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = root / "upstream_backups" / timestamp
    applied: list[Change] = []
    files = lock.setdefault("files", {})
    preserved = lock.setdefault("preserved_deletions", {})
    upstream = lock.setdefault("upstream", {})

    for change in changes:
        if change.status == "conflict":
            continue
        if change.status == "delete-preserve":
            preserved[change.path] = {
                "upstream_blob": change.base_blob,
                "local_sha256": change.local_sha,
                "noted_at": now_iso(),
            }
            continue
        if change.status == "noop":
            files.pop(change.path, None)
            continue
        if change.status not in ("update", "add", "delete"):
            continue

        local_path = root / change.path
        if args.backup:
            backup_file(root, local_path, backup_root)

        if change.status == "delete":
            if local_path.exists():
                local_path.unlink()
            files.pop(change.path, None)
            preserved.pop(change.path, None)
            applied.append(change)
            continue

        try:
            data = fetcher.raw(target["sha"], change.path)
        except SyncError as error:
            print(f"[失败] {change.path}: {error}")
            continue
        if git_blob_sha(data) != change.target_blob:
            print(f"[失败] {change.path}: 下载内容与上游哈希不一致，已跳过")
            continue
        atomic_write(local_path, data)
        files[change.path] = {
            "upstream_blob": change.target_blob,
            "local_sha256": sha256_bytes(data),
            "synced_from": short(target["sha"]),
        }
        preserved.pop(change.path, None)
        applied.append(change)

    upstream["last_target_commit"] = target["sha"]
    upstream["last_target_short"] = short(target["sha"])
    upstream["last_target_date"] = target.get("date")
    upstream["last_target_roots"] = list(roots)
    upstream["last_checked_commit"] = target["sha"]
    upstream["last_checked_at"] = now_iso()
    if applied:
        upstream["last_apply_at"] = now_iso()
    save_lock(root, lock)
    prune_backups(root)

    print("\n[完成]")
    print(f"  应用 {len(applied)} 个文件")
    if applied and args.backup:
        print(f"  备份: {backup_root}")
    if pending:
        print(f"  冲突 {len(pending)} 个，已写入 upstream_pending/：")
        for change, pending_path in pending:
            print(f"    {change.path} -> {pending_path.relative_to(root)}")
    if conflicts:
        print("  冲突文件保持原样，未覆盖。")
    if preserved_changes:
        print(f"  已保留上游删除的文件 {len(preserved_changes)} 个。")

    py_files = [str(root / change.path) for change in applied if change.path.endswith(".py")]
    if py_files:
        result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", *py_files],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("[警告] Python 语法检查未通过：")
            print(result.stdout.strip() or result.stderr.strip())
            print(f"  可从 {backup_root} 恢复。")
        else:
            print("  Python 语法检查通过。")
    print("  重启软件后生效。")
    return 0


# ---------------------------------------------------------------------------
# 状态 / 校验
# ---------------------------------------------------------------------------

def verify_local(root: Path, lock: dict, roots: tuple[str, ...]) -> tuple[list[str], list[str], list[str]]:
    excluded = tuple(lock.get("excluded") or DEFAULT_EXCLUDED)
    local = collect_local_hashes(root, roots, excluded)
    missing: list[str] = []
    modified: list[str] = []
    for path, entry in lock.get("files", {}).items():
        if not in_roots(path, roots) or is_excluded(path, excluded):
            continue
        expected = entry.get("local_sha256")
        if path not in local:
            missing.append(path)
        elif local[path] != expected:
            modified.append(path)
    extra = sorted(set(local) - set(lock.get("files", {})))
    return missing, modified, extra


def cmd_status(args: argparse.Namespace, root: Path) -> int:
    lock = load_lock(root)
    upstream = lock.get("upstream", {})
    print("Yu-Mirror 上游同步状态")
    print(f"  仓库: {upstream.get('repo', DEFAULT_REPO)}")
    print(f"  基线: {short(upstream.get('base_commit'))} {upstream.get('base_date', '')}")
    print(f"  最近检查: {short(upstream.get('last_checked_commit')) or '-'} {upstream.get('last_checked_at') or ''}")
    print(f"  最近应用: {upstream.get('last_apply_at') or '-'}")
    print(f"  管理文件: {len(lock.get('files', {}))} 个")
    print(f"  保留的删除: {len(lock.get('preserved_deletions', {}))} 个")
    roots = roots_from_names(args.paths)
    missing, modified, extra = verify_local(root, lock, roots)
    print(f"  本地缺失: {len(missing)}")
    print(f"  本地修改: {len(modified)}")
    print(f"  未跟踪文件: {len(extra)}")
    for label, items in (("缺失", missing), ("修改", modified), ("未跟踪", extra)):
        for path in items[:12]:
            print(f"    [{label}] {path}")
        if len(items) > 12:
            print(f"    ... 还有 {len(items) - 12} 个")
    return 0


def cmd_verify(args: argparse.Namespace, root: Path) -> int:
    lock = load_lock(root)
    roots = roots_from_names(args.paths)
    missing, modified, extra = verify_local(root, lock, roots)
    print(f"缺失 {len(missing)}，修改 {len(modified)}，未跟踪 {len(extra)}")
    for path in missing:
        print(f"  [缺失] {path}")
    for path in modified:
        print(f"  [修改] {path}")
    for path in extra:
        print(f"  [未跟踪] {path}")
    if missing or modified:
        print("本地核心文件与锁文件记录不一致；同步时这些文件会按冲突处理。")
        return 1
    print("本地核心文件与锁文件一致。")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从上游 ok-ww 同步 src/assets/i18n，保留本项目的界面与配置。",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="建立同步基线")
    mode.add_argument("--check", action="store_true", help="检查上游变更（默认）")
    mode.add_argument("--apply", action="store_true", help="应用上游变更")
    mode.add_argument("--status", action="store_true", help="查看同步状态")
    mode.add_argument("--verify", action="store_true", help="校验本地文件与锁文件")
    parser.add_argument("--base", default=DEFAULT_BASE, help="初始化时的上游基线 commit")
    parser.add_argument("--ref", default=DEFAULT_BRANCH, help="目标分支/标签/commit")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="锁文件记录的上游分支名")
    parser.add_argument("--paths", nargs="+", choices=("src", "assets", "i18n", "all"), default=["src"],
                        help="同步范围，默认 src")
    parser.add_argument("--repo", default=None, help="上游仓库 owner/name")
    parser.add_argument("--root", default=str(PROJECT_ROOT), help="项目根目录")
    parser.add_argument("--timeout", type=int, default=30, help="网络超时秒数")
    parser.add_argument("--exclude", action="append", default=[], help="额外排除的路径/通配符")
    parser.add_argument("--allow-deletes", action="store_true", help="允许应用上游删除")
    parser.add_argument("--allow-dep-mismatch", action="store_true", help="依赖版本不一致时仍应用 src 变更")
    parser.add_argument("--yes", action="store_true", help="跳过确认")
    parser.add_argument("--no-backup", action="store_true", help="不备份被覆盖的文件")
    parser.add_argument("--force", action="store_true", help="允许覆盖已有锁文件")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出检查结果")
    parser.add_argument("--save-requirements", action="store_true", help="把上游 requirements.txt 保存为 requirements.upstream.txt")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    args.backup = not args.no_backup
    try:
        if args.init:
            return cmd_init(args, root)
        if args.status:
            return cmd_status(args, root)
        if args.verify:
            return cmd_verify(args, root)
        if args.apply:
            return cmd_apply(args, root)
        return cmd_check(args, root)
    except SyncError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
