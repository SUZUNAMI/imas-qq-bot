"""M2 详情解析 — 纯逻辑单测（不访问网络，覆盖正文转换/提取/映射/回退/异常路径）。

运行：python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from models import NewsItem  # noqa: E402
from m2_parser import (  # noqa: E402
    ParseError,
    _build_detail,
    _extract_images,
    _extract_next_data,
    _fallback_detail,
    _fmt_date,
    _get_data_node,
    _html_to_text,
    _image_get_url,
    parse_detail,
)

# ---------------------------------------------------------------------------
# 固定样本（取自 2026-08-26 真实详情页 01_19692，裁剪）
# ---------------------------------------------------------------------------
CONTENT_HTML = (
    '<div class="row js-setGallery rel-base c-gallery" baserel="h1p0d2irvcc">'
    '<div class="col-sm-12 ui-resizable"><div data-type="component-text">'
    '<div><div class="c-txt">プロデューサーの皆さん、こんにちは！<br><br>'
    "30MSシリーズより、オプションヘアスタイル＆フェイスパーツセット(七草にちか/七草はづき)が2027年1月に発売予定✨<br><br>"
    '▼商品ページはこちら！<br><a href="https://bandai-hobby.net/item/01_7363/" target="_blank">'
    "https://bandai-hobby.net/item/01_7363/</a><br>&nbsp;</div></div></div>"
    '<div data-type="component-photo"><div class="photo-panel">'
    '<img src="/idolmaster/jp/article/019/2026/08/41CuKP9O0hPS9zRqWpWpNFD8uDmbPuCj.jpeg" alt=""></div></div>'
    '<div data-type="component-text"><div class="text-area brand-05"><h5 style="text-align: center;">商品情報</h5></div>'
    '<div><div class="c-txt"><strong>「アイドルマスター シャイニーカラーズ」</strong>より、'
    "「七草にちか」「七草はづき」のヘアスタイルパーツがセットになって商品化！<br><br>"
    "■衣装のデザインに合わせて、パーツの着脱で再現可能。<br>&nbsp;</div></div></div>"
    "</div></div>"
)

DATA_NODE = {
    "path": "01_19692",
    "title": "【シャニマス】「30MS」シリーズよりオプションヘアスタイル＆フェイスパーツセット(七草にちか/七草はづき)が2027年1月に発売予定！",
    "startdate": 1787731200,          # 2026-08-26 17:00 JST
    "dspdate": "2026/08/26 17:00",
    "content": CONTENT_HTML,
    "use_image": [
        {"path": "/idolmaster/jp/article/019/2026/08", "filename": "41CuKP9O0hPS9zRqWpWpNFD8uDmbPuCj.jpeg"},
        {"path": "/idolmaster/jp/article/019/2026/08", "filename": "1aHDH7v4dCcxrskzW3rR98kRb4QbQhGX.jpeg"},
    ],
}

NEXT_DATA_HTML = (
    '<html><head><title>x</title></head><body>'
    '<script id="__NEXT_DATA__" type="application/json">'
    + '{"props":{"pageProps":{"data":' + 'PLACEHOLDER' + '}},"page":"/news/[id]"}'
    + "</script></body></html>"
).replace("PLACEHOLDER", '{"path": "01_19692", "title": "T", "startdate": 1787731200, "content": ""}')

ITEM = NewsItem(
    id="01_19692",
    url="https://idolmaster-official.jp/news/01_19692",
    title="【シャニマス】リストタイトル",
    date="2026-08-26",
)


class TestHtmlToText(unittest.TestCase):
    def test_paragraphs_and_tag_stripping(self) -> None:
        text = _html_to_text(CONTENT_HTML)
        self.assertNotIn("<", text)
        self.assertNotIn(">", text)
        # 首段「こんにちは！」独立成段，随后是段落边界
        self.assertIn("プロデューサーの皆さん、こんにちは！\n\n30MSシリーズより", text)
        # 块级标题「商品情報」单独成段
        self.assertIn("商品情報\n\n", text)
        # 链接文本保留（原文链接显示文本即 URL）
        self.assertIn("https://bandai-hobby.net/item/01_7363/", text)

    def test_empty(self) -> None:
        self.assertEqual(_html_to_text(""), "")
        self.assertEqual(_html_to_text("<div></div>"), "")

    def test_no_double_blank_lines(self) -> None:
        text = _html_to_text(CONTENT_HTML)
        self.assertNotIn("\n\n\n", text)


class TestExtractNextData(unittest.TestCase):
    def test_normal(self) -> None:
        data = _extract_next_data(NEXT_DATA_HTML)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["page"], "/news/[id]")

    def test_missing(self) -> None:
        self.assertIsNone(_extract_next_data("<html><body>no next data</body></html>"))

    def test_broken_json(self) -> None:
        html = '<script id="__NEXT_DATA__" type="application/json">{oops</script>'
        self.assertIsNone(_extract_next_data(html))

    def test_non_dict(self) -> None:
        html = '<script id="__NEXT_DATA__" type="application/json">[1,2]</script>'
        self.assertIsNone(_extract_next_data(html))


class TestGetDataNode(unittest.TestCase):
    def test_normal(self) -> None:
        nd = _extract_next_data(NEXT_DATA_HTML)
        node = _get_data_node(nd)
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node["path"], "01_19692")

    def test_missing_path(self) -> None:
        self.assertIsNone(_get_data_node({"props": {}}))
        self.assertIsNone(_get_data_node(None))
        self.assertIsNone(_get_data_node({"props": {"pageProps": 123}}))


class TestFmtDate(unittest.TestCase):
    def test_startdate(self) -> None:
        self.assertEqual(_fmt_date(1787731200, "2026/08/26 17:00"), "2026-08-26")

    def test_dspdate_fallback(self) -> None:
        self.assertEqual(_fmt_date(None, "2026/08/26 17:00"), "2026-08-26")

    def test_none(self) -> None:
        self.assertEqual(_fmt_date(None, None), "")
        self.assertEqual(_fmt_date(None, "bad date"), "")


class TestImageUrl(unittest.TestCase):
    def test_relative_path(self) -> None:
        u = _image_get_url("/idolmaster/jp/article/x.jpeg")
        self.assertTrue(u.startswith("https://cmsapi-frontend.idolmaster-official.jp/sitern/api/idolmaster/Image/get?path="))
        self.assertIn("/idolmaster/jp/article/x.jpeg", u)

    def test_site_absolute_normalized(self) -> None:
        u = _image_get_url("https://idolmaster-official.jp/idolmaster/jp/article/x.jpeg")
        self.assertIn("Image/get?path=/idolmaster/jp/article/x.jpeg", u)

    def test_external_url_kept(self) -> None:
        self.assertEqual(_image_get_url("https://img.example.com/a.jpg"), "https://img.example.com/a.jpg")

    def test_empty(self) -> None:
        self.assertEqual(_image_get_url("  "), "")


class TestExtractImages(unittest.TestCase):
    def test_content_src_priority_dedupe_cap(self) -> None:
        html = (
            '<img src="/idolmaster/a.jpg"><img src="/idolmaster/b.jpg">'
            '<img src="/idolmaster/a.jpg">'  # 重复
            '<img src="/idolmaster/c.jpg"><img src="/idolmaster/d.jpg"><img src="/idolmaster/e.jpg">'
        )
        urls = _extract_images(html, None)
        self.assertEqual(len(urls), 4)  # 上限 4
        self.assertIn("path=/idolmaster/a.jpg", urls[0])
        self.assertIn("path=/idolmaster/b.jpg", urls[1])
        self.assertNotIn("path=/idolmaster/e.jpg", " ".join(urls))

    def test_data_src_lazyload(self) -> None:
        html = '<img data-src="/idolmaster/lazy.jpg" src="">'
        urls = _extract_images(html, None)
        self.assertEqual(len(urls), 1)
        self.assertIn("path=/idolmaster/lazy.jpg", urls[0])

    def test_use_image_fallback(self) -> None:
        urls = _extract_images("", [{"path": "/idolmaster/019/2026/08", "filename": "a.jpeg"}])
        self.assertEqual(len(urls), 1)
        self.assertIn("path=/idolmaster/019/2026/08/a.jpeg", urls[0])

    def test_content_beats_use_image(self) -> None:
        # content 有图时不用 use_image（use_image 可能含正文未展示的图）
        urls = _extract_images('<img src="/idolmaster/a.jpg">', [{"path": "/idolmaster/z", "filename": "z.jpeg"}])
        self.assertEqual(len(urls), 1)
        self.assertIn("a.jpg", urls[0])


class TestBuildDetail(unittest.TestCase):
    def test_main_path(self) -> None:
        detail = _build_detail(ITEM, DATA_NODE, "")
        self.assertEqual(detail.id, "01_19692")
        self.assertEqual(detail.url, ITEM.url)
        self.assertEqual(detail.title, DATA_NODE["title"])
        self.assertEqual(detail.date, "2026-08-26")
        self.assertNotIn("<", detail.body_text)
        self.assertIn("\n\n", detail.body_text)
        # content 里只有 1 个 <img>（fixture 裁剪），content 优先于 use_image
        self.assertEqual(len(detail.images), 1)
        self.assertTrue(all(u.startswith("https://cmsapi-frontend.") for u in detail.images))

    def test_empty_body_allowed(self) -> None:
        node = dict(DATA_NODE, content="", use_image=[])
        detail = _build_detail(ITEM, node, "")
        self.assertEqual(detail.body_text, "")
        self.assertEqual(detail.images, [])
        self.assertEqual(detail.title, DATA_NODE["title"])  # title 仍正确

    def test_title_missing_falls_back_to_item_title(self) -> None:
        node = dict(DATA_NODE, title="")
        detail = _build_detail(ITEM, node, "<html><body>无 og 无 title</body></html>")
        self.assertEqual(detail.title, ITEM.title)

    def test_title_missing_everywhere_raises(self) -> None:
        node = dict(DATA_NODE, title="")
        empty_item = NewsItem(id="x", url="https://example.com/x", title="", date="")
        with self.assertRaises(ParseError):
            _build_detail(empty_item, node, "<html><body>no title</body></html>")


class TestFallbackDetail(unittest.TestCase):
    def test_without_next_data(self) -> None:
        html = (
            '<html><head><meta property="og:title" content="【イベント】某ライブ開催決定！">'
            "<title>【イベント】某ライブ開催決定！ | 【公式】アイドルマスター ポータル</title></head>"
            f'<body><div class="c-gallery">{CONTENT_HTML}</div></body></html>'
        )
        detail = _fallback_detail(ITEM, html)
        self.assertEqual(detail.title, "【イベント】某ライブ開催決定！")
        self.assertEqual(detail.date, ITEM.date)
        self.assertNotIn("<", detail.body_text)
        self.assertIn("こんにちは！", detail.body_text)

    def test_no_og_title_uses_html_title(self) -> None:
        html = "<html><head><title>某タイトル | サイト</title></head><body></body></html>"
        detail = _fallback_detail(ITEM, html)
        self.assertEqual(detail.title, "某タイトル")

    def test_no_title_at_all_uses_item_title(self) -> None:
        detail = _fallback_detail(ITEM, "<html><body></body></html>")
        self.assertEqual(detail.title, ITEM.title)


class TestEntry(unittest.TestCase):
    def test_empty_url_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_detail(NewsItem(id="x", url="", title="t", date="2026-01-01"))

    @patch("m2_parser._fetch_html", return_value=NEXT_DATA_HTML)
    def test_main_path_with_mocked_network(self, _mock: object) -> None:
        detail = parse_detail(ITEM)
        self.assertEqual(detail.id, "01_19692")
        self.assertEqual(detail.title, "T")
        self.assertEqual(detail.date, "2026-08-26")

    @patch("m2_parser._fetch_html", return_value="<html><body>no next data</body></html>")
    def test_fallback_path_with_mocked_network(self, _mock: object) -> None:
        detail = parse_detail(ITEM)
        self.assertEqual(detail.title, ITEM.title)   # fallback 用 item.title
        self.assertEqual(detail.date, ITEM.date)


if __name__ == "__main__":
    unittest.main()
