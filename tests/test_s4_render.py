"""S4 单测：图片渲染（songbot.s4_render）.

约定（docs/S1-S7-taskplan.md §0.4 / §S4）：
- 纯函数（模板/分页/裁边/品牌色）全离线；
- 渲染用例用 mock Setlist 断言 PNG 生成 + 尺寸 + 分页张数，浏览器（Edge/playwright）
  不可用时自动 skip（验收要求人工目检日文，自动化只断言渲染不抛异常、输出非空）。
- S10（2026-08-27）：新增 ``build_list_html`` / ``render_list`` / ``render_html_pages``
  用例（序号/footer/分页/空行），列表类与 setlist 共用共享渲染管线。

运行（本机无 pytest，pip 被拦截，用标准库 unittest）：
    python -m unittest tests.test_s4_render -v
"""

import os
import re
import sys
import tempfile
import unittest
from unittest import mock

# 路径引导：仓库根加入 sys.path，使 `import songbot` 可用
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from songbot import s4_render  # noqa: E402
from songbot.models_song import Setlist, Track  # noqa: E402
from songbot.s4_render import (  # noqa: E402
    DEFAULT_BRAND_COLOR,
    MAX_PAGE_HEIGHT,
    build_html,
    build_list_html,
    render_html_pages,
    render_list,
    render_setlist,
    _brand_color,
    _chunk_tracks,
    _content_hash,
    _crop_white,
    _edge_path,
)


def _make_setlist(n: int = 3, title: str = "テスト公演 2026") -> Setlist:
    tracks = []
    for i in range(n):
        tracks.append(
            Track(
                no=i + 1,
                title=f"楽曲{i + 1} <t&>",
                brand="ミリオンライブ！" if i % 2 == 0 else None,
                performers=["舞浜歩", "菊地真"] if i % 2 == 0 else ["全員"],
                link=None,
            )
        )
    return Setlist(
        title=title,
        date_venue="2026/07/24(金) 開場17:00 / 開演18:00 某所",
        performers=["アイドルA", "アイドルB"],
        tracks=tracks,
        url="http://imas-db.jp/song/event/dummy.html",
    )


def _browser_available() -> bool:
    return s4_render._PLAYWRIGHT_OK and _edge_path() is not None


class BuildHtmlTest(unittest.TestCase):
    """模板组装（纯函数，离线）。"""

    def test_structure(self):
        html = build_html(_make_setlist())
        for needle in ("No.", "楽曲", "演者", "テスト公演 2026", "2026/07/24(金)", "アイドルA", "舞浜歩"):
            self.assertIn(needle, html, f"缺少 {needle!r}")

    def test_escape(self):
        html = build_html(_make_setlist())
        self.assertIn("&lt;t&amp;&gt;", html)     # 歌名里的 < > & 被转义
        self.assertNotIn("<t&>", html)

    def test_badge_present_with_brand(self):
        html = build_html(_make_setlist())
        self.assertIn('class="badge"', html)
        self.assertIn("ミリオンライブ！", html)

    def test_empty_tracks(self):
        sl = _make_setlist(0)
        html = build_html(sl)
        self.assertIn("<thead>", html)
        self.assertIn("<tbody></tbody>", html)
        self.assertIn("テスト公演 2026", html)  # 标题仍渲染，无曲目行

    def test_empty_title_and_date(self):
        sl = Setlist(title="", date_venue="", performers=[], tracks=[], url="")
        html = build_html(sl)
        self.assertIn("セットリスト", html)
        self.assertNotIn("class=\"meta\"", html)
        self.assertNotIn("出演:", html)


class BrandColorTest(unittest.TestCase):
    """徽章色映射（纯函数）。"""

    def test_known(self):
        self.assertEqual(_brand_color("ミリオンライブ！"), "#ffc30b")
        self.assertEqual(_brand_color("765PRO ALLSTARS"), "#f34f6d")
        self.assertEqual(_brand_color("学園アイドルマスター"), "#f39800")

    def test_unknown_and_none(self):
        self.assertEqual(_brand_color("謎のブランド"), DEFAULT_BRAND_COLOR)
        self.assertEqual(_brand_color(None), DEFAULT_BRAND_COLOR)


class ChunkTracksTest(unittest.TestCase):
    """分页（纯函数）。"""

    def test_no_pagination_when_short(self):
        tracks = _make_setlist(10).tracks
        chunks = _chunk_tracks(tracks, measured_height=MAX_PAGE_HEIGHT - 1)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0]), 10)

    def test_paginate_long(self):
        tracks = _make_setlist(50).tracks
        chunks = _chunk_tracks(tracks, measured_height=6000)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(sum(len(c) for c in chunks), 50)
        # 每页行数一致（除最后一页可能少）
        sizes = {len(c) for c in chunks}
        self.assertLessEqual(len(sizes), 2)

    def test_boundary_exact(self):
        tracks = _make_setlist(20).tracks
        chunks = _chunk_tracks(tracks, measured_height=MAX_PAGE_HEIGHT)
        self.assertEqual(len(chunks), 1)

    def test_empty(self):
        self.assertEqual(_chunk_tracks([], measured_height=5000), [[]])

    def test_extreme(self):
        # 超长表不产生空页
        tracks = _make_setlist(200).tracks
        chunks = _chunk_tracks(tracks, measured_height=10**7)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertTrue(c)


