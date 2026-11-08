"""本地账号存储（后续可替换成服务器校验）。

设计成后端可替换：
    AuthStore     本地 JSON + PBKDF2（现在用这个）
    RemoteAuth    以后接你的服务器：实现同样的 has_users / create / verify 即可

存储位置：`configs/accounts.json`
格式：{"version": 1, "users": [{"username": "...", "salt": "<hex>", "hash": "<hex>", "created_at": 0}]}
密码只存 PBKDF2-SHA256 的派生值，不存明文。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

ITERATIONS = 240_000
MIN_USERNAME = 3
MIN_PASSWORD = 6


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)


class AuthStore:
    """本地账号文件。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else Path("configs") / "accounts.json"
        self._data = self._load()

    # -- storage -------------------------------------------------------------

    def _load(self) -> dict:
        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict) and isinstance(data.get("users"), list):
                return data
        except (OSError, ValueError):
            pass
        return {"version": 1, "users": []}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as file:
            json.dump(self._data, file, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # -- api -----------------------------------------------------------------

    def has_users(self) -> bool:
        return bool(self._data["users"])

    def last_username(self) -> str:
        users = self._data["users"]
        return users[-1]["username"] if users else ""

    def _find(self, username: str):
        for user in self._data["users"]:
            if user["username"].casefold() == username.casefold():
                return user
        return None

    def create(self, username: str, password: str) -> tuple[bool, str]:
        username = (username or "").strip()
        if len(username) < MIN_USERNAME:
            return False, f"账号至少 {MIN_USERNAME} 个字符"
        if len(password or "") < MIN_PASSWORD:
            return False, f"密码至少 {MIN_PASSWORD} 位"
        if self._find(username) is not None:
            return False, "该账号已存在"
        salt = os.urandom(16)
        self._data["users"].append({
            "username": username,
            "salt": salt.hex(),
            "hash": _hash(password, salt).hex(),
            "created_at": int(time.time()),
        })
        self._save()
        return True, ""

    def verify(self, username: str, password: str) -> bool:
        user = self._find((username or "").strip())
        if user is None:
            # 账号不存在也走一遍哈希，避免通过响应时间判断账号是否存在
            _hash(password or "", b"\x00" * 16)
            return False
        try:
            salt = bytes.fromhex(user["salt"])
            expected = bytes.fromhex(user["hash"])
        except (KeyError, ValueError):
            return False
        return hmac.compare_digest(_hash(password or "", salt), expected)

    def rename_user(self, old: str, new: str) -> bool:
        user = self._find(old)
        if user is None or self._find(new) is not None:
            return False
        user["username"] = new
        self._save()
        return True


class Session:
    """当前登录用户（单进程内存态）。"""

    current: str | None = None
    remember: bool = False


session = Session()
