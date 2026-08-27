"""M2 详情解析（DetailParser）— 爱马仕官方新闻 QQ 转发机器人.

给定 ``NewsItem``（M1 输出），抓详情页，提取标题/日期/正文/配图，输出 ``NewsDetail``
（契约 docs/module-specs.md §1.2）。

数据源（2026-08 探针固化，见 docs/modules/M2-detail-parser-worklog.md）
-------------------------------------------------------------------------
详情页是 Next.js SSR 页面，HTML 内 ``<script id="__NEXT_DATA__" ...>`` 的 JSON
包含文章数据（约 5–7KB），无需无头浏览器：

    props.pageProps.data = {
        "path": "01_19692",                 # id（URL 末段）
        "title": "【シャニマス】…",          # 标题（原文日文）
        "startdate": 1787731200,            # Unix 秒（JST）；兜底 dspdate "YYYY/MM/DD HH:mm"
        "content": "<div …>…</div>",        # 正文 HTML（c-txt 文本 / component-photo 配图 / br 分段）
        "use_image": [{"path": "/idolmaster/…", "filename": "x.jpeg"}, …],  # 配图清单（可能 10+ 张）
    }

要点（实测）：
1. 正文纯文本：方案 B——``<br>``→换行、块级标签边界换行、剥离标签、空白规范化、
   连续空行压缩为段落分隔 ``\n\n``。
2. 配图 URL：content 内 ``<img src>`` 为相对路径（如 ``/idolmaster/jp/article/…``），
   **直连 idolmaster-official.jp 同路径返回 404**，必须转
   ``{CMS_API_BASE}idolmaster/Image/get?path=<相对路径>``（与 M1 缩略图同法，200 image/jpeg）。
3. 配图去重 + 上限 4 张（契约 §1.2 建议）；优先 content 的 img（兼容 data-src 懒加载），
   兜底 use_image。
4. 容错：``__NEXT_DATA__`` 缺失/损坏时回退直接解析 HTML 正文容器（``.c-gallery``），
   标题回退 ``meta[og:title]``；仍拿不到则抛 ``ParseError``，不返回半截数据。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

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

from bs4 import BeautifulSoup  # noqa: E402

from models import NewsDetail, NewsItem  # noqa: E402  契约类型单一事实源（module-specs §1，见 src/models.py）

# ---------------------------------------------------------------------------
# 常量（探针结论固化；站点改版时先改这里）
# ---------------------------------------------------------------------------
SITE_BASE = "https://idolmaster-official.jp"
CMS_API_BASE = "https://cmsapi-frontend.idolmaster-official.jp/sitern/api/"
IMAGE_ENDPOINT = CMS_API_BASE + "idolmaster/Image/get"   # 配图直连 404，必须走此接口

# __NEXT_DATA__ JSON 内文章数据的路径（逐层取 dict）
NEXT_DATA_DATA_PATH: tuple[str, ...] = ("props", "pageProps", "data")
# 文章 data 节点内的字段名（改版集中维护）
DATA_FIELD_ID = "path"
DATA_FIELD_TITLE = "title"
DATA_FIELD_STARTDATE = "startdate"
DATA_FIELD_DSPDATE = "dspdate"
DATA_FIELD_CONTENT = "content"
DATA_FIELD_USE_IMAGE = "use_image"
DATA_FIELD_URL = "url"

# fallback（__NEXT_DATA__ 缺失）时用的 HTML 选择器
FALLBACK_BODY_SELECTORS: tuple[str, ...] = (".c-gallery", ".c-txt")   # 正文容器，取第一个命中
FALLBACK_TITLE_SELECTOR = 'meta[property="og:title"]'                 # 取 content 属性

MAX_IMAGES = 4           # 契约 §1.2 建议上限
REQUEST_TIMEOUT = 25.0   # 秒
RETRY_ATTEMPTS = 3       # 连接失败/5xx 重试 3 次指数退避
RETRY_BASE_DELAY = 1.0   # 秒

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.8",
}

JST = timezone(timedelta(hours=9))  # 本站日期口径为日本时间

# 正文 HTML 中视为块级边界的标签（转换纯文本时在边界换行）
_HTML_BLOCK_TAGS = {
    "div", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "ul", "ol", "section", "article", "table", "tr",
}

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


class ParseError(RuntimeError):
    """抓取失败（重试耗尽）/ 详情结构异常。message 面向日志与告警，不返回半截数据。"""


# ---------------------------------------------------------------------------
# 网络层：httpx + UA + 超时 + 重试 3 次指数退避
# ---------------------------------------------------------------------------
def _fetch_html(client: httpx.Client, url: str) -> str:
    """GET 详情页返回 HTML；TransportError/5xx 重试 3 次指数退避，耗尽抛 ParseError。"""
    last_err: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.get(url)
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.text
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_err = exc
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BASE_DELAY * (2**attempt))
    assert last_err is not None
    raise ParseError(f"抓取详情页失败（已重试 {RETRY_ATTEMPTS} 次）: {url}: {last_err}")


# ---------------------------------------------------------------------------
# __NEXT_DATA__ 提取（纯函数，便于单测）
# ---------------------------------------------------------------------------
def _extract_next_data(html: str) -> Optional[dict]:
    """从 HTML 提取 __NEXT_DATA__ JSON；缺失或解析失败返回 None。"""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _get_data_node(next_data: Optional[dict]) -> Optional[dict]:
    """沿 NEXT_DATA_DATA_PATH 取文章数据节点；路径缺失/类型不符返回 None。"""
    node: object = next_data
    for key in NEXT_DATA_DATA_PATH:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, dict) else None


# ---------------------------------------------------------------------------
# 纯映射：data 节点 -> NewsDetail（不依赖网络）
# ---------------------------------------------------------------------------
def _fmt_date(startdate: Optional[int], dspdate: Optional[str]) -> str:
    """优先 startdate(Unix, JST)，兜底 dspdate("YYYY/MM/DD HH:mm")；都无返回 ""。"""
    if startdate:
        return datetime.fromtimestamp(int(startdate), JST).strftime("%Y-%m-%d")
    if dspdate:
        m = re.match(r"(\d{4})/(\d{2})/(\d{2})", dspdate)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def _html_to_text(html: str) -> str:
    """正文 HTML -> 纯文本：剥离标签、保留段落（连续空行压成 \\n\\n 分隔）。

    - ``<br>`` 换成换行（原文段落即 ``<br><br>`` 分隔）
    - 块级标签边界换行（div/p/h1-h6/li/…）
    - 全角/不换行空格归一为普通空格；行首尾空白 strip
    - 连续空行压缩为段落边界（单个 \\n\\n）
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(True):
        if tag.name in _HTML_BLOCK_TAGS:
            tag.insert_after("\n")
    text = soup.get_text()
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    lines = [ln.strip() for ln in text.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln:
            out.append(ln)
        elif out and out[-1] != "":   # 第一个空行标记段落边界
            out.append("")
    return "\n".join(out)


def _image_get_url(rel_path: str) -> str:
    """相对图片路径 -> 可访问的 CMS Image/get URL（直连同路径 404）。"""
    path = rel_path.strip()
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        # 已是完整 URL：本站路径仍归一化为 Image/get（直连 404），外站 URL 原样保留
        if path.startswith(SITE_BASE):
            path = path[len(SITE_BASE):]
        else:
            return path
    return f"{IMAGE_ENDPOINT}?path={path}"


def _collect_content_image_paths(content_html: str) -> list[str]:
    """从正文 HTML 收集 <img> 的 src（兼容懒加载 data-src），保持出现顺序。"""
    paths: list[str] = []
    if not content_html:
        return paths
    soup = BeautifulSoup(content_html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        src = src.strip()
        if src and src not in paths:
            paths.append(src)
    return paths


def _extract_images(content_html: str, use_image: Optional[list]) -> list[str]:
    """正文配图 URL 列表：content <img> 优先，兜底 use_image[{path,filename}]；
    全部 Image/get 化；去重；上限 MAX_IMAGES。"""
    paths = _collect_content_image_paths(content_html)
    if not paths and use_image:
        for entry in use_image:
            if not isinstance(entry, dict):
                continue
            p = (entry.get("path") or "").strip().rstrip("/")
            f = (entry.get("filename") or "").strip()
            if p and f:
                paths.append(f"{p}/{f}")
    urls: list[str] = []
    for p in paths:
        u = _image_get_url(p)
        if u and u not in urls:
            urls.append(u)
        if len(urls) >= MAX_IMAGES:
            break
    return urls


def _fallback_title(html: str, item_title: str) -> str:
    """__NEXT_DATA__ 缺失时的标题兜底：og:title -> <title>（去站点后缀）-> NewsItem.title。"""
    soup = BeautifulSoup(html, "html.parser")
    og = soup.select_one(FALLBACK_TITLE_SELECTOR)
    if og and og.get("content", "").strip():
        return og["content"].strip()
    t = soup.title
    if t and t.get_text(strip=True):
        return re.split(r"\s*\|\s*", t.get_text(strip=True), maxsplit=1)[0]
    return item_title


def _fallback_detail(item: NewsItem, html: str) -> NewsDetail:
    """__NEXT_DATA__ 缺失/异常时：直接解析 HTML 正文容器（规格 §8 回退路径）。"""
    soup = BeautifulSoup(html, "html.parser")
    container = None
    for sel in FALLBACK_BODY_SELECTORS:
        container = soup.select_one(sel)
        if container is not None:
            break
    body_html = str(container) if container is not None else ""
    return NewsDetail(
        id=item.id,
        url=item.url,
        title=_fallback_title(html, item.title),
        date=item.date,
        body_text=_html_to_text(body_html),
        images=_extract_images(body_html, None),
    )


def _build_detail(item: NewsItem, data: dict, html: str) -> NewsDetail:
    """主路径：__NEXT_DATA__ 的 data 节点 -> NewsDetail。

    校验：title 必须有（否则走 fallback 标题，仍无则抛 ParseError）；
    date 兜底 NewsItem.date（同一数据源的 startdate）；body 可为空（纯图新闻）。
    """
    title = (data.get(DATA_FIELD_TITLE) or "").strip()
    if not title:
        title = _fallback_title(html, item.title)
    if not title:
        raise ParseError(f"详情页缺标题: {item.url}")
    date = _fmt_date(data.get(DATA_FIELD_STARTDATE), data.get(DATA_FIELD_DSPDATE)) or item.date
    content_html = data.get(DATA_FIELD_CONTENT) or ""
    return NewsDetail(
        id=item.id,
        url=item.url,
        title=title,
        date=date,
        body_text=_html_to_text(content_html),
        images=_extract_images(content_html, data.get(DATA_FIELD_USE_IMAGE)),
    )


# ---------------------------------------------------------------------------
# 入口（契约签名冻结）
# ---------------------------------------------------------------------------
def parse_detail(item: NewsItem) -> NewsDetail:
    """抓取并解析一条新闻的详情。

    :param item: M1 输出的 ``NewsItem``（只用到 url / id / title / date）
    :raises ValueError: item.url 缺失
    :raises ParseError: 抓取失败重试耗尽 / __NEXT_DATA__ 与 HTML fallback 均无法取得标题
    """
    if not item.url:
        raise ValueError("NewsItem.url 不能为空")
    with httpx.Client(timeout=REQUEST_TIMEOUT, headers=DEFAULT_HEADERS, follow_redirects=True) as client:
        html = _fetch_html(client, item.url)
    next_data = _extract_next_data(html)
    data = _get_data_node(next_data)
    if data is None:
        # 规格 §8：__NEXT_DATA__ 缺失 → 回退直接解析 HTML 正文容器
        return _fallback_detail(item, html)
    return _build_detail(item, data, html)


# ---------------------------------------------------------------------------
# 命令行自测：python src/m2_parser.py <news-id 或 URL>（缺省取最新一条）
# ---------------------------------------------------------------------------
def _main() -> int:
    from m1_fetcher import fetch_news_list

    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg:
        url = arg if arg.startswith("http") else f"{SITE_BASE}/news/{arg}"
        item = NewsItem(id=arg.rsplit("/", 1)[-1], url=url, title="", date="")
    else:
        items = fetch_news_list(limit=1)
        if not items:
            print("[ERROR] 列表为空")
            return 1
        item = items[0]
    try:
        detail = parse_detail(item)
    except (ParseError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    print(json.dumps({
        "id": detail.id,
        "url": detail.url,
        "title": detail.title,
        "date": detail.date,
        "body_text": detail.body_text[:500] + ("…" if len(detail.body_text) > 500 else ""),
        "images": detail.images,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
