"""S4 图片渲染 — 无头浏览器（Edge）高保真截图 Setlist/列表 → PNG.

施工图：docs/S1-S7-taskplan.md §S4（首选 playwright + 兜底 Edge CLI + Pillow 裁边）；
S10 泛化：docs/S10-list-image-atbot-plan.md §2.1（共享管线 render_html_pages + render_list）。

方案要点
--------
- **首选**：vendor 化 playwright（见 scripts/fetch_s4_vendor_deps.py），
  ``chromium.launch(channel="msedge")`` 驱动系统 Edge（免下载 Chromium）。
  元素级截图（精确裁剪）；setlist 先整表量高，超阈值（``MAX_PAGE_HEIGHT``）按行比例分页，
  每页 HTML 自带标题/日期/出演者头部与表头；列表（render_list）用估算行高分页。
- **共享管线**（S10）：``render_html_pages(pages_html, out_dir, ...)`` 接受**已预分页的
  HTML 字符串列表**逐页截图；``render_setlist`` / ``render_list`` 只负责「分页 → 组装 HTML」，
  截图统一走该管线（playwright 首选，失败回退 Edge CLI + Pillow 裁白边）。
- **兜底**（playwright 不可用 / 启动失败 / 无 Edge）：Edge headless CLI 整页截图 +
  Pillow ``getbbox()`` 裁白边；分页用估算行高（``ROW_HEIGHT_EST``）。
- **徽章色**：硬编码 ``BRAND_COLORS``（提取自 imas-db.jp 官方 ``bg-imas-brand-*``
  对应的 ``--imas-color-brand-*`` CSS 变量，2026-08-27 抓取 imas.min.css）。
- **契约**：只消费 ``models_song.Setlist``（S1 冻结）与 ``(主文本, 副文本)`` 行（S10），
  不改模型、不联网。
- **依赖兜底**：playwright / PIL import 失败回退 ``vendor/``（照抄 S1/S2 顶部写法）。

运行自测（离线，用 fixture 详情页）::

    python -m songbot.s4_render --from-fixture fixtures/imas_db_iwsf_day1.html
"""

from __future__ import annotations

import atexit
import hashlib
import html as html_mod
import json
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
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

