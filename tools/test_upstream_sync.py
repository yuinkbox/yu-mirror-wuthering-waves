"""upstream_sync.py 的离线单元测试。

运行：
    .\\.venv\\Scripts\\python.exe tools\\test_upstream_sync.py -v
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).with_name("upstream_sync.py")
SPEC = importlib.util.spec_from_file_location("upstream_sync", MODULE_PATH)
sync = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = sync
SPEC.loader.exec_module(sync)


def sha(data: bytes) -> str:
    return sync.sha256_bytes(data)


class FakeFetcher:
    def __init__(self, commit_sha: str, tree: dict, files: dict[str, bytes], requirements: bytes = b""):
        self.commit_sha = commit_sha
        self._tree = tree
        self._files = files
        self._requirements = requirements

    def commit(self, ref: str) -> dict:
        return {"sha": self.commit_sha, "date": "2026-01-01T00:00:00Z", "message": "test"}

    def tree(self, sha_value: str) -> dict:
        return self._tree

    def raw(self, ref: str, path: str) -> bytes:
        if path == "requirements.txt":
            return self._requirements
        if path not in self._files:
            raise sync.FetchError(f"missing {path}")
        return self._files[path]

    def raw_text(self, ref: str, path: str) -> str:
        return self.raw(ref, path).decode("utf-8")


def make_lock(root: Path, files: dict[str, bytes], tree: dict) -> dict:
    lock = {
        "schema": 1,
        "upstream": {
            "repo": "fake/repo",
            "branch": "master",
            "base_commit": "b" * 40,
            "base_commit_short": "bbbbbbbb",
            "base_date": "2026-01-01T00:00:00Z",
            "base_message": "base",
        },
        "managed_roots": ["src/", "assets/", "i18n/"],
        "excluded": [],
        "files": {},
        "preserved_deletions": {},
    }
    for path, data in files.items():
        lock["files"][path] = {
            "upstream_blob": tree[path]["blob"],
            "local_sha256": sha(data),
            "synced_from": "bbbbbbbb",
        }
    sync.save_lock(root, lock)
    return lock


def make_args(**overrides):
    values = dict(
        paths=["src"],
        ref="target",
        repo=None,
        timeout=10,
        allow_deletes=False,
        allow_dep_mismatch=False,
        yes=True,
        backup=True,
        json=False,
        force=False,
        exclude=[],
        base="b" * 40,
        branch="master",
        save_requirements=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class BuildChangesTest(unittest.TestCase):
    def test_clean_modified_deleted_added(self):
        base = {
            "src/a.py": {"upstream_blob": "a", "local_sha256": "hash-a"},
            "src/b.py": {"upstream_blob": "b", "local_sha256": "hash-b"},
            "src/c.py": {"upstream_blob": "c", "local_sha256": "hash-c"},
        }
        target = {
            "src/a.py": {"blob": "a2", "size": 10},
            "src/b.py": {"blob": "b2", "size": 20},
            "src/e.py": {"blob": "e", "size": 30},
        }
        local = {"src/a.py": "hash-a", "src/b.py": "hash-b-local", "src/c.py": "hash-c"}
        changes = sync.build_changes(base, target, ("src/",), (), {}, local)
        sync.classify_changes(changes, allow_deletes=False)
        by_path = {change.path: change for change in changes}
        self.assertEqual(by_path["src/a.py"].status, "update")
        self.assertEqual(by_path["src/b.py"].status, "conflict")
        self.assertEqual(by_path["src/c.py"].status, "delete-preserve")
        self.assertEqual(by_path["src/e.py"].status, "add")
        self.assertEqual(len(changes), 4)

    def test_preserved_deletion_is_skipped(self):
        base = {"src/c.py": {"upstream_blob": "c", "local_sha256": "hash-c"}}
        changes = sync.build_changes(
            base, {}, ("src/",), (), {"src/c.py": {"upstream_blob": "c"}}, {"src/c.py": "hash-c"}
        )
        self.assertEqual(changes, [])

    def test_allow_deletes(self):
        base = {"src/c.py": {"upstream_blob": "c", "local_sha256": "hash-c"}}
        changes = sync.build_changes(base, {}, ("src/",), (), {}, {"src/c.py": "hash-c"})
        sync.classify_changes(changes, allow_deletes=True)
        self.assertEqual(changes[0].status, "delete")


class ParsingTest(unittest.TestCase):
    def test_parse_pins(self):
        text = "ok-script[ocr,qt]==3.0.4\nnumpy==2.5.2\n# comment\nfoo>=1.0\n"
        pins = sync.parse_pins(text)
        self.assertEqual(pins["ok-script"], "3.0.4")
        self.assertEqual(pins["numpy"], "2.5.2")
        self.assertNotIn("foo", pins)

    def test_parse_task_config(self):
        text = (
            "config = {\n"
            "  'onetime_tasks': [['src.task.A', 'A'], ['src.task.B', 'B']],\n"
            "  'trigger_tasks': [['src.task.T', 'T']],\n"
            "}\n"
        )
        result = sync.parse_task_config(text)
        self.assertEqual(result["onetime_tasks"], {("src.task.A", "A"), ("src.task.B", "B")})
        self.assertEqual(result["trigger_tasks"], {("src.task.T", "T")})

    def test_git_blob_sha(self):
        self.assertEqual(
            sync.git_blob_sha(b"hello\n"),
            "ce013625030ba8dba906f756967f9e9ca394464a",
        )


class InitTest(unittest.TestCase):
    def test_init_records_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            data = b"value = 1\n"
            (root / "src" / "a.py").write_bytes(data)
            tree = {"src/a.py": {"blob": sync.git_blob_sha(data), "size": len(data)}}
            fake = FakeFetcher("b" * 40, tree, {})
            original = sync.GitHubFetcher
            sync.GitHubFetcher = lambda *a, **k: fake
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    code = sync.cmd_init(make_args(), root)
            finally:
                sync.GitHubFetcher = original
            self.assertEqual(code, 0)
            lock = sync.load_lock(root)
            self.assertEqual(lock["upstream"]["base_commit"], "b" * 40)
            self.assertEqual(lock["files"]["src/a.py"]["local_sha256"], sha(data))
            self.assertEqual(lock["files"]["src/a.py"]["upstream_blob"], sync.git_blob_sha(data))


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        self.old = b"old = 1\n"
        self.new = b"new = 2\n"
        self.old_path = self.root / "src" / "a.py"
        self.old_path.write_bytes(self.old)
        self.tree = {"src/a.py": {"blob": sync.git_blob_sha(self.old), "size": len(self.old)}}
        make_lock(self.root, {"src/a.py": self.old}, self.tree)
        self.target_tree = {"src/a.py": {"blob": sync.git_blob_sha(self.new), "size": len(self.new)}}
        (self.root / "requirements.txt").write_text("ok-script==3.0.4\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_apply(self, fake: FakeFetcher):
        args = make_args()
        original = sync.GitHubFetcher
        sync.GitHubFetcher = lambda *a, **k: fake
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return sync.cmd_apply(args, self.root)
        finally:
            sync.GitHubFetcher = original

    def test_update_clean_file_and_backup(self):
        fake = FakeFetcher(
            "c" * 40,
            self.target_tree,
            {"src/a.py": self.new},
            requirements=b"ok-script==3.0.4\n",
        )
        self.assertEqual(self.run_apply(fake), 0)
        self.assertEqual(self.old_path.read_bytes(), self.new)
        lock = sync.load_lock(self.root)
        self.assertEqual(lock["files"]["src/a.py"]["local_sha256"], sha(self.new))
        backups = list((self.root / "upstream_backups").rglob("a.py"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.old)

    def test_conflict_is_not_overwritten(self):
        self.old_path.write_bytes(b"local change\n")
        fake = FakeFetcher(
            "c" * 40,
            self.target_tree,
            {"src/a.py": self.new},
            requirements=b"ok-script==3.0.4\n",
        )
        self.assertEqual(self.run_apply(fake), 0)
        self.assertEqual(self.old_path.read_bytes(), b"local change\n")
        pending = list((self.root / "upstream_pending").rglob("a.py"))
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].read_bytes(), self.new)
        lock = sync.load_lock(self.root)
        self.assertNotEqual(lock["files"]["src/a.py"]["local_sha256"], sha(b"local change\n"))

    def test_deletion_is_preserved_by_default(self):
        fake = FakeFetcher("c" * 40, {}, {}, requirements=b"ok-script==3.0.4\n")
        self.assertEqual(self.run_apply(fake), 0)
        self.assertTrue(self.old_path.exists())
        lock = sync.load_lock(self.root)
        self.assertIn("src/a.py", lock["preserved_deletions"])

    def test_dependency_mismatch_blocks_src(self):
        fake = FakeFetcher(
            "c" * 40,
            self.target_tree,
            {"src/a.py": self.new},
            requirements=b"ok-script==9.9.9\n",
        )
        self.assertEqual(self.run_apply(fake), 1)
        self.assertEqual(self.old_path.read_bytes(), self.old)


if __name__ == "__main__":
    unittest.main(verbosity=2)
