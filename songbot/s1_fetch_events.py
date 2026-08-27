"""S1 列表抓取 + 解析 — 歌曲列表 bot（songbot）.

从 https://imas-db.jp/song/event 抓取事件列表页，解析出全部顶层事件
（单页 / 多日、子公演、日期、URL、品牌徽章、年份），输出 ``Event`` 列表
（年份降序，与网页顺序一致）。

数据源结论（2026-08 探针，见 docs/S-songbot-plan.md §2）
----------------------------------------------------------
- 纯 HTTP（https 连接失败）；服务端渲染 HTML，无 JSON API。
- 列表页按 <h2>YYYY年</h2> 分组（2013–2026，14 组）；每组一个 <ul>，
  顶层事件为 <li data-brand-ids>（2026-08 fixture 实测仅两种形态：
  单页 34 个 = 直接子 <a>；多日 91 个 = 无直接 <a> 且嵌套 <ul><li>）。
- 单页事件：<a href="./xxx.html">文本</a> <small class="date">- YYYY/MM/DD(曜)</small>
- 多日事件：标题由 <li> 去掉嵌套 <ul>、徽章、visually-hidden、日期后的纯文本；
  子公演 <a title="完整标题">DAY1</a> <small class="date">- YYYY/MM/DD(曜)</small>
- 品牌徽章：<span class="badge ..." title="ブランド名">短名</span>，title 优先。
- 编码坑：Content-Type 无 charset，务必按字节显式 UTF-8 解码（resp.content.decode('utf-8')）。
- 已知项：<ruby>（如「H.I.F<ruby><rb>選抜試験</rb><rp>(</rp><rt>セレクション</rt><rp>)</rp></ruby>」）
  bs4>=4.13 的 get_text() 默认排除 rt/rp（ruby 注音）文本，标题得到 rb 优先效果
  （"H.I.F 選抜試験"）；子公演 full_title 取自 <a title> 属性则含 "…選抜試験(セレクション) DAY1"。
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Optional
from urllib.parse import urljoin

from songbot.models_song import Event, SubEvent

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

logger = logging.getLogger("songbot.s1_fetch_events")

# ---------------------------------------------------------------------------
# 常量（选择器集中，站点改版时只改这里）
# ---------------------------------------------------------------------------
EVENT_LIST_URL = "http://imas-db.jp/song/event"      # 列表页（纯 HTTP）
PAGE_BASE_URL = "http://imas-db.jp/song/event/"      # urljoin 基准（必须带尾斜杠，目录语义）

SELECTOR_SECTION = "div.section"          # 年份分组
SELECTOR_YEAR_H2 = "h2"                   # "YYYY年"
SELECTOR_EVENT_LI = "li"                  # 顶层事件（配合 recursive=False + data-brand-ids 过滤）
SELECTOR_SUB_UL = "ul"                    # 多日事件的嵌套列表
SELECTOR_SUB_LI = "li"                    # 子公演（配合 recursive=False）
SELECTOR_BADGE = "span"                   # 品牌徽章（class 含 badge）
SELECTOR_DATE_SMALL = "small"             # 日期（class 含 date）

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.8",
}
REQUEST_TIMEOUT = 25.0     # 秒
RETRY_ATTEMPTS = 3         # 连接失败/5xx 重试 3 次指数退避
RETRY_BASE_DELAY = 1.0     # 秒


class FetchError(RuntimeError):
    """抓取失败（网络错误重试耗尽 / HTTP 异常 / 解码失败），message 面向日志与告警。"""


# ---------------------------------------------------------------------------
# 请求层：httpx + UA + 超时 + 重试（写法对齐 ref/m1_fetcher.py）
# ---------------------------------------------------------------------------
def _request(client: httpx.Client, method: str, url: str) -> httpx.Response:
    """发请求；TransportError/5xx 重试 3 次指数退避，4xx 直接失败；耗尽抛 FetchError。"""
    last_err: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.request(method, url)
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_err = exc
            if attempt < RETRY_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (2**attempt)
                time.sleep(delay)
    assert last_err is not None
    raise FetchError(f"请求失败（已重试 {RETRY_ATTEMPTS} 次）: {method} {url}: {last_err}") from last_err


# ---------------------------------------------------------------------------
# 纯解析（不联网，便于离线单测）
# ---------------------------------------------------------------------------
def _badge_names(li) -> list[str]:
    """品牌徽章名列表：badge 的 title 优先，缺则取文本；无徽章返回空列表。"""
    names: list[str] = []
    for b in li.find_all(SELECTOR_BADGE, class_="badge"):
        names.append(b.get("title") or b.get_text(strip=True) or "")
    return names


def _clean_multi_title(li) -> str:
    """多日事件标题：独立副本去掉嵌套 ul / 徽章 / visually-hidden / 日期后取纯文本。

    注意（bs4 4.15 行为）：
    - ``copy.deepcopy(Tag)`` 直接返回自身（非真深拷贝），decompose 会破坏原始 li；
      因此用「序列化 -> 重解析」建立完全独立的树。
    - ``decompose()`` 会递归清空目标及**全部后代**（attrs 变 None、name 变 ""），
      导致快照列表里仍引用已清除元素；遍历时用 ``PageElement.decomposed`` 跳过。
    """
    c = BeautifulSoup(str(li), "html.parser")
    for tag in c.find_all(["ul", "small", SELECTOR_BADGE]):
        if tag.decomposed:
            continue
        classes = tag.get("class") or []
        if tag.name == "ul":
            tag.decompose()
        elif "date" in classes:
            tag.decompose()
        elif "badge" in classes or "visually-hidden" in classes:
            tag.decompose()
    return c.get_text(" ", strip=True)


def _parse_li(li, year: str, base_url: str) -> Optional[Event]:
    """单个顶层事件 <li> -> Event；坏条目记日志跳过返回 None（不抛异常）。"""
    brands = _badge_names(li)
    direct_a = li.find("a", recursive=False)

    # 单页事件：直接子 <a> 存在
    if direct_a is not None:
        href = direct_a.get("href") or ""
        small = li.find(SELECTOR_DATE_SMALL, class_="date")
        date = small.get_text(strip=True).lstrip("- ") if small else ""
        return Event(
            title=direct_a.get_text(strip=True),
            year=year,
            date=date,
            brands=brands,
            url=urljoin(base_url, href) if href else "",
            sub_events=[],
        )

    # 多日事件：无直接 <a>，依赖嵌套 <ul>
    sub_ul = li.find(SELECTOR_SUB_UL)
    if sub_ul is None:
        logger.warning("跳过坏条目：li 既无直接 <a> 也无嵌套 <ul>（year=%s, text=%r）",
                       year, li.get_text(" ", strip=True)[:80])
        return None

    subs: list[SubEvent] = []
    for x in sub_ul.find_all(SELECTOR_SUB_LI, recursive=False):
        a = x.find("a")
        if a is None:
            logger.warning("跳过坏子事件：嵌套 li 无 <a>（year=%s, text=%r）",
                           year, x.get_text(" ", strip=True)[:60])
            continue
        small = x.find(SELECTOR_DATE_SMALL, class_="date")
        date = small.get_text(strip=True).lstrip("- ") if small else ""
        href = a.get("href") or ""
        subs.append(SubEvent(
            title=a.get_text(strip=True),
            full_title=a.get("title", ""),
            url=urljoin(base_url, href) if href else "",
            date=date,
        ))

    return Event(
        title=_clean_multi_title(li),
        year=year,
        brands=brands,
        url="",
        sub_events=subs,
    )


def parse_events_html(html: str, base_url: str = PAGE_BASE_URL) -> list[Event]:
    """纯解析：HTML 文本 -> Event 列表（年份降序，与网页顺序一致；不联网）。

    :param html: 列表页 HTML 文本（UTF-8 解码后的 str）
    :param base_url: 相对 href 的 urljoin 基准（目录语义，须以 / 结尾）
    :return: 顶层事件列表；坏条目跳过并记日志，不抛异常
    """
    soup = BeautifulSoup(html, "html.parser")
    events: list[Event] = []
    for sec in soup.select(SELECTOR_SECTION):
        h2 = sec.find(SELECTOR_YEAR_H2)
        if h2 is None:
            logger.warning("跳过坏 section：无 <h2>（无法确定年份），text=%r",
                           sec.get_text(" ", strip=True)[:80])
            continue
        year = h2.get_text(strip=True).replace("年", "").strip()
        ul = sec.find(SELECTOR_SUB_UL)
        if ul is None:
            logger.warning("跳过坏 section：year=%s 无 <ul>", year)
            continue
        for li in ul.find_all(SELECTOR_EVENT_LI, recursive=False):
            if li.get("data-brand-ids") is None:
                continue  # 只认顶层事件 li[data-brand-ids]
            event = _parse_li(li, year, base_url)
            if event is not None:
                events.append(event)
    return events


# ---------------------------------------------------------------------------
# 入口（契约签名冻结：fetch_events(url=EVENT_LIST_URL, *, client=None)）
# ---------------------------------------------------------------------------
def fetch_events(url: str = EVENT_LIST_URL, *, client=None) -> list[Event]:
    """抓取并解析事件列表，最新年份在前（与网页顺序一致）。

    :param url: 列表页 URL（默认 EVENT_LIST_URL，纯 HTTP）
    :param client: 可选 httpx.Client（供单测注入 MockTransport 等）；None 时内部自建
    :raises FetchError: 网络异常重试耗尽 / HTTP 异常 / UTF-8 解码失败
    """
    join_base = url.rstrip("/") + "/"   # urljoin 基准（目录语义，须以 / 结尾）
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
        raise FetchError(f"列表页 UTF-8 解码失败: {url}: {exc}") from exc
    return parse_events_html(html, base_url=join_base)


# ---------------------------------------------------------------------------
# 命令行自测：python -m songbot.s1_fetch_events [--url ...] [--full]
# ---------------------------------------------------------------------------
def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="s1_fetch_events", description="抓取 imas-db 歌曲事件列表并打印摘要")
    parser.add_argument("--url", default=EVENT_LIST_URL, help=f"列表页 URL（默认 {EVENT_LIST_URL}）")
    parser.add_argument("--full", action="store_true", help="打印全部事件明细（默认只打印 10 条）")
    args = parser.parse_args(argv)

    try:
        events = fetch_events(args.url)
    except FetchError as exc:
        print(f"[ERROR] {exc}")
        return 1

    print(f"共 {len(events)} 个顶层事件（年份降序）：")
    shown = events if args.full else events[:10]
    for ev in shown:
        if ev.sub_events:
            subs = ", ".join(f"{s.title} ({s.date})" for s in ev.sub_events)
            print(f"  [多日] {ev.year} {ev.title} | brands={ev.brands} | subs=[{subs}]")
        else:
            print(f"  [单页] {ev.year} {ev.title} | url={ev.url} | brands={ev.brands}")
    if not args.full and len(events) > 10:
        print(f"  …（共 {len(events)} 条，--full 查看全部）")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