class CropWhiteTest(unittest.TestCase):
    """Pillow 裁白边（纯函数；Pillow 缺失时 skip）。"""

    def setUp(self):
        if s4_render.Image is None:  # pragma: no cover
            self.skipTest("Pillow 不可用")

    def test_crop(self):
        from PIL import Image as PILImage

        img = PILImage.new("RGB", (400, 300), "white")
        for x in range(150, 250):
            for y in range(100, 200):
                img.putpixel((x, y), (30, 30, 30))
        cropped = _crop_white(img)
        self.assertLess(cropped.width, 400)
        self.assertLess(cropped.height, 300)
        self.assertGreaterEqual(cropped.width, 100)
        self.assertGreaterEqual(cropped.height, 100)

    def test_all_white_unchanged(self):
        from PIL import Image as PILImage

        img = PILImage.new("RGB", (100, 80), "white")
        self.assertIs(_crop_white(img), img)


@unittest.skipUnless(_browser_available(), "Edge/playwright 不可用，跳过渲染用例")
class RenderSetlistTest(unittest.TestCase):
    """真实渲染（浏览器）。"""

    def _render(self, setlist, tmp):
        return render_setlist(setlist, out_dir=tmp)

    def test_small_single_png(self):
        with tempfile.TemporaryDirectory() as td:
            paths = render_setlist(_make_setlist(5), out_dir=td)
            self.assertEqual(len(paths), 1)
            self.assertTrue(paths[0].exists())
            self.assertGreater(paths[0].stat().st_size, 0)
            if s4_render.Image is not None:
                from PIL import Image as PILImage

                with PILImage.open(paths[0]) as img:
                    self.assertGreater(img.width, 0)
                    self.assertGreater(img.height, 0)
                    # 内容非空白：有非白像素
                    gray = img.convert("L")
                    nonwhite = sum(1 for px in gray.getdata() if px < 240)
                    self.assertGreater(nonwhite, 200)

    def test_long_table_multiple_pages(self):
        # 120 行单行曲目 ≈ 120×32px + 头部 200px > 3000px 阈值，必分页
        with tempfile.TemporaryDirectory() as td:
            paths = render_setlist(_make_setlist(120), out_dir=td)
            self.assertGreater(len(paths), 1)
            for p in paths:
                self.assertGreater(p.stat().st_size, 0)

    def test_empty_tracks_still_renders(self):
        with tempfile.TemporaryDirectory() as td:
            paths = render_setlist(_make_setlist(0), out_dir=td)
            self.assertEqual(len(paths), 1)
            self.assertGreater(paths[0].stat().st_size, 0)

    def test_out_dir_created(self):
        import tempfile as _tf

        with _tf.TemporaryDirectory() as td:
            sub = os.path.join(td, "a", "b")
            paths = render_setlist(_make_setlist(3), out_dir=sub)
            self.assertTrue(os.path.isdir(sub))
            self.assertTrue(paths[0].exists())


class BuildListHtmlTest(unittest.TestCase):
    """S10 列表模板组装（纯函数，离线）。"""

    def _rows(self, n: int = 3) -> list[tuple[str, str]]:
        return [(f"イベント{i}", f"2026/0{i}/01") for i in range(1, n + 1)]

    def test_structure(self):
        html = build_list_html("2026年7月 的 LIVE（共 2 场）",
                               [("IDOL WORLD SUPER FESTIVAL 2026", "多日：DAY1(2026/07/24(金))"),
                                ("DERE of the DEAD", "2026/07/04(土)・05(日)")])
        for needle in ("2026年7月 的 LIVE", "IDOL WORLD SUPER FESTIVAL 2026",
                       "DERE of the DEAD", "多日：DAY1", "回复序号"):
            self.assertIn(needle, html, f"缺少 {needle!r}")
        self.assertEqual(html.count('class="list-row"'), 2)

    def test_numbering_auto(self):
        html = build_list_html("标题", [("A", "a"), ("B", "b"), ("C", "c")])
        for n in ("1.", "2.", "3."):
            self.assertIn(n, html)
        self.assertNotIn("0.", html)

    def test_escape(self):
        html = build_list_html("标<题> & 更多", [("行<&>", "副<&>")])
        self.assertIn("&lt;", html)
        self.assertNotIn("行<&>", html)

    def test_empty_rows(self):
        html = build_list_html("空列表", [])
        self.assertIn("空列表", html)
        self.assertNotIn('class="list-row"', html)   # CSS 里的 .list-row 规则不算行
        self.assertIn("回复序号", html)          # footer 仍渲染

    def test_custom_hint(self):
        html = build_list_html("标题", [("A", "")], hint="回复序号或 LIVE 名")
        self.assertIn("回复序号或 LIVE 名", html)
        self.assertNotIn(">回复序号<", html)

    def test_no_sub_omits_span(self):
        html = build_list_html("标题", [("A", "")])
        self.assertNotIn("class=\"sub\"", html)


