"""M1 acceptance checks: stability (two runs) + thumbnail URL + error handling."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "vendor"))

import httpx  # noqa: E402

import m1_fetcher  # noqa: E402


def check_stability() -> None:
    r1 = m1_fetcher.fetch_news_list()
    r2 = m1_fetcher.fetch_news_list()
    ids1 = [(i.id, i.date, i.title) for i in r1]
    ids2 = [(i.id, i.date, i.title) for i in r2]
    assert len(r1) == len(r2) == 20, f"len mismatch: {len(r1)} vs {len(r2)}"
    assert ids1 == ids2, "two runs differ!"
    dup = len(r1) - len({i.id for i in r1})
    assert dup == 0, f"{dup} duplicate ids"
    dates = [i.date for i in r1]
    assert dates == sorted(dates, reverse=True), "not newest-first"
    print(f"[stable] two runs identical, {len(r1)} items, newest-first OK")


def check_thumbnail() -> None:
    item = m1_fetcher.fetch_news_list(limit=1)[0]
    assert item.thumbnail, "no thumbnail"
    with httpx.Client(timeout=25, headers=m1_fetcher.DEFAULT_HEADERS, follow_redirects=True) as c:
        r = c.get(item.thumbnail)
    print(f"[thumb] {r.status_code} {r.headers.get('content-type')} {len(r.content)}B <- {item.thumbnail[:110]}")
    assert r.status_code == 200 and r.headers.get("content-type", "").startswith("image/")
    print("[thumb] OK")


def check_error_handling() -> None:
    old_base = m1_fetcher.CMS_API_BASE
    m1_fetcher.CMS_API_BASE = "https://nonexistent.invalid.example/sitern/api/"
    try:
        try:
            m1_fetcher.fetch_news_list()
            raise AssertionError("expected FetchError, got none")
        except m1_fetcher.FetchError as exc:
            msg = str(exc)
            assert "重试" in msg or "失败" in msg, msg
            print(f"[error] FetchError raised as expected: {msg[:120]}")
    finally:
        m1_fetcher.CMS_API_BASE = old_base


def main() -> int:
    check_stability()
    check_thumbnail()
    check_error_handling()
    print("[ALL PASS]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
