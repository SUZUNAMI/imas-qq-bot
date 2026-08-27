"""Probe: fetch a news detail page and inspect real <img> URLs (thumbnail host)."""
import os
import re
import sys

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vendor"))

BASE = "https://idolmaster-official.jp"


def main() -> int:
    c = httpx.Client(timeout=25, headers={"User-Agent": "Mozilla/5.0 Chrome/126.0"}, follow_redirects=True)
    r = c.get(BASE + "/news/01_19692")
    print("status", r.status_code, "bytes", len(r.content))
    html = r.text
    imgs = re.findall(r'<img[^>]+src="([^"]+)"', html)
    print("imgs:", imgs[:12])
    for m in re.findall(r'srcSet="([^"]+)"', html)[:6]:
        print("srcSet:", m[:200])
    for m in re.findall(r'"[^"]*article/[^"]*"', html)[:8]:
        print("article-ref:", m[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
