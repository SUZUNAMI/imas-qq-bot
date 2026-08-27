"""Probe: enumerate brand codes seen in news articles + test API-side brand filter.

1. Fetch a large window of news articles and tally article['brand'][*]['code'].
2. Test whether `data={"category":["NEWS"],"brand":["SHINYCOLORS"]}` filters server-side.
3. Try cmsbase/SiteCommon/get to see if the site config exposes the brand list.
"""
import json
import os
import sys
from collections import Counter

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vendor"))

BASE = "https://cmsapi-frontend.idolmaster-official.jp/sitern/api/"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}


def main() -> int:
    c = httpx.Client(timeout=25, headers=UA, follow_redirects=True)
    token = c.get(BASE + "cmsbase/Token/get").json()["data"]["token"]
    print("token ok")

    # 1) brand distribution over a wide window
    data = json.dumps({"category": ["NEWS"], "subcategory": [], "brand": None}, ensure_ascii=False)
    r = c.get(BASE + "idolmaster/Article/list", params={"site": "jp", "ip": "idolmaster", "token": token, "data": data, "limit": 200, "start": 0})
    arts = r.json()["data"]["article_list"]
    print(f"fetched {len(arts)} articles; total={r.json()['data']['total_count']}")
    brand_counter: Counter = Counter()
    no_brand = 0
    multi_brand = 0
    for a in arts:
        brands = a.get("brand")
        if not brands:
            no_brand += 1
            brand_counter["<none>"] += 1
        else:
            if len(brands) > 1:
                multi_brand += 1
            for b in brands:
                brand_counter[(b.get("code") or "?") + " | " + (b.get("name") or "?")] += 1
    print("--- brand distribution (code | name) over", len(arts), "articles ---")
    for k, v in brand_counter.most_common():
        print(f"  {v:4d}  {k}")
    print(f"articles with NO brand: {no_brand}, with MULTI brand: {multi_brand}")

    # 2) server-side brand filter test
    for probe in [
        {"category": ["NEWS"], "subcategory": [], "brand": ["SHINYCOLORS"]},
        {"category": ["NEWS"], "subcategory": [], "brand": ["CINDERELLAGIRLS", "MILLIONLIVE"]},
    ]:
        d2 = json.dumps(probe, ensure_ascii=False)
        r2 = c.get(BASE + "idolmaster/Article/list", params={"site": "jp", "ip": "idolmaster", "token": token, "data": d2, "limit": 20, "start": 0})
        j2 = r2.json()
        arts2 = j2["data"]["article_list"]
        codes = sorted({b["code"] for a in arts2 for b in (a.get("brand") or [])})
        print(f"filter {probe['brand']}: total={j2['data']['total_count']}, returned={len(arts2)}, brand codes seen={codes}")

    # 3) SiteCommon/get structure (brand definitions?)
    r3 = c.get(BASE + "cmsbase/SiteCommon/get", params={"site": "jp", "ip": "idolmaster", "token": token})
    print("SiteCommon status", r3.status_code)
    if r3.status_code == 200:
        j3 = r3.json()
        d3 = j3.get("data") or {}
        print("SiteCommon data keys:", list(d3.keys())[:20])
        brands = d3.get("brand")
        print("brand def:", json.dumps(brands, ensure_ascii=False)[:1500] if brands else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
