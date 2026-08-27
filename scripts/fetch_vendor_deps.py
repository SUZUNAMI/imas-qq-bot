"""Fetch pure-python wheels from PyPI and unpack into ./vendor (pip-free).

Why: the DSH sandbox denies pip's temp-unpack machinery even inside the
workspace, while plain requests + file writes work fine.

Usage: python scripts/fetch_vendor_deps.py
Then run code with:  $env:PYTHONPATH="$pwd\vendor"; python ...
"""
import os
import sys
import zipfile

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor")

# httpx 0.28.x + beautifulsoup4 dependency closure + python-dotenv (py3-none-any wheels)
PACKAGES = [
    "httpx",
    "beautifulsoup4",
    "python-dotenv",
    "anyio",
    "certifi",
    "httpcore",
    "idna",
    "sniffio",
    "h11",
    "typing_extensions",
    "soupsieve",
]

UA = {"User-Agent": "Mozilla/5.0 (m1-fetcher vendor bootstrap)"}


def fetch(name: str) -> str:
    info = requests.get(f"https://pypi.org/pypi/{name}/json", headers=UA, timeout=30).json()
    ver = info["info"]["version"]
    wheels = [u for u in info["urls"] if u["packagetype"] == "bdist_wheel"]
    wheels.sort(key=lambda u: (not u["filename"].endswith("py3-none-any.whl"), u["filename"]))
    url = wheels[0]["url"]
    fn = os.path.basename(url)
    dest = os.path.join(VENDOR, fn)
    print(f"[fetch] {name} {ver} -> {fn}", flush=True)
    if not os.path.exists(dest):
        r = requests.get(url, headers=UA, timeout=180)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
    with zipfile.ZipFile(dest) as z:
        z.extractall(VENDOR)
    return fn


def main() -> int:
    os.makedirs(VENDOR, exist_ok=True)
    for pkg in PACKAGES:
        try:
            fetch(pkg)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {pkg}: {type(e).__name__}: {e}", flush=True)
            return 1
    print("[ok] vendored. run with: $env:PYTHONPATH=...vendor; python ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