# S10：列表类（候选/子列表/时间筛选/歌曲出现/bindings）自包含 HTML 样式。
# 版式沿用 setlist 模板（同字体/标题/宽度/白底），行样式为「序号 + 主文本 + 副文本（弱化色）」。
_LIST_CSS = """\
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #fff; }
body { font-family: "Yu Gothic UI","Yu Gothic","Meiryo","Segoe UI",sans-serif; color: #212529; }
#render-root { padding: 18px 22px; width: 880px; }
h1 { font-size: 1.55rem; font-weight: 700; line-height: 1.35; margin: 0 0 .4rem;
     padding-bottom: .3rem; border-bottom: 2px solid #ddd; color: #666; text-shadow: #ddd 1px 1px 1px; }
.list-row { display: flex; gap: .7rem; padding: .3rem .2rem; border-bottom: 1px solid #eee;
            align-items: baseline; }
.list-row .idx { flex: none; width: 2.8rem; text-align: right; color: #666;
                 font-variant-numeric: tabular-nums; }
.list-row .main { flex: 1 1 auto; font-weight: 600; }
.list-row .sub { flex: none; color: #868e96; font-size: 88.8%; white-space: nowrap; }
.list-footer { color: #868e96; font-size: 88.8%; margin-top: .6rem; }
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
# 渲染引擎（S10 共享管线：render_html_pages 接受已预分页的 HTML 字符串列表）
# ---------------------------------------------------------------------------
def _wait_fonts(page) -> None:
    """等待网页字体就绪（日文无缺字的前提：等字体加载完再截图）。"""
    try:
        page.wait_for_function("document.fonts && document.fonts.ready.then(()=>true)")
    except Exception:  # noqa: BLE001  （极端情况等不到字体也不阻断）
        page.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# 渲染工作线程（2026-08-27）：playwright 同步 API **非线程安全**（跨线程会报
# "cannot switch to a different thread"，且每次冷启动 Edge ~7s）。故起一个常驻
# worker 线程**独占**浏览器，所有「量高/截图」经队列串行执行：复用浏览器（首张
# ~7s、后续 ~1s）又不跨线程。playwright 不可用时 worker 不启动，量高返回 None、
# 渲染抛错回退 Edge CLI。
# ---------------------------------------------------------------------------
_render_queue = queue.Queue()
_render_worker: Optional[threading.Thread] = None
_render_worker_lock = threading.Lock()


def _measure_in_browser(browser, html: str) -> int:
    page = browser.new_page(device_scale_factor=DEVICE_SCALE)
    try:
        page.set_content(html, wait_until="load")
        _wait_fonts(page)
        return int(page.evaluate(
            "document.querySelector('#render-root').getBoundingClientRect().height"))
    finally:
        page.close()


def _render_in_browser(browser, pages_html: list[str], out_dir: Path, slug: str) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    page = browser.new_page(device_scale_factor=DEVICE_SCALE)
    try:
        for i, html in enumerate(pages_html, 1):
            page.set_content(html, wait_until="load")
            _wait_fonts(page)
            png = out_dir / f"{slug}_{i:02d}.png"
            page.locator("#render-root").screenshot(path=str(png))
            paths.append(png)
    finally:
        page.close()
    return paths


def _render_worker_loop() -> None:
    """渲染 worker 线程体：独占 playwright 浏览器，串行处理量高/截图请求。"""
    pw = None
    browser = None
    if _PLAYWRIGHT_OK:
        try:
            pw = sync_playwright().start()
            # M9 服务器 2GB 内存优化：单进程/限渲染进程/限 JS 堆，降渲染峰值内存（自建 HTML 简单，单进程够用）
            browser = pw.chromium.launch(
                channel=EDGE_CHANNEL, headless=True,
                args=[
                    '--disable-gpu',
                    '--disable-dev-shm-usage',
                    '--single-process',
                    '--renderer-process-limit=1',
                    '--js-flags=--max-old-space-size=256',
                    '--no-sandbox',
                    '--disable-extensions',
                    '--disable-background-networking',
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("渲染 worker 启动浏览器失败（渲染将回退 Edge CLI）: %s: %s",
                           type(exc).__name__, exc)
            browser = None
    while True:
        job = _render_queue.get()
        if job is None:              # 停止哨兵
            break
        kind, payload, result_queue = job
        try:
            if browser is None:
                raise RuntimeError("playwright 浏览器不可用（渲染 worker 启动失败）")
            if kind == "measure":
                result_queue.put(("ok", _measure_in_browser(browser, payload)))
            elif kind == "render":
                result_queue.put(("ok", _render_in_browser(browser, *payload)))
            else:  # pragma: no cover
                result_queue.put(("err", RuntimeError(f"未知渲染任务类型 {kind!r}")))
        except Exception as exc:  # noqa: BLE001 — 单次失败返回错误，不中断 worker
            result_queue.put(("err", exc))
    if browser is not None:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass
    if pw is not None:
        try:
            pw.stop()
        except Exception:  # noqa: BLE001
            pass


def _ensure_render_worker() -> None:
    """惰性启动渲染 worker 线程（进程内单例，幂等）。"""
    global _render_worker
    with _render_worker_lock:
        if _render_worker is None or not _render_worker.is_alive():
            _render_worker = threading.Thread(target=_render_worker_loop,
                                              name="render-worker", daemon=True)
            _render_worker.start()


def _submit_render(kind: str, payload, timeout: float = 120.0):
    """向渲染 worker 提交量高/截图任务并同步等待结果；失败抛异常。"""
    _ensure_render_worker()
    result_queue = queue.Queue()
    _render_queue.put((kind, payload, result_queue))
    status, result = result_queue.get(timeout=timeout)
    if status == "err":
        raise result
    return result


def _stop_render_worker() -> None:
    """停止渲染 worker（进程退出清理；幂等）。"""
    global _render_worker
    with _render_worker_lock:
        if _render_worker is not None and _render_worker.is_alive():
            _render_queue.put(None)     # 停止哨兵
            _render_worker.join(timeout=5.0)
        _render_worker = None


atexit.register(_stop_render_worker)


def warmup_browser() -> None:
    """预热渲染 worker：后台启动浏览器，首次渲染免冷启动 ~7s（浏览器在 worker 线程内创建，线程安全）。"""
    if not _PLAYWRIGHT_OK:
        return
    _ensure_render_worker()


def _measure_html_height(html: str) -> Optional[int]:
    """playwright 测量 ``#render-root`` 渲染高度（CSS px）；失败/不可用返回 None。

    经渲染 worker 线程执行（复用浏览器）；playwright 不可用或启动失败时
    由调用方回退估算行高（等价旧版 ``_render_cli_pages`` 的行为）。
    """
    if not _PLAYWRIGHT_OK:
        return None
    try:
        return _submit_render("measure", html)
    except Exception as exc:  # noqa: BLE001  — 量高失败回退估算（渲染主流程仍可走）
        logger.warning("playwright 量高失败（回退估算行高分页）: %s: %s",
                       type(exc).__name__, exc)
        return None


def _render_pages_playwright(pages_html: list[str], out_dir: Path, *, slug: str) -> list[Path]:
    """playwright → Edge channel：逐页元素级截图（经渲染 worker 线程复用浏览器）。"""
    return _submit_render("render", (pages_html, out_dir, slug))


def _render_pages_cli(
    pages_html: list[str],
    out_dir: Path,
    *,
    slug: str,
    est_heights: Optional[list[int]] = None,
) -> list[Path]:
    """兜底：Edge headless CLI 整页截图 + Pillow 裁白边。

    :param est_heights: 每页估算高（CSS px，窗口高度用）；缺省用宽松默认
        （``MAX_PAGE_HEIGHT + 800``），靠裁白边收边。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    edge = _edge_path()
    if edge is None:
        raise RuntimeError("Edge 可执行文件未找到，无法渲染（请安装 Edge 或改用 playwright 通道）")
    paths: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="s4_render_") as td:
        for i, html in enumerate(pages_html, 1):
            html_file = os.path.join(td, f"page{i}.html")
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(html)
            png = out_dir / f"{slug}_{i:02d}.png"
            if est_heights and i - 1 < len(est_heights):
                height = est_heights[i - 1]
            else:
                height = MAX_PAGE_HEIGHT + 800          # 宽松默认（无估算时用）
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


