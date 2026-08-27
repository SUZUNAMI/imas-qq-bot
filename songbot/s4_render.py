"""S4 图片渲染 — 无头浏览器（Edge）高保真截图 Setlist → PNG.

施工图：docs/S1-S7-taskplan.md §S4（首选 playwright + 兜底 Edge CLI + Pillow 裁边）。

方案要点
--------
- **首选**：vendor 化 playwright（见 scripts/fetch_s4_vendor_deps.py），
  ``chromium.launch(channel="msedge")`` 驱动系统 Edge（免下载 Chromium）。
  元素级截图（精确裁剪）；先整表量高，超阈值（``MAX_PAGE_HEIGHT``）按行比例分页，
  每页 HTML 自带标题/日期/出演者头部与表头。
- **兜底**（playwright 不可用 / 启动失败 / 无 Edge）：Edge headless CLI 整页截图 +
  Pillow ``getbbox()`` 裁白边；分页用估算行高（``ROW_HEIGHT_EST``）。
- **徽章色**：硬编码 ``BRAND_COLORS``（提取自 imas-db.jp 官方 ``bg-imas-brand-*``
  对应的 ``--imas-color-brand-*`` CSS 变量，2026-08-27 抓取 imas.min.css）。
- **契约**：只消费 ``models_song.Setlist``（S1 冻结），不改模型、不联网。
- **依赖兜底**：playwright / PIL import 失败回退 ``vendor/``（照抄 S1/S2 顶部写法）。

运行自测（离线，用 fixture 详情页）::

    python -m songbot.s4_render --from-fixture fixtures/imas_db_iwsf_day1.html
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from songbot.models_song import Setlist, Track

# ---------------------------------------------------------------------------
# 依赖兜底（vendor 化：沙箱无法 pip 安装，正常环境走系统 site-packages）
# ---------------------------------------------------------------------------
try:
    from PIL import Image
except ImportError:  # pragma: no cover
    _vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
    if os.path.isdir(_vendor) and _vendor not in sys.path:
        sys.path.insert(0, _vendor)
        from PIL import Image
    else:
        Image = None  # 无 Pillow：跳过裁边/压缩（渲染本身仍可用）

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_OK = True
except Exception:  # pragma: no cover  （ImportError 或驱动缺件都算不可用）
    _vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
    try:
        if os.path.isdir(_vendor) and _vendor not in sys.path:
            sys.path.insert(0, _vendor)
        from playwright.sync_api import sync_playwright
        _PLAYWRIGHT_OK = True
    except Exception:  # noqa: BLE001
        _PLAYWRIGHT_OK = False

logger = logging.getLogger("songbot.s4_render")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
EDGE_EXECUTABLE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
EDGE_CHANNEL = "msedge"        # playwright 免下载 Chromium，驱动系统 Edge

MAX_PAGE_HEIGHT = 3000         # 单张图片高度阈值（CSS px）
ROW_HEIGHT_EST = 36            # Edge CLI 兜底分页的估算行高（px）
HEADER_HEIGHT_EST = 150        # 标题/日期/出演/表头估算高（px）
DEVICE_SCALE = 2               # 输出 2x 清晰度
CLI_WIDTH = 900                # Edge CLI 兜底窗口宽
VIRTUAL_TIME_BUDGET = 8000     # Edge CLI 渲染等待（ms）

DEFAULT_BRAND_COLOR = "#747488"   # --imas-color-brand-general

# ---------------------------------------------------------------------------
# 原网页颜色数据（2026-08-27 抓取 imas.min.css / maruamyu.min.css）
# ---------------------------------------------------------------------------
# badge 显示名（title 或文本）→ CSS 类 key（bg-imas-brand-* / bg-imas-mr-project）
BRAND_KEY_MAP = {
    "THE IDOLM@STERシリーズ": "idolmaster",
    "765PRO ALLSTARS": "765as",
    "シンデレラガールズ": "cinderella",
    "ミリオンライブ！": "million",
    "SideM": "sidem",
    "シャイニーカラーズ": "shinycolors",
    "学園アイドルマスター": "gakuen",
    "vα-liv": "valiv",
    "961プロダクション": "961pro",
    "Dearly Stars": "dearlystars",
    "876プロダクション": "876pro",
    "XENOGLOSSIA": "xenoglossia",
    '"MORE RE@LITY"形式のイベント': "mr-project",
    # badge 无 title 时的文本兜底
    "IM@S": "idolmaster",
    "765AS": "765as",
    "シンデレラ": "cinderella",
    "ミリオン": "million",
    "シャニ": "shinycolors",
    "学園": "gakuen",
    "961プロ": "961pro",
    "MORE RE@LITY": "mr-project",
}

# CSS key → 颜色（--imas-color-brand-* 变量，var() 引用已解析为最终值）。
# 运行 scripts/refresh_site_colors.py 可抓取最新 CSS 生成 data/songbot_site_colors.json 覆盖。
DEFAULT_BRAND_KEYS = {
    "general": "#747488",
    "idolmaster": "#ff74b8",
    "765as": "#f34f6d",
    "cinderella": "#2681c8",
    "million": "#ffc30b",
    "sidem": "#0fbe94",
    "shinycolors": "#8dbbff",
    "gakuen": "#f39800",
    "xenoglossia": "#747488",
    "dearlystars": "#ffa500",
    "876pro": "#ffa500",
    "kr": "#747488",
    "valiv": "#656a75",
    "961pro": "#747488",
    "mr-project": "#929cb1",
    "others": "#747488",
}

# 网页版式色（maruamyu.min.css / imas.min.css，2026-08-27 提取）
STYLE_COLORS = {
    "page_title": "#666",          # h1#page_title 文字色
    "page_title_shadow": "#ddd",   # h1#page_title text-shadow
    "part_header_bg": "#f7f7f7",   # tr.part-header 背景（幕标题行）
    "part_header_color": "#666",   # tr.part-header 文字
    "row_hover_bg": "#efefef",     # tbody tr:hover 背景
    "caption_color": "#666",       # .caption 文字
}

SITE_COLORS_FILE = "data/songbot_site_colors.json"   # 相对仓库根

_site_brand_keys: dict = {}
_site_style: dict = {}


def extract_site_colors(imas_css: str) -> dict:
    """从 imas.min.css 文本提取品牌色（CSS key → 颜色，var() 引用已解析）。纯函数。"""
    raw: dict[str, str] = {}
    for m in re.finditer(r"--imas-color-brand-([\w-]+)\s*:\s*([^;}]+);", imas_css):
        raw[m.group(1)] = m.group(2).strip()
    out: dict[str, str] = {}
    for key, value in raw.items():
        resolved = value
        for _ in range(5):  # var(--x) 递归解析（xenoglossia→general 等）
            m = re.match(r"var\(\s*--imas-color-brand-([\w-]+)\s*\)", resolved)
            if not m:
                break
            nxt = raw.get(m.group(1))
            if nxt is None:
                break
            resolved = nxt.strip()
        out[key] = resolved
    return out


def load_site_colors(path: Optional[str] = None) -> dict:
    """读取 data/songbot_site_colors.json；缺失/损坏回退内置常量（不抛异常）。

    JSON 结构：{"fetched_at": ..., "brand_keys": {...}, "style": {...}}
    """
    global _site_brand_keys, _site_style
    if path is None:
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", SITE_COLORS_FILE
        )
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        _site_brand_keys = data.get("brand_keys") or {}
        _site_style = data.get("style") or {}
    except (OSError, ValueError):
        _site_brand_keys, _site_style = {}, {}
    return {"brand_keys": _site_brand_keys, "style": _site_style}
# 品牌（badge title 或文本）→ 徽章底色（bg-imas-brand-* / --imas-color-brand-*，2026-08-27 抓取）
BRAND_COLORS = {
    "THE IDOLM@STERシリーズ": "#ff74b8",   # idolmaster
    "765PRO ALLSTARS": "#f34f6d",          # 765as
    "シンデレラガールズ": "#2681c8",        # cinderella
    "ミリオンライブ！": "#ffc30b",          # million
    "SideM": "#0fbe94",                    # sidem
    "シャイニーカラーズ": "#8dbbff",         # shinycolors
    "学園アイドルマスター": "#f39800",       # gakuen
    "vα-liv": "#656a75",                   # valiv
    "961プロダクション": "#747488",          # 961pro -> general
    "Dearly Stars": "#ffa500",             # dearlystars（orange）
    "876プロダクション": "#ffa500",          # 876pro -> dearlystars
    "XENOGLOSSIA": "#747488",              # xenoglossia -> general
    # badge 无 title 时的文本兜底
    "IM@S": "#ff74b8",
    "765AS": "#f34f6d",
    "シンデレラ": "#2681c8",
    "ミリオン": "#ffc30b",
    "シャニ": "#8dbbff",
    "学園": "#f39800",
    "961プロ": "#747488",
}
# 浅色底徽章用深色文字（对齐站点 .badge.bg-imas-brand-million/.shinycolors 覆盖）
BRAND_DARK_TEXT = {"#ffc30b", "#8dbbff", "#ffa500"}

# 内联 CSS：复刻 maruamyu.min.css 的 table.tracklist 规则（2026-08-27 抓取），
# 外加标题/日期/出演头部与徽章样式；颜色对齐原网页（h1#page_title 灰字+阴影等，
# 见 STYLE_COLORS / DEFAULT_BRAND_KEYS）。
_CSS = """\
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #fff; }
body { font-family: "Yu Gothic UI","Yu Gothic","Meiryo","Segoe UI",sans-serif; color: #212529; }
#render-root { padding: 18px 22px; width: 880px; }
h1 { font-size: 1.55rem; font-weight: 700; line-height: 1.35; margin: 0 0 .4rem;
     padding-bottom: .3rem; border-bottom: 2px solid #ddd; color: #666; text-shadow: #ddd 1px 1px 1px; }
.meta { color: #495057; margin: .2rem 0 .35rem; }
.performers { color: #343a40; margin: 0 0 .8rem; }
table.tracklist { width: 100%; border-collapse: collapse; }
table.tracklist td, table.tracklist th { padding: .25rem .4rem; }
table.tracklist thead { border-bottom: 1px solid #aaa; font-weight: 700; }
table.tracklist tbody tr { border-bottom: 1px solid #ddd; vertical-align: top; }
table.tracklist tbody tr:hover { background-color: #efefef; }
table.tracklist tr.part-header { color: #666; text-shadow: 1px 1px #fff; background-color: #f7f7f7; }
table.tracklist .caption { color: #666; font-size: 88.8%; }
table.tracklist tr td:first-child, table.tracklist tr th:first-child {
    width: 3.5rem; text-align: right; }
table.tracklist tr td:first-child:after { content: "."; }
table.tracklist tr td:nth-child(2), table.tracklist tr th:nth-child(2) { width: 21rem; }
table.tracklist tr td:nth-child(2) { text-shadow: 1px 1px #ddd; }
table.tracklist tr td:nth-child(2) .badge { text-shadow: none; }
.idol-name { display: inline-block; line-height: 100%; text-shadow: 1px 1px #ddd;
             border-bottom: 2px solid #ddd; margin: 0 .12em; }   /* 应援色色带（原网页 .idol-name） */
.badge { display: inline-block; padding: .12em .5em; margin-left: .35em;
         font-size: .75rem; font-weight: 600; line-height: 1.5; color: #fff;
         border-radius: .375rem; vertical-align: middle; white-space: nowrap; }
"""


# ---------------------------------------------------------------------------
# 纯函数（离线可单测）
# ---------------------------------------------------------------------------
def _brand_color(brand: Optional[str]) -> str:
    """品牌（badge title/文本）→ 徽章底色。

    优先 site JSON 中 CSS key 的颜色（跟随原网页更新），其次内置 DEFAULT_BRAND_KEYS；
    未知品牌/未知 key 用中性灰（general）。"""
    key = BRAND_KEY_MAP.get(brand) if brand else None
    if key:
        color = _site_brand_keys.get(key)
        if color:
            return color
        return DEFAULT_BRAND_KEYS.get(key, DEFAULT_BRAND_COLOR)
    return DEFAULT_BRAND_COLOR


def _esc(value) -> str:
    return html_mod.escape(str(value), quote=True)


def _idol_spans(names: list[str], colors: Optional[list] = None) -> str:
    """演者名 → idol-name span 序列（应援色下划线，复刻原网页 .idol-name 的 border-bottom）。"""
    colors = colors or []
    parts = []
    for i, name in enumerate(names):
        color = colors[i] if i < len(colors) else None
        style = f' style="border-color:{color}"' if color else ""
        parts.append(f'<span class="idol-name"{style}>{_esc(name)}</span>')
    return "、".join(parts)


def _badge_html(brand: Optional[str]) -> str:
    if not brand:
        return ""
    color = _brand_color(brand)
    text_color = "#212529" if color in BRAND_DARK_TEXT else "#fff"
    return (
        f'<small class="badge" style="background:{color};color:{text_color}">'
        f"{_esc(brand)}</small>"
    )


def build_html(setlist: Setlist, *, tracks: Optional[list[Track]] = None) -> str:
    """组装自包含 HTML（内联 CSS 复刻 tracklist 版式）。

    :param setlist: 待渲染的 Setlist（标题/日期场馆/出演者/曲目）
    :param tracks: 指定渲染的曲目子集（分页时每页传一页）；None 时渲染全部
    """
    rows = setlist.tracks if tracks is None else tracks
    body = []
    for t in rows:
        title_cell = f"{_esc(t.title)}{_badge_html(t.brand)}"
        perf_cell = _idol_spans(t.performers, t.performer_colors) if t.performers else ""
        body.append(
            f"<tr><td>{t.no}</td><td>{title_cell}</td><td>{perf_cell}</td></tr>"
        )
    title = _esc(setlist.title) if setlist.title else "セットリスト"
    meta = f'<div class="meta">{_esc(setlist.date_venue)}</div>' if setlist.date_venue else ""
    performers_html = (
        f'<div class="performers">出演: {_idol_spans(setlist.performers, setlist.performer_colors)}</div>'
        if setlist.performers else ""
    )
    return (
        "<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
        f"<style>{_CSS}</style></head><body><div id=\"render-root\">"
        f"<h1>{title}</h1>{meta}{performers_html}"
        f'<table class="tracklist"><thead><tr><th>No.</th><th>楽曲</th><th>演者</th></tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div></body></html>"
    )


def _chunk_tracks(
    tracks: list[Track],
    measured_height: int,
    *,
    max_height: int = MAX_PAGE_HEIGHT,
) -> list[list[Track]]:
    """按整表测量高度与单页阈值拆分（行数比例法，每页自带表头）。

    :param tracks: 全部曲目
    :param measured_height: 整表（含头部/表头）渲染高度（CSS px）
    :param max_height: 单张图片高度阈值
    """
    if not tracks or measured_height <= max_height:
        return [tracks]
    rows_per_page = max(1, int(len(tracks) * max_height / measured_height))
    return [tracks[i : i + rows_per_page] for i in range(0, len(tracks), rows_per_page)]


def _crop_white(img, *, threshold: int = 248, pad: int = 8):
    """裁掉四周接近白色的边（保留 pad 像素留白）；全白图原样返回。"""
    gray = img.convert("L")
    mask = gray.point(lambda v: 255 if v < threshold else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return img
    left, top, right, bottom = bbox
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(img.width, right + pad)
    bottom = min(img.height, bottom + pad)
    return img.crop((left, top, right, bottom))


def _edge_path() -> Optional[str]:
    for p in EDGE_EXECUTABLE_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def _title_slug(title: str) -> str:
    """从标题生成文件名 slug（前 12 个字母/数字/日文字符），避免同目录多次渲染互相覆盖。"""
    slug = re.sub(r"[^\w\u3040-\u30ff\u4e00-\u9fff-]", "", title or "", flags=re.UNICODE)[:12]
    return slug or "setlist"


# ---------------------------------------------------------------------------
# 渲染引擎
# ---------------------------------------------------------------------------
def _wait_fonts(page) -> None:
    """等待网页字体就绪（日文无缺字的前提：等字体加载完再截图）。"""
    try:
        page.wait_for_function("document.fonts && document.fonts.ready.then(()=>true)")
    except Exception:  # noqa: BLE001  （极端情况等不到字体也不阻断）
        page.wait_for_timeout(300)


def _render_playwright_pages(setlist: Setlist, out_dir: Path) -> list[Path]:
    """playwright → Edge channel：整表量高 → 分页 → 逐页元素级截图。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=EDGE_CHANNEL, headless=True)
        try:
            page = browser.new_page(device_scale_factor=DEVICE_SCALE)
            page.set_content(build_html(setlist), wait_until="load")
            _wait_fonts(page)
            height = float(
                page.evaluate(
                    "document.querySelector('#render-root').getBoundingClientRect().height"
                )
            )
            chunks = _chunk_tracks(setlist.tracks, int(height))
            slug = _title_slug(setlist.title)
            for i, chunk in enumerate(chunks, 1):
                page.set_content(build_html(setlist, tracks=chunk), wait_until="load")
                _wait_fonts(page)
                png = out_dir / f"{slug}_{i:02d}.png"
                page.locator("#render-root").screenshot(path=str(png))
                paths.append(png)
        finally:
            browser.close()
    return paths


def _render_cli_pages(setlist: Setlist, out_dir: Path) -> list[Path]:
    """兜底：Edge headless CLI 整页截图 + Pillow 裁白边（估算行高分页）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    edge = _edge_path()
    if edge is None:
        raise RuntimeError("Edge 可执行文件未找到，无法渲染（请安装 Edge 或改用 playwright 通道）")
    n = len(setlist.tracks)
    est_height = HEADER_HEIGHT_EST + n * ROW_HEIGHT_EST
    chunks = _chunk_tracks(setlist.tracks, est_height)
    paths: list[Path] = []
    slug = _title_slug(setlist.title)
    with tempfile.TemporaryDirectory(prefix="s4_render_") as td:
        for i, chunk in enumerate(chunks, 1):
            html = build_html(setlist, tracks=chunk)
            html_file = os.path.join(td, f"page{i}.html")
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(html)
            png = out_dir / f"{slug}_{i:02d}.png"
            height = HEADER_HEIGHT_EST + len(chunk) * ROW_HEIGHT_EST
            cmd = [
                edge,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--disable-extensions",
                f"--user-data-dir={os.path.join(td, f'profile{i}')}",
                f"--screenshot={png}",
                f"--window-size={CLI_WIDTH},{height}",
                f"--virtual-time-budget={VIRTUAL_TIME_BUDGET}",
                Path(html_file).as_uri(),
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            if Image is not None:
                img = Image.open(png)
                img = _crop_white(img)
                img.save(png, optimize=True)
            paths.append(png)
    return paths


# ---------------------------------------------------------------------------
# 入口（契约签名：render_setlist(setlist, *, out_dir=None) -> list[Path]）
# ---------------------------------------------------------------------------
def render_setlist(
    setlist: Setlist, *, out_dir: Optional[Union[str, Path]] = None
) -> list[Path]:
    """渲染 Setlist → 一张或多张 PNG（长表分页，每张带表头），返回 PNG 路径列表。

    :param setlist: 待渲染的 Setlist（来自 S2 的 fetch_setlist / parse_setlist_html）
    :param out_dir: 输出目录；None 时用 ``data/songbot_img/<YYYYMMDD_HHMMSS>/``
        （相对当前工作目录，自动创建）
    :return: 生成的 PNG 路径列表（升序，每张对应一页）
    :raises RuntimeError: 所有渲染引擎都不可用时（无 Edge、playwright 失败且 CLI 不可用）
    """
    if out_dir is None:
        out_dir = Path("data") / "songbot_img" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if _PLAYWRIGHT_OK:
        try:
            return _render_playwright_pages(setlist, out_dir)
        except Exception as exc:  # noqa: BLE001  回退兜底引擎
            logger.warning("playwright 渲染失败，回退 Edge CLI: %s: %s", type(exc).__name__, exc)
    return _render_cli_pages(setlist, out_dir)


# ---------------------------------------------------------------------------
# 命令行自测：python -m songbot.s4_render --from-fixture <详情页 html>
# ---------------------------------------------------------------------------
def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="s4_render", description="Setlist → PNG（无头浏览器渲染）")
    parser.add_argument("--from-fixture", metavar="HTML", help="本地详情页 HTML（用 S2 解析后渲染）")
    parser.add_argument("--out-dir", metavar="DIR", default=None, help="输出目录（默认 data/songbot_img/<ts>/）")
    args = parser.parse_args(argv)

    if not args.from_fixture:
        parser.error("需要 --from-fixture <详情页 html>")
    try:
        from songbot.s2_fetch_setlist import parse_setlist_html

        with open(args.from_fixture, encoding="utf-8") as f:
            setlist = parse_setlist_html(f.read(), url="")
        paths = render_setlist(setlist, out_dir=args.out_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1

    print(f"标题: {setlist.title}")
    print(f"曲目数: {len(setlist.tracks)}  →  生成 {len(paths)} 张 PNG:")
    for p in paths:
        size = p.stat().st_size if p.exists() else 0
        print(f"  {p}  ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
