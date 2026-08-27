"""M2 acceptance checks: parse 3 real detail URLs -> NewsDetail 与页面一致 + 图片可访问 + 异常明确。

运行：python scripts/acceptance_m2.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "vendor"))

import httpx  # noqa: E402

import m1_fetcher  # noqa: E402
import m2_parser  # noqa: E402
from models import NewsItem  # noqa: E402


def check_detail(item: NewsItem, client: httpx.Client) -> None:
    """解析一条真实详情，按验收标准逐项核对。"""
    detail = m2_parser.parse_detail(item)

    # 验收 1：标题/日期与页面一致（以 __NEXT_DATA__ 的 title/startdate 为准）
    assert detail.title == item.title, f"title 不一致: {detail.title!r} vs {item.title!r}"
    assert detail.date == item.date, f"date 不一致: {detail.date!r} vs {item.date!r}"
    assert detail.id == item.id
    assert detail.url == item.url
    print(f"[{item.id}] title/date OK: {detail.title[:40]}… ({detail.date})")

    # 验收 2：body_text 纯文本、无 HTML 标签、段落间 \n\n（非空正文时）
    assert "<" not in detail.body_text and ">" not in detail.body_text, "body 含 HTML 标签"
    if detail.body_text:
        assert "\n\n" in detail.body_text, "段落未用 \\n\\n 分隔"
        print(f"[{item.id}] body_text OK: {len(detail.body_text)} chars, 首段={detail.body_text.split(chr(10)+chr(10))[0][:40]!r}")
    else:
        print(f"[{item.id}] body_text 为空（纯图新闻，允许）")

    # 验收 3：images 每个 URL 可直接访问（CMS Image/get 形态）
    for u in detail.images:
        r = client.get(u)
        ctype = r.headers.get("content-type", "")
        assert r.status_code == 200 and ctype.startswith("image/"), f"图片不可访问: {r.status_code} {ctype} {u}"
        print(f"[{item.id}] image OK {r.status_code} {ctype} {len(r.content)}B <- {u[:100]}")
    print(f"[{item.id}] images OK: {len(detail.images)} 张（上限 {m2_parser.MAX_IMAGES}）")


def check_error_handling() -> None:
    """验收 4：详情页结构异常抛明确错误（断网/无效域名）。"""
    bad = NewsItem(id="x", url="https://nonexistent.invalid.example/news/x", title="", date="")
    try:
        try:
            m2_parser.parse_detail(bad)
            raise AssertionError("expected ParseError, got none")
        except m2_parser.ParseError as exc:
            msg = str(exc)
            assert "重试" in msg or "失败" in msg, msg
            print(f"[error] ParseError raised as expected: {msg[:120]}")
    finally:
        pass


def main() -> int:
    items = m1_fetcher.fetch_news_list(limit=5)
    assert len(items) >= 3, f"列表不足 3 条: {len(items)}"
    with httpx.Client(timeout=25, headers=m2_parser.DEFAULT_HEADERS, follow_redirects=True) as client:
        for item in items[:3]:
            check_detail(item, client)
    check_error_handling()
    print("[ALL PASS]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
