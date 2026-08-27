"""Find the news-list request builder: context around `.../list"` concat calls."""
import os
import re

PROBE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tmp", "probe")


def main() -> int:
    for fn in sorted(os.listdir(PROBE)):
        if not fn.endswith(".js"):
            continue
        body = open(os.path.join(PROBE, fn), encoding="utf-8", errors="replace").read()
        for m in re.finditer(r'concat\([^)]*"/list"', body):
            s = max(0, m.start() - 2500)
            e = min(len(body), m.end() + 700)
            print(f"##### {fn} @ {m.start()}")
            print(body[s:e])
            print()
    return 0


if __name__ == "__main__":
    main()
