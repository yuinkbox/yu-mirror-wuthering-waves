"""把任务/分类图标换成 Lucide 线性图标（shadcn 的图标语言）。

- 从 lucide-static 下载官方 SVG（MIT 许可），改成统一描边色后存到 icons/tasks/
- 原彩色 PNG 备份到 icons/tasks/original/，想还原随时拷贝回来
- task_icons.py 会优先读同名 .svg，所以不需要改代码

用法：
    .\\.venv\\Scripts\\python.exe tools\\make_icons.py
"""

from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

ICON_DIR = Path(__file__).resolve().parent.parent / "icons" / "tasks"
BACKUP_DIR = ICON_DIR / "original"
LUCIDE_VERSION = "0.468.0"
BASE_URL = f"https://unpkg.com/lucide-static@{LUCIDE_VERSION}/icons"

# 项目图标名 -> lucide 图标名
MAPPING = {
    "boss": "swords",
    "category-all": "layout-grid",
    "category-daily": "calendar-days",
    "category-echo": "gem",
    "category-onetime": "zap",
    "category-trigger": "timer",
    "combat": "sword",
    "daily": "calendar-check",
    "diagnosis": "activity",
    "echo-tuning": "sliders-horizontal",
    "echo-upgrade": "trending-up",
    "fast-travel": "map-pin",
    "forgery": "hammer",
    "garden": "sprout",
    "login": "log-in",
    "map": "map",
    "multi-account": "users",
    "nightmare": "moon",
    "pickup": "sparkles",
    "settings": "settings",
    "simulation": "repeat",
    "skip-dialog": "message-square",
    "synthesis": "layers",
    "tacet": "target",
    "tacet-elimination": "crosshair",
}

# 深浅主题都能看清的 zinc-500
STROKE_COLOR = "#71717A"
STROKE_WIDTH = 2


def fetch(slug: str) -> str:
    url = f"{BASE_URL}/{slug}.svg"
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read().decode("utf-8")


def restyle(svg: str) -> str:
    svg = svg.replace('stroke="currentColor"', f'stroke="{STROKE_COLOR}"')
    svg = svg.replace("currentColor", STROKE_COLOR)
    # lucide 默认 width/height=24 且没有 stroke-width，这里统一成 24/2
    if "stroke-width" not in svg:
        svg = svg.replace("<svg", f'<svg stroke-width="{STROKE_WIDTH}"', 1)
    else:
        svg = svg.replace('stroke-width="2"', f'stroke-width="{STROKE_WIDTH}"')
    if 'fill="none"' not in svg:
        svg = svg.replace("<svg", '<svg fill="none"', 1)
    return svg


def main() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, []
    for name, slug in MAPPING.items():
        png = ICON_DIR / f"{name}.png"
        for suffix in (".png", ".svg"):
            source = ICON_DIR / f"{name}{suffix}"
            backup = BACKUP_DIR / f"{name}{suffix}"
            if source.is_file() and not backup.is_file():
                shutil.copy2(source, backup)
        try:
            svg = restyle(fetch(slug))
        except Exception as error:  # noqa: BLE001
            failed.append((name, slug, repr(error)))
            continue
        (ICON_DIR / f"{name}.svg").write_text(svg, encoding="utf-8")
        ok += 1
        print(f"ok   {name:20s} <- {slug}")
    print(f"\n{ok}/{len(MAPPING)} 个图标已替换，备份在 {BACKUP_DIR}")
    for name, slug, error in failed:
        print(f"FAIL {name} ({slug}): {error}")


if __name__ == "__main__":
    main()
