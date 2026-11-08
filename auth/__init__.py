"""本地账号系统（可替换为服务器校验）。"""

from .dialog import AuthDialog, authenticate
from .store import AuthStore, session

__all__ = ["AuthDialog", "AuthStore", "authenticate", "session"]
