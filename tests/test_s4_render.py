"""S4 单测：图片渲染（songbot.s4_render）.

约定（docs/S1-S7-taskplan.md §0.4 / §S4）：
- 纯函数（模板/分页/裁边/品牌色）全离线；
- 渲染用例用 mock Setlist 断言 PNG 生成 + 尺寸 + 分页张数，浏览器（Edge/playwright）
  不可用时自动 skip（验收要求人工目检日文，自动化只断言渲染不抛异常、输出非空）。

运行（本机无 pytest，pip 被拦截，用标准库 unittest）：
    python -m unittest tests.test_s4_render -v
"""

import os
import sys
import tempfile
import unittest

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
    render_setlist,
    _brand_color,
    _chunk_tracks,
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


if __name__ == "__main__":
    unittest.main()
