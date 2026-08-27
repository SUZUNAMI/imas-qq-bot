"""Search downloaded chunks for query patterns (react-query keys, API paths)."""
import os
import re
import sys

PROBE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tmp", "probe")

FILES = [
    "news-0b66dc8609dad28f.js",
    "6223-0abc0fb44e37a379.js",
    "3391-3b23f458411aae08.js",
    "9780-f9a33a34c81e45d5.js",
    "main-fb8a07c6126d11d0.js",
]

PATS = [
    r'\["news"',
    r'"news"',
    r"useQuery",
    r"queryKey",
    r"page_index",
    r"pageIndex",
    r'limit:',
    r"category:",
    r"news/list",
    r"newsList",
    r"getNews",
]


def main() -> int:
    for fn in FILES:
        p = os.path.join(PROBE, fn)
        if not os.path.exists(p):
            print(f"[skip] {fn}")
            continue
        body = open(p, encoding="utf-8", errors="replace").read()
        print(f"##### {fn} ({len(body)} bytes)")
        for pat in PATS:
            hits = list(re.finditer(pat, body))
            if not hits:
                continue
            print(f"  -- {pat}: {len(hits)} hits --")
            for m in hits[:8]:
                s = max(0, m.start() - 110)
                e = min(len(body), m.end() + 160)
                ctx = body[s:e].replace("\n", " ")
                if len(ctx) > 270:
                    ctx = ctx[:270] + "..."
                print("     ", ctx)
                print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
