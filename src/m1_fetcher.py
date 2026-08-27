"""M1 列表抓取（Fetcher）— 爱马仕官方新闻 QQ 转发机器人.

从 https://idolmaster-official.jp/news 获取最新新闻列表，输出 ``NewsItem`` 列表（最新在前）。

数据源（2026-08 探针定位，见 docs/modules/M1-fetcher-worklog.md）
-------------------------------------------------------------------------
本站是 Next.js SPA，列表数据由前端 JS 从 CMS 直连 API 拉取（不走 HTML/SSR）。
经前端 chunk 逆向，得到如下接口（base = ``https://cmsapi-frontend.idolmaster-official.jp/sitern/api/``）：

1. Token（每次运行先取，供列表接口鉴权）:
   GET {base}cmsbase/Token/get
   -> {"statusCode":200,"data":{"token":"<hex>","limit":<配额>,"time":0}}

2. 新闻列表:
   GET {base}idolmaster/Article/list
       ?site=jp&ip=idolmaster&token=<token>&data=<urlencoded JSON>&limit=<N>&start=0
   data JSON = {"category":["NEWS"],"subcategory":[],"brand":null}
   -> {"statusCode":200,"data":{"total_count":N,"article_list":[{...}]}}
   article 关键字段: path(如 "01_19692") / title / startdate(Unix, JST) / dspdate("YYYY/MM/DD HH:mm") /
                     thumbnail(相对路径) / url(带 .html 后缀) / publish_status / delflg
   返回顺序即最新在前（按 startdate 排序复核）。

3. 配图（NewsItem.thumbnail 用此 URL）:
   GET {base}idolmaster/Image/get?path=<thumbnail 相对路径>
   -> 图片二进制（image/jpeg 等）。直连 idolmaster-official.jp 的同路径返回 404。

4. 分企划筛选（brands 白名单）:
   - 官方 7 个 brand tag（cmsbase/SiteCommon/get 的 brand 定义，2026-08 实测）见 BRAND_CODES。
   - 注意：列表接口 data JSON 里的 "brand" 参数**并不真正过滤**（实测返回列表仍含全部品牌，
     只影响 total_count）——因此筛选在**客户端**做（fetch_news_list(brands=[...])）。
   - 匹配语义：article.brand 为数组，跨企划合作新闻含多个 brand；任一 code ∈ 白名单即保留。
   - 无 brand 字段的条目在给定白名单时会被排除（0/200 样本中未出现，边界防御）。

契约（docs/module-specs.md §1.1）：id=URL 末段(path)、url=详情页完整 URL、date="YYYY-MM-DD"、thumbnail 可为 None。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from models import NewsItem  # 契约类型单一事实源（module-specs §1，见 src/models.py）

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

# ---------------------------------------------------------------------------
# 常量（探针结论固化）
# ---------------------------------------------------------------------------
CMS_API_BASE = "https://cmsapi-frontend.idolmaster-official.jp/sitern/api/"
NEWS_SITE_BASE = "https://idolmaster-official.jp"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ja,en;q=0.8",
}

JST = timezone(timedelta(hours=9))  # 本站日期口径为日本时间

DEFAULT_LIMIT = 20          # 只需最新 10–20 条（规格 §8：勿抓历史全部）
REQUEST_TIMEOUT = 25.0      # 秒
RETRY_ATTEMPTS = 3          # 连接失败/5xx 重试 3 次指数退避
RETRY_BASE_DELAY = 1.0      # 秒

# 官方 7 个分企划 tag（cmsbase/SiteCommon/get 的 brand 定义，2026-08 实测）。
# 供 fetch_news_list(brands=...) 白名单使用；值恒为大写。
BRAND_CODES: dict[str, str] = {
    "IDOLMASTER": "THE IDOLM@STER（765PRO ALLSTARS）",
    "CINDERELLAGIRLS": "シンデレラガールズ",
    "MILLIONLIVE": "ミリオンライブ！",
    "SIDEM": "SideM",
    "SHINYCOLORS": "シャイニーカラーズ",
    "GAKUEN": "学園アイドルマスター",
    "OTHER": "その他",
}


class FetchError(RuntimeError):
    """抓取失败（网络错误重试耗尽 / 响应异常），message 面向日志与告警。"""


# NewsItem 契约类型已移至 src/models.py（统一 import 防漂移），此处仅 re-export，
# 保持 `from m1_fetcher import NewsItem` 的既有公共 API 不变。


# ---------------------------------------------------------------------------
# 请求层：httpx + UA + 超时 + 重试 3 次指数退避
# ---------------------------------------------------------------------------
def _request(client: httpx.Client, method: str, url: str, *, params: Optional[dict] = None) -> httpx.Response:
    """发请求；TransportError/5xx 重试 3 次指数退避，4xx 直接失败；耗尽抛 FetchError。"""
    last_err: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.request(method, url, params=params)
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


def _get_cms_token(client: httpx.Client, base: str = CMS_API_BASE) -> str:
    """取 CMS 会话 token（列表接口鉴权用）。"""
    resp = _request(client, "GET", base + "cmsbase/Token/get")
    try:
        token = resp.json()["data"]["token"]
    except (ValueError, KeyError, TypeError) as exc:
        raise FetchError(f"Token 接口响应格式异常: {resp.text[:200]}") from exc
    if not token:
        raise FetchError("Token 接口返回空 token")
    return token


# ---------------------------------------------------------------------------
# 纯映射：API article -> NewsItem（不依赖网络，便于单测）
# ---------------------------------------------------------------------------
def _fmt_date(startdate: Optional[int], dspdate: Optional[str], updated: Optional[int]) -> str:
    """优先 startdate(Unix, JST)，其次 dspdate("YYYY/MM/DD HH:mm")，兜底 updated。"""
    if startdate:
        return datetime.fromtimestamp(int(startdate), JST).strftime("%Y-%m-%d")
    if dspdate:
        m = re.match(r"(\d{4})/(\d{2})/(\d{2})", dspdate)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if updated:
        return datetime.fromtimestamp(int(updated), JST).strftime("%Y-%m-%d")
    return ""


def _article_to_item(article: dict, base: str = CMS_API_BASE) -> Optional[NewsItem]:
    """单个 CMS article -> NewsItem；无效条目（已删除/未发布/缺 path）返回 None。"""
    if article.get("delflg") == "1" or article.get("publish_status") not in (None, "publish"):
        return None
    path = article.get("path") or ""
    if not path:
        return None
    thumb_rel = (article.get("thumbnail") or "").split("?")[0]  # 去掉 _= 缓存参数，否则 Image/get 返回 404
    return NewsItem(
        id=path,
        url=f"{NEWS_SITE_BASE}/news/{path}",
        title=article.get("title") or "",
        date=_fmt_date(article.get("startdate"), article.get("dspdate"), article.get("updated")),
        thumbnail=f"{base}idolmaster/Image/get?path={thumb_rel}" if thumb_rel else None,
    )


def _normalize_brands(brands) -> Optional[set[str]]:
    """把 brands 白名单规整为全大写 code 集合；None / 空序列 -> None（不过滤）。"""
    if brands is None:
        return None
    if isinstance(brands, str):
        brands = [brands]
    codes = {str(b).strip().upper() for b in brands if str(b).strip()}
    return codes or None


def _article_has_brand(article: dict, brand_set: set[str]) -> bool:
    """article.brand 为数组（跨企划合作新闻可含多个）；任一 code ∈ 白名单即 True。"""
    for b in article.get("brand") or []:
        if isinstance(b, dict) and (b.get("code") or "").upper() in brand_set:
            return True
    return False


def _articles_to_items(
    article_list: list,
    brands=None,
    base: str = CMS_API_BASE,
    min_updated: Optional[int] = None,
) -> list[NewsItem]:
    """分企划筛选（可选）+ 时间截断（可选）+ 过滤 + 去重 + 按 startdate 降序（最新在前）。

    :param min_updated: 只保留 ``updated``（缺失回退 ``startdate``）>= 该 Unix 时间戳的条目；
        用于「只推送启动时间之后更新/发布的新闻」的客户端截断（M7 2026-08 追加，向后兼容）。
    """
    brand_set = _normalize_brands(brands)
    items: list[NewsItem] = []
    seen: set[str] = set()
    for article in article_list:
        if not isinstance(article, dict):
            continue
        if brand_set is not None and not _article_has_brand(article, brand_set):
            continue
        if min_updated is not None and (article.get("updated") or article.get("startdate") or 0) < min_updated:
            continue
        item = _article_to_item(article, base=base)
        if item is None or item.id in seen:
            continue
        seen.add(item.id)
        items.append((article.get("startdate") or 0, item))
    items.sort(key=lambda t: (-int(t[0]), t[1].id))
    return [item for _, item in items]


# ---------------------------------------------------------------------------
# 入口（契约签名冻结）
# ---------------------------------------------------------------------------
def fetch_news_list(
    limit: int = DEFAULT_LIMIT,
    brands=None,
    api_base: Optional[str] = None,
    min_updated: Optional[int] = None,
) -> list[NewsItem]:
    """抓取最新新闻列表，最新在前。

    :param limit: 期望条数（默认 20，规格只要求最新 10–20 条）
    :param brands: 可选分企划白名单（官方 7 个 code 见 BRAND_CODES），如 ["SHINYCOLORS", "GAKUEN"]。
        传字符串会被当单元素处理；大小写不敏感。None / 空序列 = 不过滤（默认）。
        注意：白名单过滤在客户端做（服务端 data.brand 不真正过滤），因此返回条数可能 < limit。
    :param api_base: 可选 CMS API 基址覆盖（默认 CMS_API_BASE，含尾部斜杠）——
        2026-08-26 M7 追加，向后兼容：供 M7 从 config.yaml 的 orchestrator.api_base 显式调整，无需改代码。
    :param min_updated: 可选时间截断（Unix 秒）——只保留 ``updated``（缺失回退 ``startdate``）
        >= 该值的条目；M7 用它实现「只推送启动时间之后更新的新闻」（2026-08-26 追加，向后兼容）。
    :raises FetchError: 网络异常重试耗尽 / 接口异常 / 响应格式异常（不静默返回空列表）
    """
    if limit <= 0:
        raise ValueError("limit 必须为正整数")
    base = (api_base or CMS_API_BASE).rstrip("/") + "/"
    with httpx.Client(timeout=REQUEST_TIMEOUT, headers=DEFAULT_HEADERS, follow_redirects=True) as client:
        token = _get_cms_token(client, base=base)
        data = json.dumps({"category": ["NEWS"], "subcategory": [], "brand": None}, ensure_ascii=False)
        resp = _request(
            client,
            "GET",
            base + "idolmaster/Article/list",
            params={
                "site": "jp",
                "ip": "idolmaster",
                "token": token,
                "data": data,
                "limit": limit,
                "start": 0,
            },
        )
        try:
            payload = resp.json()
            article_list = payload["data"]["article_list"]
        except (ValueError, KeyError, TypeError) as exc:
            raise FetchError(f"列表接口响应格式异常: {resp.text[:300]}") from exc
        if not isinstance(article_list, list):
            raise FetchError(f"列表接口 article_list 不是数组: {resp.text[:300]}")
    return _articles_to_items(article_list, brands=brands, base=base, min_updated=min_updated)


# ---------------------------------------------------------------------------
# 命令行自测：python src/m1_fetcher.py [--limit N] [--brands SC,GAKUEN]
# ---------------------------------------------------------------------------
def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="m1_fetcher",
        description="抓取爱马仕官方最新新闻列表（可选按分企划筛选）",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"抓取窗口条数（默认 {DEFAULT_LIMIT}）")
    parser.add_argument(
        "--brands",
        help="分企划白名单，逗号分隔（大小写不敏感）。可选: " + ",".join(BRAND_CODES),
    )
    args = parser.parse_args(argv)

    brands = None
    if args.brands:
        brands = [b.strip() for b in args.brands.split(",") if b.strip()]
        unknown = sorted({b.upper() for b in brands} - set(BRAND_CODES))
        if unknown:
            parser.error(f"未知品牌 code: {unknown}；可选: {','.join(BRAND_CODES)}")

    try:
        items = fetch_news_list(limit=args.limit, brands=brands)
    except FetchError as exc:
        print(f"[ERROR] {exc}")
        return 1
    label = "全部企划" if brands is None else "企划=" + ",".join(sorted({b.upper() for b in brands}))
    print(f"共获取 {len(items)} 条（{label}），最新 10 条：")
    for item in items[:10]:
        print(json.dumps(
            {"id": item.id, "url": item.url, "title": item.title, "date": item.date, "thumbnail": item.thumbnail},
            ensure_ascii=False,
        ))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
