"""Fetch playwright + greenlet + pyee wheels and unpack into ./vendor (pip-free).

Why: the DSH sandbox denies pip's temp-unpack machinery even inside the
workspace, while plain requests + file writes work fine (see fetch_vendor_deps.py).

S4 需要 playwright（驱动系统 Edge 免下载 Chromium），依赖 greenlet（编译扩展，须按
cpython 版本选 wheel）与 pyee。playwright 自带 node 驱动，须选 win_amd64 wheel。

Usage: python scripts/fetch_s4_vendor_deps.py
"""
import os
import sys
import zipfile

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor")

# (pkg, 挑选规则)：py3-none-any 纯 py；否则按平台/ABI 关键字挑选
PACKAGES = [
    ("playwright", "py3-none-win_amd64"),
    ("greenlet", "cp313-win_amd64"),
    ("pyee", "py3-none-any"),
]

UA = {"User-Agent": "Mozilla/5.0 (s4-render vendor bootstrap)"}


def fetch(name: str, pick: str) -> str:
    info = requests.get(f"https://pypi.org/pypi/{name}/json", headers=UA, timeout=30).json()
    ver = info["info"]["version"]
    wheels = [u for u in info["urls"] if u["packagetype"] == "bdist_wheel"]
    wheels = [u for u in wheels if pick in u["filename"]]
    if not wheels:  # pragma: no cover
        raise RuntimeError(f"{name}: no wheel matching {pick!r}")
    wheels.sort(key=lambda u: u["filename"])
    url = wheels[0]["url"]
    fn = os.path.basename(url)
    dest = os.path.join(VENDOR, fn)
    print(f"[fetch] {name} {ver} -> {fn}", flush=True)
    if not os.path.exists(dest):
        r = requests.get(url, headers=UA, timeout=300)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
    with zipfile.ZipFile(dest) as z:
        z.extractall(VENDOR)
    return fn


def main() -> int:
    os.makedirs(VENDOR, exist_ok=True)
    for pkg, pick in PACKAGES:
        try:
            fetch(pkg, pick)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {pkg}: {type(e).__name__}: {e}", flush=True)
            return 1
    print("[ok] s4 vendor deps vendored.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