def render_html_pages(
    pages_html: list[str],
    out_dir: Path,
    *,
    slug: str = "page",
    est_heights: Optional[list[int]] = None,
) -> list[Path]:
    """**S10 共享渲染管线**：已预分页的 HTML 字符串列表 → PNG（playwright 首选 / CLI 兜底）。

    截图核心（逐页截图 + 裁白边）唯一实现处；``render_setlist`` / ``render_list``
    只负责「分页 → 组装每页 HTML」，再调用本函数出图。

    :param pages_html: 每页**自包含** HTML（须含 ``id="render-root"`` 容器，元素级截图目标）
    :param out_dir: 输出目录（自动创建）
    :param slug: PNG 文件名前缀（``{slug}_{01..NN}.png``）
    :param est_heights: 每页估算高（CSS px，仅 Edge CLI 兜底的窗口高度用）；
        None 时 CLI 兜底用宽松默认高度
    :return: 生成的 PNG 路径列表（升序，每张对应一页）
    :raises RuntimeError: 所有渲染引擎都不可用时（无 Edge、playwright 失败且 CLI 不可用）
    """
    if not pages_html:
        return []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if _PLAYWRIGHT_OK:
        try:
            return _render_pages_playwright(pages_html, out_dir, slug=slug)
        except Exception as exc:  # noqa: BLE001  回退兜底引擎
            logger.warning("playwright 渲染失败，回退 Edge CLI: %s: %s", type(exc).__name__, exc)
    return _render_pages_cli(pages_html, out_dir, slug=slug, est_heights=est_heights)


# ---------------------------------------------------------------------------
# 分页 → HTML（render_setlist / render_list 各自组装，统一交 render_html_pages）
# ---------------------------------------------------------------------------
def _estimate_height(n_rows: int) -> int:
    """估算行列表页高（CLI 兜底分页 + 窗口高度用；列表与 setlist 共用）。"""
    return HEADER_HEIGHT_EST + n_rows * ROW_HEIGHT_EST


def _chunk_rows(
    rows: list,
    measured_height: int,
    *,
    max_height: int = MAX_PAGE_HEIGHT,
) -> list[list]:
    """通用行分页（列表类用；与 ``_chunk_tracks`` 同逻辑，仅语义改名）。"""
    return _chunk_tracks(rows, measured_height, max_height=max_height)


def _build_setlist_pages(setlist: Setlist) -> tuple[list[str], list[int]]:
    """Setlist → (每页 HTML, 每页估算高)：量高（或估算）→ ``_chunk_tracks`` → ``build_html``。"""
    measured = _measure_html_height(build_html(setlist))
    if measured is None:
        measured = _estimate_height(len(setlist.tracks))
    chunks = _chunk_tracks(setlist.tracks, measured)
    pages = [build_html(setlist, tracks=chunk) for chunk in chunks]
    heights = [_estimate_height(len(chunk)) for chunk in chunks]
    return pages, heights


