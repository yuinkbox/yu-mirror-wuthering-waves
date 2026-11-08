"""开发/启动入口的公共前置步骤。

顺序很重要：
    1. 建 QApplication（DPI 设置必须在这之前）
    2. **先**把启动画面显示出来并 processEvents，让用户立刻看到东西
    3. 再去导入/替换 ok-script 的 init_app_config（这一步会 import 整个 ok 包，很重）
"""

from __future__ import annotations

import os

from app_ui import splash


def bootstrap(show_splash: bool = True):
    application = splash.ensure_application()

    # 趁屏幕上还没有我们的窗口，先截一张桌面当「磨砂玻璃」的底
    from app_ui import frosted

    frosted.capture_screen_snapshot()

    if show_splash:
        icon = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "icons", "icon.png")
        splash.show_splash(icon if os.path.isfile(icon) else None)

    splash.patch_init_app_config()
    return application
