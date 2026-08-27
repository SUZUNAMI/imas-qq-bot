"""Probe: verify thumbnail fetch paths (Image/get API vs direct)."""
import os
import sys

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vendor"))

P = "/idolmaster/jp/article/019/2026/08/41CuKP9O0hPS9zRqWpWpNFD8uDmbPuCj.jpeg"


def main() -> int:
    c = httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
    urls = [
        "https://cmsapi-frontend.idolmaster-official.jp/sitern/api/idolmaster/Image/get?path=" + P,
        "https://idolmaster-official.jp" + P,
    ]
    for u in urls:
        try:
            r = c.get(u, timeout=20)
            print(r.status_code, r.headers.get("content-type"), len(r.content), u[:120])
        except Exception as e:  # noqa: BLE001
            print("ERR", type(e).__name__, str(e)[:100], u[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
