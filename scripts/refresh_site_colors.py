"""刷新 data/songbot_site_colors.json：从 imas-db.jp 抓取 CSS，提取品牌色与版式色。

用法：
    python scripts/refresh_site_colors.py [--page http://imas-db.jp/song/event/million_13th_day1.html]

输出 data/songbot_site_colors.json：
    {"fetched_at": "...", "source": [...], "brand_keys": {key: color}, "style": {...}}

s4_render 渲染时读取该 JSON（缺失/损坏自动回退内置常量），让徽章/标题等颜色跟随原网页。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from songbot.s4_render import SITE_COLORS_FILE, extract_site_colors  # noqa: E402

BASE = "http://imas-db.jp"
STYLE_RULES = {
    "page_title": (r"h1#page_title\s*\{([^}]*)\}", r"color:\s*(#[0-9a-fA-F]{3,8})\b"),
    "page_title_shadow": (r"h1#page_title\s*\{([^}]*)\}", r"text-shadow:\s*(#[0-9a-fA-F]{3,8})\b"),
    "part_header_bg": (r"tr\.part-header\s*\{([^}]*)\}", r"background-color:\s*(#[0-9a-fA-F]{3,8})\b"),
    "part_header_color": (r"tr\.part-header\s*\{([^}]*)\}", r"color:\s*(#[0-9a-fA-F]{3,8})\b"),
    "row_hover_bg": (r"tbody tr:hover\s*\{([^}]*)\}", r"background-color:\s*(#[0-9a-fA-F]{3,8})\b"),
    "caption_color": (r"\.caption\s*\{([^}]*)\}", r"color:\s*(#[0-9a-fA-F]{3,8})\b"),
}

# attr 颜色变量名（--imas-<brand>-attr-color-<name>）→ (data 属性前缀, 属性值编号)
# 注意：million 的 vocal/dance/visual 走 data-million-gree-attr（与 princess/fairy/angel 不同）
ATTR_NAME_TO_ATTR = {
    "cute": ("cinderella-attr", "1"), "cool": ("cinderella-attr", "2"), "passion": ("cinderella-attr", "3"),
    "princess": ("million-attr", "1"), "fairy": ("million-attr", "2"), "angel": ("million-attr", "3"),
    "vocal": ("million-gree-attr", "1"), "dance": ("million-gree-attr", "2"), "visual": ("million-gree-attr", "3"),
    "physical": ("sidem-attr", "1"), "intelli": ("sidem-attr", "2"), "mental": ("sidem-attr", "3"),
}


def _extract_rule_colors(css: str, attr: str) -> dict:
    """块级解析 ``.idol-name[data-<attr>="N"]`` 规则的 border-color。

    处理逗号分隔的共享块（多个选择器共用一个声明块，如
    ``.idol-name[data-character-id="1"],.idol-name[data-character-id="2"]{border-color:#xxx}``），
    每个匹配的 id 都记录；按 CSS 出现顺序遍历，后定义覆盖先定义（与浏览器一致）。
    """
    out: dict[str, str] = {}
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        sel, body = m.group(1), m.group(2)
        if attr not in sel or "idol-name" not in sel:
            continue
        bc = re.search(r"border-color:\s*([^;}]+)", body)
        if not bc:
            continue
        color = bc.group(1).strip()
        for cid in re.findall(rf'data-{attr}="(\d+)"', sel):
            out[cid] = color
    return out


def extract_brand_id_map(css: str) -> dict:
    """.idol-name[data-brand-id="N"] 规则 → {"N": "key"}（border-color:var(--imas-color-brand-<key>)）"""
    out: dict[str, str] = {}
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        sel, body = m.group(1), m.group(2)
        if "data-brand-id" not in sel or "idol-name" not in sel:
            continue
        vm = re.search(r"border-color:\s*var\(--imas-color-brand-([\w-]+)\)", body)
        if not vm:
            continue
        for bid in re.findall(r'data-brand-id="(\d+)"', sel):
            out[bid] = vm.group(1)
    return out


def extract_attr_colors(css: str) -> dict:
    """--imas-<brand>-attr-color-<name>:色 → {"<attr前缀>-<N>": "色"}（如 cinderella-attr-1 → #ef2782）"""
    out: dict[str, str] = {}
    for m in re.finditer(r"--imas-([\w-]+)-attr-color-([\w]+)\s*:\s*([^;}]+);", css):
        name, color = m.group(2), m.group(3).strip()
        mapped = ATTR_NAME_TO_ATTR.get(name)
        if mapped:
            out[f"{mapped[0]}-{mapped[1]}"] = color
    return out


def extract_group_colors(css: str) -> dict:
    """.idol-name[data-group-id="N"] 规则 → {"N": "色"}（组合色，块级解析）"""
    return _extract_rule_colors(css, "group-id")


def extract_character_colors(css: str) -> dict:
    """.idol-name[data-character-id="N"] 规则 → {"N": "色"}（角色个人应援色，400+ 条，块级解析）"""
    return _extract_rule_colors(css, "character-id")


def extract_idol_class_colors(css: str) -> dict:
    """.idol_<class>{color:…} 规则 → {"idol_sc_sakuya": "#006047"}（早期版式文字色方案）.

    S11（2026-08-27）：2022 及更早公演用 ``<span class="idol_*">`` 演者标记，
    颜色定义在 ``.idol_*{color:…!important}``（如 ``idol_sc_unit02`` / ``idol_ml_mirai``）。
    块级解析、后定义覆盖先定义（与 ``_extract_rule_colors`` 一致，逗号分隔共享块
    的每个类都记录）；``color:`` 捕获组排除 ``!important``（早期规则普遍带 !important）。
    实测 imas.min.css + maruamyu.min.css 共 174 条（覆盖 sc 37 / ml 76 / gk 15 / 765AS / …）。
    """
    out: dict[str, str] = {}
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        sel, body = m.group(1), m.group(2)
        cm = re.search(r"color:\s*([^;!}]+)", body)
        if not cm:
            continue
        color = cm.group(1).strip()
        for cls in re.findall(r"\.(idol_[a-z0-9_]+)\b", sel):
            out[cls] = color
    return out


def extract_style_colors(maruamyu_css: str) -> dict:
    """从 maruamyu.min.css 提取版式色（找不到的键留空，由渲染侧回退内置值）。"""
    out: dict[str, str] = {}
    for key, (rule_re, color_re) in STYLE_RULES.items():
        m = re.search(rule_re, maruamyu_css)
        if not m:
            continue
        cm = re.search(color_re, m.group(1))
        if cm:
            out[key] = cm.group(1)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="刷新 songbot 站点颜色缓存 JSON")
    parser.add_argument("--page", default=BASE + "/song/event/million_13th_day1.html")
    args = parser.parse_args(argv)

    client = httpx.Client(follow_redirects=True, timeout=30)
    html = client.get(args.page).content.decode("utf-8")
    css_hrefs = re.findall(r'<link[^>]+href="([^"]+\.css[^"]*)"', html)
    if not css_hrefs:
        print("[ERROR] 页面未发现 CSS 链接", file=sys.stderr)
        return 1

    sources, imas_css, maruamyu_css = [], "", ""
    for href in css_hrefs:
        url = href if href.startswith("http") else BASE + ("" if href.startswith("/") else "/song/event/") + href
        text = client.get(url).content.decode("utf-8", errors="replace")
        sources.append(url)
        if "imas.min.css" in url:
            imas_css = text
        if "maruamyu.min.css" in url:
            maruamyu_css = text

    brand_keys = extract_site_colors(imas_css) if imas_css else {}
    style = extract_style_colors(maruamyu_css) if maruamyu_css else {}
    if not brand_keys:
        print("[ERROR] 未提取到品牌色（imas.min.css 缺失？）", file=sys.stderr)
        return 1

    css = imas_css + "\n" + maruamyu_css
    data = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source": sources,
        "brand_keys": brand_keys,
        "style": style,
        "brand_id_map": extract_brand_id_map(css),      # idol-name data-brand-id → key
        "attr_colors": extract_attr_colors(imas_css),   # 属性色（cinderella/million/sidem）
        "group_colors": extract_group_colors(css),      # 组合色（data-group-id）
        "character_colors": extract_character_colors(css),  # 角色个人应援色（data-character-id，优先级最高）
        "idol_class_colors": extract_idol_class_colors(css),  # 早期版式 .idol_*{color}（S11）
    }
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path = os.path.join(root, SITE_COLORS_FILE)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(f"[OK] {len(brand_keys)} 品牌色 / {len(data['brand_id_map'])} brand-id / "
          f"{len(data['attr_colors'])} 属性色 / {len(data['group_colors'])} 组合色 / "
          f"{len(data['character_colors'])} 角色个人色 / {len(data['idol_class_colors'])} 早期类色 / "
          f"{len(style)} 版式色 -> {out_path}")
    print("  brand_id_map:", json.dumps(data["brand_id_map"], ensure_ascii=False))
    print("  attr_colors:", json.dumps(data["attr_colors"], ensure_ascii=False))
    print("  group_colors:", json.dumps(data["group_colors"], ensure_ascii=False))
    print("  character_colors 样本:", json.dumps(
        dict(list(data["character_colors"].items())[:6]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
