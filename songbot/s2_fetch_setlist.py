"""S2 详情抓取 + 解析 — 歌曲列表 bot（songbot）.

抓公演详情页（xxx.html），解析出结构化 ``Setlist``（标题 / 日期场馆 / 出演者 / セットリスト），
供 S4 渲染与 S6 主控消费。

数据源结论（2026-08-27 三份 fixture 实测，详见 docs/modules/S2-fetch-setlist-worklog.md）
----------------------------------------------------------------------------------------
站点详情页存在**多种历史版式**，选择器必须泛化：

- A) IWSF 型（fixtures/imas_db_iwsf_day1.html）：日期场馆在 ``<div class="m-2">``
  （含 ``開場/開演`` 与 ``<a>詳細</a>``）；出演在 ``<div class="m-2">出演アイドル:``。
- B) 13thLIVE 型（fixtures/imas_db_million_13th_day1.html）：日期场馆在 ``<p>``
  （含 ``開場/開演`` 与 ``<a>詳細</a>``）；出演在 ``<div class="my-2">出演:``。
  该页 tracklist **无徽章**（brand=None）；歌名后可能有 ``<small class="notes">(新曲)</small>``
  （非徽章，保留在标题里，忠实显示）。
- C) 音乐剧型（fixtures/imas_db_cg_musical_dd.html）：公演概要节内 ``<div class="mx-3 my-2">``
  为日期场馆行（含 ``<a>詳細</a>``），**另有一张公演日程表**（DAY1/DAY2 昼/夜 的 ``開場/開演``
  td 单元格，不能误当日期场馆）；出演在 ``<div class="mx-3 my-2">出演:``。
  tracklist 有 ``<tr class="part-header"><th colspan="3">【第X幕 …】</th>`` 幕标题行（<3 个 td，
  跳过）与**无序号行**（td0 为空，no 回退为运行序号）。

通用识别规则（三版式统一）：
- 日期场馆：取 ``<a>詳細</a>``（官方公式サイト链接，每页唯一、总在日期/场馆行）的 div/p 祖先，
  去掉 ``<a>`` 后取文本；无 ``詳細`` 链接时兜底取含「開演/開場」的**最短** div/p
  （最短防外层大容器误匹配）。
- 出演者：首个文本以「出演」开头的 div 内全部 ``span.idol-name`` 文本。
- 歌曲行：``table.tracklist > tbody > tr``；不足 3 个 td 的行跳过（幕标题行/坏行）；
  td0 空/非数字 → ``no`` 回退为运行序号（len+1）。
- 歌名：td1 内 ``<a>`` 文本（有链接时）；否则去掉 ``badge``/``visually-hidden`` 后的文本。
- 编码坑：Content-Type 无 charset，一律 ``resp.content.decode('utf-8')``。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional
from urllib.parse import urljoin

from songbot.models_song import Setlist, Track
from songbot.s1_fetch_events import (  # 复用请求层（重试/异常/UA/超时单一事实源）
    DEFAULT_HEADERS,
    PAGE_BASE_URL,
    REQUEST_TIMEOUT,
    FetchError,
    _request,
)

# 环境兜底：本机依赖已 vendor 化（沙箱无法 pip 安装），正常环境走系统 site-packages
try:
    import httpx
except ImportError:  # pragma: no cover
    _vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
    if os.path.isdir(_vendor):
        sys.path.insert(0, _vendor)
        import httpx
    else:
        raise

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    _vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
    if os.path.isdir(_vendor) and _vendor not in sys.path:
        sys.path.insert(0, _vendor)
        from bs4 import BeautifulSoup
    else:
        raise

logger = logging.getLogger("songbot.s2_fetch_setlist")

# ---------------------------------------------------------------------------
# 常量（选择器集中，站点改版时只改这里）
# ---------------------------------------------------------------------------
SELECTOR_TITLE = "#page_title"            # h1 标题
SELECTOR_DETAIL_LINK = "a"                # 官方公式サイト链接，文本 == 詳細
SELECTOR_TRACK_TABLE = "table.tracklist"  # セットリスト表
SELECTOR_TRACK_ROW = "tr"
SELECTOR_TRACK_TD = "td"
SELECTOR_BADGE = "small"                  # 品牌徽章（class 含 badge）
SELECTOR_HIDDEN_SPAN = "span"             # ( ) 装饰（class 含 visually-hidden）
SELECTOR_IDOL_NAME = "span"               # 演者（class 含 idol-name）

DATE_KEYWORDS = ("開演", "開場")           # 日期/场馆行特征词
PERFORMER_PREFIX = "出演"                 # 出演者块文本前缀

# 应援色（メンバーカラー）：原网页 .idol-name 用 border-bottom 色带，颜色按 data-* 属性映射，
# CSS 覆盖顺序 group > attr > brand（scripts/refresh_site_colors.py 提取到 data/songbot_site_colors.json）
SITE_COLORS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "songbot_site_colors.json"
)
ATTR_DATA_NAMES = (
    # 顺序 = 原网页 CSS 规则后定义覆盖的顺序（后定义的优先）：
    # cinderella < million < million-gree < sidem，故从 sidem 开始取第一个命中
    "data-sidem-attr",
    "data-million-gree-attr",
    "data-million-attr",
    "data-cinderella-attr",
)


def _read_idol_color_tables() -> tuple[dict, dict, dict, dict, dict]:
    """读 (brand_id_map, attr_colors, group_colors, brand_keys, character_colors)；
    JSON 缺失/损坏回退空表（无应援色）。"""
    try:
        with open(SITE_COLORS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return (
            d.get("brand_id_map") or {},
            d.get("attr_colors") or {},
            d.get("group_colors") or {},
            d.get("brand_keys") or {},
            d.get("character_colors") or {},
        )
    except (OSError, ValueError):
        return {}, {}, {}, {}, {}


def _idol_color(span, tables) -> Optional[str]:
    """span.idol-name 的应援色：character > group > attr > brand
    （对应原网页 CSS 后定义覆盖：brand → attr → group → character 规则依次在后）。"""
    brand_id_map, attr_colors, group_colors, brand_keys, character_colors = tables
    cid = span.get("data-character-id")
    if cid and cid in character_colors:
        return character_colors[cid]          # 角色个人应援色（大多数偶像）
    gid = span.get("data-group-id")
    if gid and gid in group_colors:
        return group_colors[gid]
    for attr_name in ATTR_DATA_NAMES:
        val = span.get(attr_name)
        if val:
            key = f"{attr_name[len('data-'):]}-{val}"   # data-cinderella-attr="1" -> cinderella-attr-1
            if key in attr_colors:
                return attr_colors[key]
            return None   # 有 attr 属性但 CSS 无对应变量（如 sidem mental）→ 原网页同样无色
    bid = span.get("data-brand-id")
    if bid:
        key = brand_id_map.get(bid)
        if key:
            return brand_keys.get(key)
    return None


# ---------------------------------------------------------------------------
# 纯解析（不联网，便于离线单测）
# ---------------------------------------------------------------------------
def _strip_anchors(el) -> str:
    """克隆（序列化->重解析，规避 bs4 4.15 deepcopy 陷阱）后去掉全部 <a>，取纯文本。"""
    c = BeautifulSoup(str(el), "html.parser")
    for a in c.find_all("a"):
        a.decompose()
    return c.get_text(" ", strip=True)


def _find_date_venue(soup) -> str:
    """日期/场馆行文本（去「詳細」链接）。

    首选：``<a>詳細</a>`` 所在 div/p 祖先（官方链接每页唯一，总在日期/场馆行）。
    兜底：含「開演/開場」的 div/p 中文本最短者（最短防外层大容器误匹配）。
    """
    for a in soup.find_all(SELECTOR_DETAIL_LINK):
        if a.get_text(strip=True) == "詳細":
            el = a.find_parent(["div", "p"])
            if el is not None:
                return _strip_anchors(el)
    best = None
    best_len: Optional[int] = None
    for el in soup.find_all(["div", "p"]):
        txt = el.get_text(" ", strip=True)
        if any(k in txt for k in DATE_KEYWORDS):
            if best_len is None or len(txt) < best_len:
                best, best_len = el, len(txt)
    return _strip_anchors(best) if best is not None else ""


def _find_performers(soup, tables) -> tuple[list[str], list[Optional[str]]]:
    """出演者：首个文本以「出演」开头的 div 内全部 span.idol-name → (名字, 应援色)。"""
    for d in soup.find_all("div"):
        if d.get_text(" ", strip=True).startswith(PERFORMER_PREFIX):
            spans = d.find_all(SELECTOR_IDOL_NAME, class_="idol-name")
            return (
                [s.get_text(strip=True) for s in spans],
                [_idol_color(s, tables) for s in spans],
            )
    return [], []


def _clean_title(td) -> str:
    """歌名清洗：去掉 badge 与 visually-hidden（( ) 装饰）后取文本；notes 等保留。"""
    c = BeautifulSoup(str(td), "html.parser")
    for tag in c.find_all([SELECTOR_BADGE, SELECTOR_HIDDEN_SPAN]):
        if tag.decomposed:
            continue
        classes = tag.get("class") or []
        if "badge" in classes or "visually-hidden" in classes:
            tag.decompose()
    return c.get_text(" ", strip=True)


def _parse_title_cell(td, base_url: str) -> tuple[str, Optional[str], Optional[str]]:
    """td1（楽曲）-> (title, link, brand)；有 <a> 时标题取链接文本，否则清洗文本。"""
    badge = td.find(SELECTOR_BADGE, class_="badge")
    brand = None
    if badge is not None:
        brand = badge.get("title") or badge.get_text(strip=True) or None
    a = td.find("a")
    if a is not None:
        href = a.get("href") or ""
        link = urljoin(base_url, href) if href else None
        return a.get_text(strip=True), link, brand
    return _clean_title(td), None, brand


def _parse_performers_cell(td, tables) -> tuple[list[str], list[Optional[str]]]:
    """td2（演者）-> (名字, 应援色)；无 idol-name span（如「全員」/「城主(穴沢裕介)」）取整格文本，色为 None。"""
    spans = td.find_all(SELECTOR_IDOL_NAME, class_="idol-name")
    if spans:
        return (
            [s.get_text(strip=True) for s in spans],
            [_idol_color(s, tables) for s in spans],
        )
    text = td.get_text(strip=True)
    return ([text] if text else [], [None] if text else [])


def _parse_track_row(tr, base_url: str, fallback_no: int, tables) -> Optional[Track]:
    """单个歌曲行 -> Track；不足 3 个 td（幕标题行/坏行）返回 None。"""
    tds = tr.find_all(SELECTOR_TRACK_TD)
    if len(tds) < 3:
        return None
    raw_no = tds[0].get_text(strip=True)
    try:
        no = int(raw_no)
    except ValueError:
        no = fallback_no  # 空/非数字（音乐剧无序号行）回退为运行序号
    title, link, brand = _parse_title_cell(tds[1], base_url)
    performers, colors = _parse_performers_cell(tds[2], tables)
    return Track(no=no, title=title, brand=brand, performers=performers,
                 performer_colors=colors, link=link)


def parse_setlist_html(html: str, *, url: str = "", base_url: str = PAGE_BASE_URL,
                       idol_colors: Optional[tuple] = None) -> Setlist:
    """纯解析：详情页 HTML 文本 -> Setlist（不联网）。

    :param html: 详情页 HTML 文本（UTF-8 解码后的 str）
    :param url: 详情页 URL（写入 Setlist.url，并作为相对链接的 urljoin 基准）
    :param base_url: url 为空时的兜底基准（目录语义，须以 / 结尾）
    :param idol_colors: 应援色表 (brand_id_map, attr_colors, group_colors, brand_keys)；
        默认 None 时读 data/songbot_site_colors.json（缺失则无应援色）
    """
    soup = BeautifulSoup(html, "html.parser")
    join_base = url or base_url
    tables = idol_colors if idol_colors is not None else _read_idol_color_tables()

    h1 = soup.select_one(SELECTOR_TITLE)
    title = h1.get_text(strip=True) if h1 is not None else ""

    tracks: list[Track] = []
    table = soup.select_one(SELECTOR_TRACK_TABLE)
    if table is not None:
        tbody = table.find("tbody")
        if tbody is not None:
            for tr in tbody.find_all(SELECTOR_TRACK_ROW, recursive=False):
                track = _parse_track_row(tr, join_base, len(tracks) + 1, tables)
                if track is not None:
                    tracks.append(track)

    performers, performer_colors = _find_performers(soup, tables)
    return Setlist(
        title=title,
        date_venue=_find_date_venue(soup),
        performers=performers,
        performer_colors=performer_colors,
        tracks=tracks,
        url=url,
    )


# ---------------------------------------------------------------------------
# 入口（契约签名冻结：fetch_setlist(url, *, client=None)）
# ---------------------------------------------------------------------------
def fetch_setlist(url: str, *, client=None) -> Setlist:
    """抓取并解析公演详情页。

    :param url: 公演详情页 URL（纯 HTTP，如 http://imas-db.jp/song/event/million_13th_day1.html）
    :param client: 可选 httpx.Client（供单测注入 MockTransport 等）；None 时内部自建
    :raises FetchError: 网络异常重试耗尽 / HTTP 异常 / UTF-8 解码失败
    """
    if client is not None:
        resp = _request(client, "GET", url)
    else:
        with httpx.Client(
            timeout=REQUEST_TIMEOUT, headers=DEFAULT_HEADERS, follow_redirects=True
        ) as c:
            resp = _request(c, "GET", url)
    try:
        html = resp.content.decode("utf-8")  # 显式按字节 UTF-8 解码（站点无 charset）
    except UnicodeDecodeError as exc:
        raise FetchError(f"详情页 UTF-8 解码失败: {url}: {exc}") from exc
    return parse_setlist_html(html, url=url)


# ---------------------------------------------------------------------------
# 命令行自测：python -m songbot.s2_fetch_setlist --url <详情页 URL>
# ---------------------------------------------------------------------------
def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    # Windows 控制台默认 GBK，日文/「・」等字符可能超出可编码范围；强制 stdout 走 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="s2_fetch_setlist", description="抓取 imas-db 公演详情页并打印结构化 Setlist")
    parser.add_argument("--url", required=True, help="公演详情页 URL（纯 HTTP）")
    args = parser.parse_args(argv)

    try:
        sl = fetch_setlist(args.url)
    except FetchError as exc:
        print(f"[ERROR] {exc}")
        return 1

    print(f"标题: {sl.title}")
    print(f"日期/场馆: {sl.date_venue}")
    print(f"出演者({len(sl.performers)}): {'、'.join(sl.performers)}")
    print(f"曲目({len(sl.tracks)}):")
    for t in sl.tracks:
        link = f" link={t.link}" if t.link else ""
        brand = f" [{t.brand}]" if t.brand else ""
        print(f"  {t.no:>3}. {t.title}{brand} | {','.join(t.performers)}{link}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
