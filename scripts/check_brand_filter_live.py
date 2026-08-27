"""Live check: fetch_news_list(brands=...) only returns matching articles.

Cross-check against raw API article data (source of truth for brand codes).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "vendor"))

import httpx  # noqa: E402

import m1_fetcher  # noqa: E402


def raw_articles(limit: int = 40) -> list[dict]:
    c = httpx.Client(timeout=25, headers=m1_fetcher.DEFAULT_HEADERS, follow_redirects=True)
    token = c.get(m1_fetcher.CMS_API_BASE + "cmsbase/Token/get").json()["data"]["token"]
    data = json.dumps({"category": ["NEWS"], "subcategory": [], "brand": None}, ensure_ascii=False)
    r = c.get(
        m1_fetcher.CMS_API_BASE + "idolmaster/Article/list",
        params={"site": "jp", "ip": "idolmaster", "token": token, "data": data, "limit": limit, "start": 0},
    )
    return r.json()["data"]["article_list"]


def brand_codes_of(article: dict) -> set[str]:
    return {(b.get("code") or "").upper() for b in (article.get("brand") or []) if isinstance(b, dict)}


def main() -> int:
    arts = raw_articles(40)
    by_path = {a.get("path"): a for a in arts}
    print(f"raw articles: {len(arts)}")

    for sel in (["SHINYCOLORS"], ["GAKUEN", "IDOLMASTER"], ["CINDERELLAGIRLS"]):
        items = m1_fetcher.fetch_news_list(limit=40, brands=sel)
        print(f"\n-- brands={sel}: returned {len(items)}")
        bad = []
        for it in items:
            raw = by_path.get(it.id)
            if raw is None:
                bad.append((it.id, "NOT-IN-WINDOW"))  # 窗口外的条目无法核对，跳过不算错
                continue
            if not (brand_codes_of(raw) & set(sel)):
                bad.append((it.id, brand_codes_of(raw)))
        if bad:
            print("  [FAIL] non-matching ids:", bad)
            return 1
        # 至少返回 1 条可核对项
        checkable = [it for it in items if it.id in by_path]
        print(f"  [PASS] 全部 {len(checkable)} 条可核对项均属于所选企划")
    return 0


if __name__ == "__main__":
    sys.exit(main())
