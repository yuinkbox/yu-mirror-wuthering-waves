import os

os.environ["PYAPPIFY_PYTHON_TEST"] = "1"
if __name__ == '__main__':
    from app_ui import prelude

    prelude.bootstrap()

    from config import config
    from ok import OK

    if config.get('ui_style', 'shadcn') == 'shadcn':
        from ui_theme import shadcn
        shadcn.apply()

    from ui import install as ui_install
    ui_install.install()

    config = config
    config['debug'] = True
    # config['click_screenshots_folder'] = "click_screenshots"  # debug用 点击后截图文件夹]
    ok = OK(config)
    ok.start()
