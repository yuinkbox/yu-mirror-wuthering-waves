if __name__ == '__main__':
    from app_ui import prelude, splash

    prelude.bootstrap()
    splash.set_progress(0.06, "正在加载组件…")

    from config import config
    from ok import OK

    splash.set_progress(0.40, "加载引擎…")

    # 界面：shadcn 主题 + 本项目自己实现的任务页/关于页
    if config.get('ui_style', 'shadcn') == 'shadcn':
        from ui_theme import shadcn
        shadcn.apply()

    from ui import install as ui_install
    ui_install.install()
    splash.set_progress(0.58, "初始化界面…")

    splash.set_progress(0.66, "初始化设备与识别…")
    ok = OK(config)
    splash.set_progress(0.92, "准备就绪")
    ok.start()
