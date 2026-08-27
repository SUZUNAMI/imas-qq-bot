"""M1 probe: locate the news-list JSON API of https://idolmaster-official.jp/news.

Path A strategy (from docs/modules/M1-fetcher.md):
1. GET the /news page, collect <script src> chunk URLs.
2. Download the news page chunk + its imported chunks (Next.js chunk graph).
3. Regex-scan chunks for fetch()/axios/API URLs; dump matches for inspection.

Findings are written to .tmp/probe/ so the chunk sources are kept for review.
"""
import os
import re
import sys

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vendor"))
PROBE_DIR = os.path.join(ROOT, ".tmp", "probe")

PAGE_URL = "https://idolmaster-official.jp/news"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}

API_URL_RE = re.compile(r"https?://[^\s\"'`<>(){}]+")
API_PATH_RE = re.compile(r"[/\"']((?:api|v\d+|graphql)[^\"'\s`<>]*?)[\"'`]")
FETCH_RE = re.compile(r"fetch\s*\(|axios|XMLHttpRequest|\.get\s*\(|\.post\s*\(")
URL_BUILD_RE = re.compile(r"`([^`]*\$\{[^`]*\}[^`]*)`|concat\([\"'][^\"']+[\"']\)")


def main() -> int:
    os.makedirs(PROBE_DIR, exist_ok=True)
    client = httpx.Client(timeout=30, headers=UA, follow_redirects=True)

    print(f"[1] GET {PAGE_URL}")
    page = client.get(PAGE_URL)
    page.raise_for_status()
    html = page.text
    (PROBE_DIR / "news.html").write_text(html, encoding="utf-8") if False else None
    with open(os.path.join(PROBE_DIR, "news.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"    status={page.status_code} bytes={len(html)}")

    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    print(f"[2] script srcs: {len(srcs)}")
    for s in srcs:
        print("    ", s)
    news_chunks = [s for s in srcs if "/chunks/pages/news" in s or "pages/news" in s]
    print(f"    news page chunks: {news_chunks}")

    all_urls = []
    for s in srcs:
        url = s if s.startswith("http") else "https://idolmaster-official.jp" + s
        all_urls.append(url)

    # Follow the page chunk's imports (__webpack_require__ ids map to chunk names in Next.js)
    import re as _re
    fetched = set()
    queue = list(all_urls)
    hits = {}
    while queue:
        url = queue.pop(0)
        if url in fetched:
            continue
        fetched.add(url)
        try:
            r = client.get(url)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            print(f"    [warn] {url}: {type(e).__name__}")
            continue
        body = r.text
        fn = url.split("/")[-1].split("?")[0]
        with open(os.path.join(PROBE_DIR, fn), "w", encoding="utf-8") as f:
            f.write(body)
        # find imported chunk paths inside this chunk
        for m in _re.finditer(r'(?:static/chunks/[A-Za-z0-9._\-/]+\.js|"/_next/[^"]+\.js")', body):
            ref = m.group(0).strip('"')
            if not ref.startswith("http"):
                ref = "https://idolmaster-official.jp" + ref if ref.startswith("/") else "https://idolmaster-official.jp/_next/" + ref.lstrip("/")
            if ref not in fetched:
                queue.append(ref)
        # scan for API hints
        for m in API_URL_RE.finditer(body):
            u = m.group(0)
            if "idolmaster" in u or "/api/" in u or "news" in u.lower():
                hits.setdefault("url", []).append(u)
        for m in API_PATH_RE.finditer(body):
            p = m.group(1)
            if "news" in p.lower() or "list" in p.lower():
                hits.setdefault("path", []).append(p)
        if FETCH_RE.search(body):
            hits.setdefault("fetch_in", []).append(fn)
        if len(fetched) > 200:
            print("    [warn] chunk crawl cap reached")
            break

    print(f"[3] crawled {len(fetched)} chunks")
    for k, v in hits.items():
        seen = set()
        print(f"    -- {k} ({len(v)} raw matches, {len(set(v))} unique) --")
        for item in v[:60]:
            if item not in seen:
                seen.add(item)
                print("       ", item)

    # dump NEXT_DATA for reference
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        with open(os.path.join(PROBE_DIR, "next_data.json"), "w", encoding="utf-8") as f:
            f.write(m.group(1))
        print("[4] __NEXT_DATA__ bytes:", len(m.group(1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
