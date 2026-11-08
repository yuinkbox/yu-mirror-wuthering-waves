"""UI 预览入口：不影响正在运行的正式实例。

- 关闭单实例互斥（check_mutex），所以可以和已经开着的软件同时运行
- 使用独立的 configs_preview 配置目录，避免改到正式配置
- 同样应用 shadcn 主题

用法：
    .\\.venv\\Scripts\\python.exe dev_preview.py
"""

import os

os.environ["PYAPPIFY_PYTHON_TEST"] = "1"

if __name__ == "__main__":
    from app_ui import prelude

    prelude.bootstrap()

    from config import config
    from ok import OK

    config = dict(config)
    config["check_mutex"] = False
    config["config_folder"] = "configs_preview"
    config["gui_title"] = f"{config['gui_title']} (预览)"

    if config.get("ui_style", "shadcn") == "shadcn":
        from ui_theme import shadcn

        shadcn.apply()

    from ui import install as ui_install

    ui_install.install()

    OK(config).start()