class RenderListTest(unittest.TestCase):
    """S10 列表渲染（纯分页逻辑离线；真实浏览器渲染可用时跑 PNG 用例）。"""

    def test_empty_rows_returns_empty(self):
        self.assertEqual(render_list("空", []), [])

    def test_paginate_long_rows(self):
        """分页（显式传 measured_height，离线确定性；量高路径由浏览器用例覆盖）。"""
        rows = [(f"行{i}", f"2026/0{i % 9 + 1}/01") for i in range(1, 300)]
        pages = s4_render._build_list_pages("长列表", rows, hint="回复序号", measured_height=5000)
        html_pages, heights = pages
        self.assertGreater(len(html_pages), 1)
        self.assertEqual(len(html_pages), len(heights))
        total = sum(html.count('class="list-row"') for html in html_pages)
        self.assertEqual(total, 299)
        # 每页都不超过估算阈值（估算行高 * 行数 + 头部）
        for html, h in zip(html_pages, heights):
            n = html.count('class="list-row"')
            self.assertLessEqual(h, s4_render.HEADER_HEIGHT_EST
                                 + (n + 1) * s4_render.ROW_HEIGHT_EST)

    def test_measure_fallback_estimates_when_unavailable(self):
        """量高不可用时（playwright 失败）回退估算行高（不抛异常，仍分页出页）。"""
        rows = [(f"行{i}", "") for i in range(1, 30)]
        with mock.patch.object(s4_render, "_measure_html_height", return_value=None):
            pages, heights = s4_render._build_list_pages("回退列表", rows, hint="回复序号")
        self.assertEqual(len(pages), 1)                  # 29 行估算 < 阈值 -> 单页
        self.assertEqual(heights[0], s4_render.HEADER_HEIGHT_EST + 29 * s4_render.ROW_HEIGHT_EST)

    def test_content_hash_differs(self):
        self.assertNotEqual(_content_hash(["a", "b"]), _content_hash(["a", "c"]))
        self.assertEqual(_content_hash(["x", "y"]), _content_hash(["x", "y"]))
        self.assertEqual(len(_content_hash(["x"])), 6)

    def test_render_html_pages_empty(self):
        """共享管线：空页面列表直接返回 []（不启动浏览器）。"""
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(render_html_pages([], td), [])


@unittest.skipUnless(_browser_available(), "Edge/playwright 不可用，跳过渲染用例")
class RenderListBrowserTest(unittest.TestCase):
    """S10 真实渲染：列表 PNG 非空、长列表分页、文件名带标题 slug + 内容哈希。"""

    def test_small_list_single_png(self):
        with tempfile.TemporaryDirectory() as td:
            paths = render_list("候補リスト", [("LIVE A", "2026/07/24(金)"),
                                               ("LIVE B", "2026/07/25(土)")], out_dir=td)
            self.assertEqual(len(paths), 1)
            self.assertTrue(paths[0].exists())
            self.assertGreater(paths[0].stat().st_size, 0)
            self.assertRegex(paths[0].name, r"^候補リスト_.+_01\.png$")  # 标题 slug + 内容哈希

    def test_long_list_multiple_pages(self):
        rows = [(f"LIVE {i}", f"2026/0{i % 9 + 1}/01") for i in range(1, 220)]
        with tempfile.TemporaryDirectory() as td:
            paths = render_list("長いリスト", rows, out_dir=td)
            self.assertGreater(len(paths), 1)
            for p in paths:
                self.assertGreater(p.stat().st_size, 0)

    def test_out_dir_created(self):
        with tempfile.TemporaryDirectory() as td:
            sub = os.path.join(td, "x", "y")
            paths = render_list("テスト", [("A", "")], out_dir=sub)
            self.assertTrue(os.path.isdir(sub))
            self.assertTrue(paths[0].exists())

    def test_png_nonblank(self):
        with tempfile.TemporaryDirectory() as td:
            paths = render_list("リスト", [(f"行{i}", "") for i in range(1, 30)], out_dir=td)
            if s4_render.Image is not None:
                from PIL import Image as PILImage

                with PILImage.open(paths[0]) as img:
                    gray = img.convert("L")
                    nonwhite = sum(1 for px in gray.getdata() if px < 240)
                    self.assertGreater(nonwhite, 200)


if __name__ == "__main__":
    unittest.main()