def _build_list_pages(
    title: str,
    rows: list[tuple[str, str]],
    *,
    hint: str,
    measured_height: Optional[int] = None,
) -> tuple[list[str], list[int]]:
    """列表行 → (每页 HTML, 每页估算高)：量高（或估算）→ ``_chunk_rows`` → ``build_list_html``。

    :param measured_height: 整表测量高（CSS px）；None 时优先 playwright 量高
        （与 ``_build_setlist_pages`` 一致）。**不要只用估算行高**：列表主文本是长事件名，
        flex 容器内会换行成 2–3 行，实际行高约为 ``ROW_HEIGHT_EST`` 的 1.4x——估算分页
        会严重低估页高，产出超高 PNG（2x 缩放后上万 px、数 MB），截图与 base64 上传都慢
        （2026-08-27 S10 live 反馈「列表图片特别慢」修复）；量高失败（playwright 不可用）才回退估算。
    """
    if measured_height is None:
        measured_height = _measure_html_height(build_list_html(title, rows, hint=hint))
    if measured_height is None:
        measured_height = _estimate_height(len(rows))
    chunks = _chunk_rows(rows, measured_height)
    pages = [build_list_html(title, chunk, hint=hint) for chunk in chunks]
    heights = [_estimate_height(len(chunk)) for chunk in chunks]
    return pages, heights


# ---------------------------------------------------------------------------
# 入口（契约签名：render_setlist(setlist, *, out_dir=None) -> list[Path]；
#            render_list(title, rows, *, out_dir=None, hint="回复序号") -> list[Path]）
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
    pages, heights = _build_setlist_pages(setlist)
    slug = _title_slug(setlist.title)
    return render_html_pages(pages, out_dir, slug=slug, est_heights=heights)


def _content_hash(texts: list[str], length: int = 6) -> str:
    """内容短哈希（render_list 文件名防同标题覆盖：标题相同但行不同 → 不同文件名）。"""
    return hashlib.md5("".join(texts).encode("utf-8")).hexdigest()[:length]


def build_list_html(
    title: str, rows: list[tuple[str, str]], *, hint: str = "回复序号"
) -> str:
    """组装列表类自包含 HTML（S10）：标题 + 序号行「主文本 + 副文本」+ footer 提示。

    :param title: 列表标题（如「2026年7月 的 LIVE（共 2 场）」）
    :param rows: [(主文本, 副文本)]，序号自动 1 起；副文本可为 ""（日期/品牌等弱化色）
    :param hint: footer 提示文本（默认「回复序号」，S10 拍板统一）
    """
    body = []
    for i, (main, sub) in enumerate(rows, 1):
        sub_html = f'<span class="sub">{_esc(sub)}</span>' if sub else ""
        body.append(
            f'<div class="list-row"><span class="idx">{i}.</span>'
            f'<span class="main">{_esc(main)}</span>{sub_html}</div>'
        )
    return (
        "<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
        f"<style>{_LIST_CSS}</style></head><body><div id=\"render-root\">"
        f"<h1>{_esc(title)}</h1>{''.join(body)}"
        f'<div class="list-footer">{_esc(hint)}</div></div></body></html>'
    )


def render_list(
    title: str,
    rows: list[tuple[str, str]],
    *,
    out_dir: Optional[Union[str, Path]] = None,
    hint: str = "回复序号",
    slug: Optional[str] = None,
) -> list[Path]:
    """渲染列表类回复 → 一张或多张 PNG（长列表分页），返回 PNG 路径列表（S10）。

    :param title: 列表标题（渲染进每页图内）
    :param rows: [(主文本, 副文本)]，序号自动 1 起；空 rows 返回 []
    :param out_dir: 输出目录；None 时用 ``data/songbot_img/<YYYYMMDD_HHMMSS>/``
    :param hint: 图内 footer 提示（默认「回复序号」）
    :param slug: PNG 文件名前缀；None 用「标题 slug + 内容短哈希」（同标题不同行不互相覆盖）
    :return: 生成的 PNG 路径列表（升序，每张对应一页）；rows 为空返回 []
    :raises RuntimeError: 所有渲染引擎都不可用时（同 render_setlist）
    """
    if not rows:
        return []
    if out_dir is None:
        out_dir = Path("data") / "songbot_img" / datetime.now().strftime("%Y%m%d_%H%M%S")
    pages, heights = _build_list_pages(title, rows, hint=hint)
    if slug is None:
        slug = _title_slug(title) + "_" + _content_hash(pages)
    return render_html_pages(pages, out_dir, slug=slug, est_heights=heights)


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
