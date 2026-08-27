"""M5 消息组装 — 纯逻辑单测（纯函数，无 IO、无网络）。

运行：python -m unittest discover -s tests -v
覆盖：docs/modules/M5-formatter.md §8 验收 1–5 + 分片边界/硬切/鸭子类型/防御性。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import m5_formatter as m5  # noqa: E402
from models import NewsDetail, PushMessage, TranslationResult  # noqa: E402

SAMPLE_DETAIL = NewsDetail(
    id="01_17821",
    url="https://idolmaster-official.jp/news/01_17821",
    title="【イベント】アイドルマスター 新情報発表会 開催決定！",
    date="2026-08-26",
    body_text="第一段落のテキスト。\n\n第二段落のテキスト。",
    images=["https://example.com/a.jpg", "https://example.com/b.jpg"],
)

SAMPLE_TR = TranslationResult(
    title_zh="【活动】偶像大师 新情报发布会 举办决定！",
    body_zh="来自『偶像大师 闪耀色彩』的新情报发布会举办决定。\n\n预定于 2026 年 9 月 12 日（周六）举行。详情日后公布。",
)

SAMPLE_GROUPS = ["123456789", "987654321"]


class TestContractReExport(unittest.TestCase):
    def test_types_come_from_models(self):
        self.assertIs(m5.NewsDetail, NewsDetail)
        self.assertIs(m5.TranslationResult, TranslationResult)
        self.assertIs(m5.PushMessage, PushMessage)


class TestBasicAssembly(unittest.TestCase):
    """验收 1：给定样例输入，输出符合 §3 结构的 PushMessage。"""

    def test_structure(self):
        msg = m5.format_message(SAMPLE_DETAIL, SAMPLE_TR, SAMPLE_GROUPS)
        self.assertIsInstance(msg, PushMessage)
        self.assertEqual(msg.group_ids, SAMPLE_GROUPS)
        self.assertEqual(msg.link, SAMPLE_DETAIL.url)
        self.assertEqual(msg.images, SAMPLE_DETAIL.images)
        self.assertIsInstance(msg.segments, list)
        self.assertTrue(all(isinstance(s, str) and s for s in msg.segments))

    def test_short_text_single_segment(self):
        msg = m5.format_message(SAMPLE_DETAIL, SAMPLE_TR, SAMPLE_GROUPS)
        self.assertEqual(len(msg.segments), 1)

    def test_group_ids_are_copied(self):
        groups = ["1"]
        msg = m5.format_message(SAMPLE_DETAIL, SAMPLE_TR, groups)
        groups.append("2")
        self.assertEqual(msg.group_ids, ["1"])

    def test_images_copied(self):
        detail = NewsDetail(
            id="x", url="u", title="t", date="d", body_text="b", images=["i1"]
        )
        msg = m5.format_message(detail, SAMPLE_TR, [])
        detail.images.append("i2")
        self.assertEqual(msg.images, ["i1"])


class TestTemplate(unittest.TestCase):
    """验收 2：完整文本包含日期、原文标题、原文正文、标题译文、正文译文、原文链接。"""

    def test_all_required_parts(self):
        msg = m5.format_message(SAMPLE_DETAIL, SAMPLE_TR, SAMPLE_GROUPS)
        text = "\n".join(msg.segments)
        self.assertIn("【NEWS】2026-08-26", text)
        self.assertIn(SAMPLE_DETAIL.title, text)
        self.assertIn(SAMPLE_DETAIL.body_text, text)      # 原文正文
        self.assertIn("——— 中文翻译 ———", text)
        self.assertIn(SAMPLE_TR.title_zh, text)
        self.assertIn(SAMPLE_TR.body_zh, text)
        self.assertIn(f"🔗 原文：{SAMPLE_DETAIL.url}", text)

    def test_template_layout(self):
        msg = m5.format_message(SAMPLE_DETAIL, SAMPLE_TR, SAMPLE_GROUPS)
        text = msg.segments[0]
        # 空行分隔段落（\n\n），与规格 §4 模板一致：原文在上、译文在下
        self.assertIn(f"【NEWS】{SAMPLE_DETAIL.date}\n{SAMPLE_DETAIL.title}\n\n", text)
        self.assertIn(f"\n\n{SAMPLE_DETAIL.body_text}\n\n——— 中文翻译 ———\n", text)
        self.assertIn(f"\n\n🔗 原文：{SAMPLE_DETAIL.url}", text)

    def test_body_zh_keeps_paragraph_newlines(self):
        msg = m5.format_message(SAMPLE_DETAIL, SAMPLE_TR, SAMPLE_GROUPS)
        # 译文正文的段落换行原样保留（不挤压成一行）
        self.assertIn(SAMPLE_TR.body_zh, msg.segments[0])
        self.assertIn("\n\n", msg.segments[0])


class TestEmptyBody(unittest.TestCase):
    """验收 5：空正文也能生成合法消息（标题+译文+链接，body 部分为空）。"""

    def test_empty_body_zh(self):
        tr = TranslationResult(title_zh="只有标题译文", body_zh="")
        msg = m5.format_message(SAMPLE_DETAIL, tr, SAMPLE_GROUPS)
        text = "\n".join(msg.segments)
        self.assertIn("只有标题译文", text)
        self.assertIn(f"🔗 原文：{SAMPLE_DETAIL.url}", text)
        self.assertIn("——— 中文翻译 ———", text)
        # 译文正文为空：译文标题后直接接链接，不应出现残留空正文行
        self.assertNotIn("只有标题译文\n\n\n", text)

    def test_empty_original_title(self):
        detail = NewsDetail(
            id="x", url="https://example.com/x", title="", date="2026-08-26",
            body_text="", images=[],
        )
        msg = m5.format_message(detail, SAMPLE_TR, [])
        text = msg.segments[0]
        # 标题行为空时保留模板结构（空行仍存在），不报错
        self.assertIn("【NEWS】2026-08-26\n\n——— 中文翻译 ———", text)

    def test_fully_empty_inputs(self):
        detail = NewsDetail(id="x", url="https://example.com/x", title="", date="2026-08-26", body_text="", images=[])
        tr = TranslationResult(title_zh="", body_zh="")
        msg = m5.format_message(detail, tr, [])
        text = msg.segments[0]
        self.assertIn("【NEWS】2026-08-26", text)
        self.assertIn("🔗 原文：https://example.com/x", text)
        self.assertIn("——— 中文翻译 ———", text)


class TestSplitting(unittest.TestCase):
    """验收 3：超长正文切成多段，每段 ≤ max_len，切分点在段落边界（超限段才硬切）。"""

    def _para(self, ch: str, n: int) -> str:
        return ch * n

    def test_long_text_split_at_paragraph_boundary(self):
        # 两段各 ~2000 字符，合并超 3500 → 应在两段之间切
        body = f"{self._para('甲', 2000)}\n\n{self._para('乙', 2000)}"
        detail = NewsDetail(id="x", url="u", title="t", date="2026-08-26", body_text=body, images=[])
        tr = TranslationResult(title_zh="T", body_zh=body)
        msg = m5.format_message(detail, tr, [])
        self.assertGreater(len(msg.segments), 1)
        for seg in msg.segments:
            self.assertLessEqual(len(seg), m5.DEFAULT_MAX_LEN)
        # 切分点在段落边界：任何一段不得同时包含两段正文内容（甲/乙不混段）
        for seg in msg.segments:
            self.assertFalse("甲" in seg and "乙" in seg, "段落边界切分失效")
        # 原文与译文各含一份甲/乙（双份拼接）
        self.assertEqual("".join(msg.segments).count("甲"), 4000)
        self.assertEqual("".join(msg.segments).count("乙"), 4000)

    def test_single_paragraph_over_limit_hard_split(self):
        # 单段 6000 字符、含换行 → 内部按行边界硬切（优先 \n）
        para = "\n".join([self._para("行", 3000), self._para("行", 3000)])
        detail = NewsDetail(id="x", url="u", title="t", date="2026-08-26", body_text=para, images=[])
        tr = TranslationResult(title_zh="T", body_zh=para)
        msg = m5.format_message(detail, tr, [], max_len=3500)
        self.assertGreaterEqual(len(msg.segments), 2)
        for seg in msg.segments:
            self.assertLessEqual(len(seg), 3500)
        # 硬切发生在行边界
        text = "\n\n".join(msg.segments)
        self.assertNotIn("行" * 3000 + "\n" + "行" * 3000, text)

    def test_character_hard_split_no_newline(self):
        # 无任何换行的 8000 字符段落 → 按字符硬切
        para = self._para("字", 8000)
        detail = NewsDetail(id="x", url="u", title="t", date="2026-08-26", body_text=para, images=[])
        tr = TranslationResult(title_zh="T", body_zh=para)
        msg = m5.format_message(detail, tr, [], max_len=3500)
        for seg in msg.segments:
            self.assertLessEqual(len(seg), 3500)
        # 拼接后正文内容无丢失、无重复（原文 + 译文各 8000 字符）
        self.assertEqual("".join(msg.segments).count("字"), 16000)

    def test_custom_max_len(self):
        body = "一二三四五六七八九十" * 10  # 100 字符
        detail = NewsDetail(id="x", url="u", title="t", date="2026-08-26", body_text=body, images=[])
        tr = TranslationResult(title_zh="T", body_zh=body)
        msg = m5.format_message(detail, tr, [], max_len=30)
        for seg in msg.segments:
            self.assertLessEqual(len(seg), 30)
        self.assertGreater(len(msg.segments), 1)

    def test_below_limit_single_segment(self):
        # 整条消息（含原文+译文）不超过上限 → 保持 1 段
        body = "正" * 1500  # 原文 + 译文各 1500 → 全文约 3.0K < 3500
        detail = NewsDetail(id="x", url="u", title="t", date="2026-08-26", body_text=body, images=[])
        tr = TranslationResult(title_zh="T", body_zh=body)
        msg = m5.format_message(detail, tr, [], max_len=3500)
        self.assertEqual(len(msg.segments), 1)
        self.assertLessEqual(len(msg.segments[0]), 3500)

    def test_link_footer_survives_splitting(self):
        """超长时链接必须完整保留在最后一段。"""
        body = f"{self._para('甲', 2000)}\n\n{self._para('乙', 2000)}"
        detail = NewsDetail(id="x", url="https://example.com/01_999", title="t", date="2026-08-26", body_text=body, images=[])
        tr = TranslationResult(title_zh="T", body_zh=body)
        msg = m5.format_message(detail, tr, [])
        last = msg.segments[-1]
        self.assertIn("🔗 原文：https://example.com/01_999", last)


class TestImages(unittest.TestCase):
    """验收 4：images 正确透传（≤4 张）。"""

    def test_passthrough_up_to_four(self):
        imgs = [f"https://example.com/{i}.jpg" for i in range(4)]
        detail = NewsDetail(id="x", url="u", title="t", date="d", body_text="b", images=imgs)
        msg = m5.format_message(detail, SAMPLE_TR, [])
        self.assertEqual(msg.images, imgs)

    def test_truncated_to_four(self):
        imgs = [f"https://example.com/{i}.jpg" for i in range(7)]
        detail = NewsDetail(id="x", url="u", title="t", date="d", body_text="b", images=imgs)
        msg = m5.format_message(detail, SAMPLE_TR, [])
        self.assertEqual(len(msg.images), 4)
        self.assertEqual(msg.images, imgs[:4])

    def test_empty_images(self):
        detail = NewsDetail(id="x", url="u", title="t", date="d", body_text="b", images=[])
        msg = m5.format_message(detail, SAMPLE_TR, [])
        self.assertEqual(msg.images, [])


class TestDuckTyping(unittest.TestCase):
    """输入兼容：models 类 / 任意契约 dataclass / dict。"""

    def test_dict_input(self):
        detail = {
            "id": "x", "url": "u", "title": "t", "date": "2026-08-26",
            "body_text": "b", "images": ["i"],
        }
        tr = {"title_zh": "T", "body_zh": "B"}
        msg = m5.format_message(detail, tr, ["1"])
        self.assertIsInstance(msg, PushMessage)
        self.assertIn("T", msg.segments[0])
        self.assertIn("B", msg.segments[0])

    def test_other_dataclass_with_contract_fields(self):
        from dataclasses import dataclass, field

        @dataclass
        class OtherDetail:
            id: str
            url: str
            title: str
            date: str
            body_text: str
            images: list = field(default_factory=list)

        @dataclass
        class OtherTr:
            title_zh: str
            body_zh: str

        msg = m5.format_message(
            OtherDetail("x", "u", "t", "2026-08-26", "b", []),
            OtherTr("T", "B"),
            [],
        )
        self.assertEqual(msg.link, "u")


class TestInvalidInput(unittest.TestCase):
    def test_junk_detail_raises(self):
        with self.assertRaises(ValueError):
            m5.format_message(123, SAMPLE_TR, [])

    def test_junk_tr_raises(self):
        with self.assertRaises(ValueError):
            m5.format_message(SAMPLE_DETAIL, 456, [])

    def test_missing_field_raises(self):
        with self.assertRaises(ValueError):
            m5.format_message({"id": "x", "title": "t"}, SAMPLE_TR, [])

    def test_invalid_max_len(self):
        for bad in (0, -5, 3.5, "3500", True):
            with self.assertRaises(ValueError):
                m5.format_message(SAMPLE_DETAIL, SAMPLE_TR, [], max_len=bad)


if __name__ == "__main__":
    unittest.main()
