"""Dump a webpack module from a chunk: python scripts/dump_module.py <chunk> <module_id>"""
import os
import re
import sys

PROBE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tmp", "probe")


def extract_module(body: str, mod_id: str) -> str | None:
    pat = re.compile(re.escape(mod_id) + r":function\([a-z],?[a-z]?,?[a-z]?\)\{")
    m = pat.search(body)
    if not m:
        return None
    j = m.end() - 1
    depth = 0
    k = j
    in_str = None
    esc = False
    while k < len(body):
        ch = body[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_str:
                in_str = None
        else:
            if ch in ('"', "'", "`"):
                in_str = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
        k += 1
    return body[m.start() : k + 1]


def main() -> int:
    chunk = sys.argv[1] if len(sys.argv) > 1 else "_app-9b429b5c0d4ac058.js"
    mod_id = sys.argv[2] if len(sys.argv) > 2 else "60083"
    body = open(os.path.join(PROBE, chunk), encoding="utf-8", errors="replace").read()
    src = extract_module(body, mod_id)
    if not src:
        print(f"module {mod_id} not found in {chunk}")
        return 1
    out = os.path.join(PROBE, f"module_{mod_id}.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"module {mod_id} from {chunk} ({len(src)} bytes) -> {out}")
    print(src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
