"""Dump the module that defines API base URLs from the _app chunk."""
import os
import re
import sys

PROBE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tmp", "probe")
APP_CHUNK = os.path.join(PROBE, "_app-9b429b5c0d4ac058.js")


def module_at(body: str, pos: int) -> tuple[str, str]:
    m = re.search(r"(\d+):function\(e,t,n\)\{", body[:pos])
    assert m, "module start not found"
    start = m.start()
    j = body.index("{", m.end())
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
            if ch in ('"', "'"):
                in_str = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
        k += 1
    return m.group(1), body[start : k + 1]


def main() -> int:
    body = open(APP_CHUNK, encoding="utf-8", errors="replace").read()
    pos = body.find("sitern/api/")
    mod_id, src = module_at(body, pos)
    out = os.path.join(PROBE, "api_config_module.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"module {mod_id} -> {out} ({len(src)} bytes)")
    # print all exported names (n.d(t,{name:...}) pattern)
    for name in re.findall(r"n\.d\(t,\{([^}]+)\}\)", src):
        print("  exports:", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
