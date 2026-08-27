"""M5 acceptance checks: 消息组装验收（纯函数，无网络）。

用法：
    python scripts/acceptance_m5.py
覆盖 docs/modules/M5-formatter.md §8 验收 1–5：
  1. 样例输入 → PushMessage 结构符合 §3；
  2. 完整文本含日期/原文标题/标题译文/正文译文/原文链接；
  3. 超长正文切多段且每段 ≤ 3500、切分点在段落边界；
  4. images 正确透传（≤4 张）；
  5. 空正文也能生成合法消息。
"""
import os
import sys

# Windows 控制台默认 GBK 无法编码 emoji（🔗 等），统一走 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import m5_formatter as m5  # noqa: E402
from models import NewsDetail, PushMessage, TranslationResult  # noqa: E402

# 样例（与 M4 验收同源的假想新闻）
SAMPLE_DETAIL = NewsDetail(
    id="01_17821",
    url="https://idolmaster-official.jp/news/01_17821",
    title="【イベント】アイドルマスター 新情報発表会 開催決定！",
    date="2026-08-26",
    body_text=(
        "『アイドルマスター シャイニーカラーズ』より、新情報発表会の開催が決定いたしました。\n\n"
        "2026年9月12日（土）に実施予定です。詳細は後日お知らせいたします。"
    ),
    images=["https://example.com/a.jpg", "https://example.com/b.jpg"],
)

SAMPLE_TR = TranslationResult(
    title_zh="【活动】偶像大师 新情报发布会 举办决定！",
    body_zh="来自『偶像大师 闪耀色彩』的新情报发布会举办决定。\n\n预定于 2026 年 9 月 12 日（周六）举行。详情日后公布。",
)


def check_structure_and_template() -> None:
    """验收 1+2：结构符合 §3，完整文本含全部要素（原文 + 译文均拼接）。"""
    msg = m5.format_message(SAMPLE_DETAIL, SAMPLE_TR, ["123456789", "987654321"])
    assert isinstance(msg, PushMessage), type(msg)
    assert msg.group_ids == ["123456789", "987654321"], msg.group_ids
    assert msg.link == SAMPLE_DETAIL.url, msg.link
    assert msg.images == SAMPLE_DETAIL.images, msg.images
    text = "\n".join(msg.segments)
    for needle in (
        "【NEWS】2026-08-26",
        SAMPLE_DETAIL.title,
        SAMPLE_DETAIL.body_text,   # 原文正文
        "——— 中文翻译 ———",
        SAMPLE_TR.title_zh,
        SAMPLE_TR.body_zh,
        f"🔗 原文：{SAMPLE_DETAIL.url}",
    ):
        assert needle in text, f"缺少: {needle!r}"
    # 原文在分隔线上方、译文在下方
    assert text.index(SAMPLE_DETAIL.body_text) < text.index("——— 中文翻译 ———") < text.index(SAMPLE_TR.body_zh)
    print(f"[structure] OK: 1 段, len={len(text)} 字符, images={len(msg.images)} 张（原文+译文均拼接）")


def check_long_split() -> None:
    """验收 3：超长正文切多段，每段 ≤ 3500，切分点在段落边界。"""
    body = "甲" * 2000 + "\n\n" + "乙" * 2000
    detail = NewsDetail(id="x", url="https://example.com/01_999", title="t", date="2026-08-26", body_text=body, images=[])
    tr = TranslationResult(title_zh="T", body_zh=body)
    msg = m5.format_message(detail, tr, [])
    assert len(msg.segments) > 1, "超长正文未分片"
    for seg in msg.segments:
        assert len(seg) <= m5.DEFAULT_MAX_LEN, f"段超限: {len(seg)}"
    for seg in msg.segments:
        assert not ("甲" in seg and "乙" in seg), "切分点不在段落边界（甲/乙混段）"
    assert "🔗 原文：https://example.com/01_999" in msg.segments[-1]
    print(f"[split] OK: {len(msg.segments)} 段，各段 ≤ {m5.DEFAULT_MAX_LEN}，段落边界切分，链接在末段")


def check_images_passthrough() -> None:
    """验收 4：images 透传且 ≤4 张。"""
    imgs = [f"https://example.com/{i}.jpg" for i in range(7)]
    detail = NewsDetail(id="x", url="u", title="t", date="2026-08-26", body_text="b", images=imgs)
    msg = m5.format_message(detail, SAMPLE_TR, [])
    assert msg.images == imgs[:4], msg.images
    empty = NewsDetail(id="x", url="u", title="t", date="2026-08-26", body_text="b", images=[])
    msg0 = m5.format_message(empty, SAMPLE_TR, [])
    assert msg0.images == [], msg0.images
    print(f"[images] OK: 7 张截断为 {len(msg.images)}；空列表透传 []")


def check_empty_body() -> None:
    """验收 5：空正文也能生成合法消息。"""
    tr = TranslationResult(title_zh="只有标题译文", body_zh="")
    msg = m5.format_message(SAMPLE_DETAIL, tr, [])
    text = "\n".join(msg.segments)
    assert "只有标题译文" in text
    assert f"🔗 原文：{SAMPLE_DETAIL.url}" in text
    print("[empty-body] OK: 标题译文 + 链接，正文部分为空，消息合法")


def main() -> int:
    check_structure_and_template()
    check_long_split()
    check_images_passthrough()
    check_empty_body()
    print("[ALL PASS]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
