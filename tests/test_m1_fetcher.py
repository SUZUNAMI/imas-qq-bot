"""M1 列表抓取 — 纯逻辑单测（不访问网络，只测 API article -> NewsItem 映射）。

运行：python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from m1_fetcher import NewsItem, _article_to_item, _articles_to_items, fetch_news_list  # noqa: E402

SAMPLE = {
    "_id": "23256",
    "path": "01_19692",
    "title": "【テスト】30MS 発売予定",
    "startdate": 1787731200,          # 2026-08-26 17:00 JST
    "dspdate": "2026/08/26 17:00",
    "updated": 1787731203,
    "thumbnail": "/idolmaster/jp/article/019/2026/08/img.jpeg?_=abc123",
    "url": "https://idolmaster-official.jp/news/01_19692.html",
    "publish_status": "publish",
    "delflg": "0",
}


class TestArticleToItem(unittest.TestCase):
    def test_valid_article(self) -> None:
        item = _article_to_item(SAMPLE)
        self.assertIsInstance(item, NewsItem)
        assert item is not None
        self.assertEqual(item.id, "01_19692")
        self.assertEqual(item.url, "https://idolmaster-official.jp/news/01_19692")
        self.assertEqual(item.date, "2026-08-26")
        self.assertIn("idolmaster/Image/get?path=", item.thumbnail or "")
        self.assertNotIn("?_=", item.thumbnail or "")  # 缓存参数已剥离

    def test_deleted_and_unpublished_skipped(self) -> None:
        d = dict(SAMPLE, delflg="1")
        self.assertIsNone(_article_to_item(d))
        d = dict(SAMPLE, publish_status="draft")
        self.assertIsNone(_article_to_item(d))

    def test_missing_path_skipped(self) -> None:
        d = dict(SAMPLE, path="")
        self.assertIsNone(_article_to_item(d))

    def test_no_thumbnail(self) -> None:
        d = dict(SAMPLE, thumbnail="")
        item = _article_to_item(d)
        assert item is not None
        self.assertIsNone(item.thumbnail)

    def test_dspdate_fallback(self) -> None:
        d = dict(SAMPLE, startdate=None)
        item = _article_to_item(d)
        assert item is not None
        self.assertEqual(item.date, "2026-08-26")


class TestArticlesToItems(unittest.TestCase):
    def test_newest_first_and_dedupe(self) -> None:
        older = dict(SAMPLE, path="01_10000", startdate=1700000000)
        dupe = dict(SAMPLE)  # 与 SAMPLE 同 path
        items = _articles_to_items([older, SAMPLE, dupe])
        self.assertEqual([i.id for i in items], ["01_19692", "01_10000"])
        self.assertEqual(len(items), 2)

    def test_invalid_entries_dropped(self) -> None:
        items = _articles_to_items([SAMPLE, {"junk": True}, None, dict(SAMPLE, delflg="1")])
        self.assertEqual(len(items), 1)


class TestBrandFilter(unittest.TestCase):
    def _sc_article(self, path: str = "01_20000", startdate: int = 1700000000) -> dict:
        return dict(SAMPLE, path=path, startdate=startdate, brand=[{"name": "シャイニーカラーズ", "code": "SHINYCOLORS"}])

    def _multi_article(self, path: str = "01_20001", startdate: int = 1700000001) -> dict:
        # 跨企划合作：CG + ML
        return dict(
            SAMPLE,
            path=path,
            startdate=startdate,
            brand=[
                {"name": "シンデレラガールズ", "code": "CINDERELLAGIRLS"},
                {"name": "ミリオンライブ！", "code": "MILLIONLIVE"},
            ],
        )

    def _no_brand_article(self, path: str = "01_20002", startdate: int = 1700000002) -> dict:
        return dict(SAMPLE, path=path, startdate=startdate, brand=[])

    def test_whitelist_keeps_matching_only(self) -> None:
        arts = [self._sc_article(), self._multi_article(), self._no_brand_article()]
        items = _articles_to_items(arts, brands=["SHINYCOLORS"])
        self.assertEqual([i.id for i in items], ["01_20000"])

    def test_multi_brand_matches_any(self) -> None:
        arts = [self._sc_article(), self._multi_article()]
        items = _articles_to_items(arts, brands=["CINDERELLAGIRLS"])
        self.assertEqual([i.id for i in items], ["01_20001"])  # 合作新闻因含 CG 而保留
        items2 = _articles_to_items(arts, brands=["MILLIONLIVE"])
        self.assertEqual([i.id for i in items2], ["01_20001"])

    def test_no_brand_excluded_under_whitelist_kept_without(self) -> None:
        arts = [self._no_brand_article(), self._sc_article()]
        self.assertEqual(len(_articles_to_items(arts, brands=["SHINYCOLORS"])), 1)
        self.assertEqual(len(_articles_to_items(arts)), 2)  # 无白名单时全部保留

    def test_case_insensitive_and_str_input(self) -> None:
        arts = [self._sc_article(), self._multi_article()]
        self.assertEqual(len(_articles_to_items(arts, brands="shinycolors")), 1)
        self.assertEqual(len(_articles_to_items(arts, brands=["MiLLiOnLiVe"])), 1)

    def test_empty_whitelist_means_no_filter(self) -> None:
        arts = [self._sc_article(), self._multi_article(), self._no_brand_article()]
        self.assertEqual(len(_articles_to_items(arts, brands=[])), 3)
        self.assertEqual(len(_articles_to_items(arts, brands=None)), 3)


class TestApiBaseOverride(unittest.TestCase):
    """api_base 可选参数（M7 追加，向后兼容）：缩略图/请求基址使用覆盖值。"""

    def test_article_to_item_uses_base_for_thumbnail(self) -> None:
        item = _article_to_item(SAMPLE, base="https://mirror.example/api/")
        assert item is not None and item.thumbnail
        self.assertTrue(
            item.thumbnail.startswith("https://mirror.example/api/idolmaster/Image/get?path=")
        )

    def test_articles_to_items_passes_base(self) -> None:
        items = _articles_to_items([SAMPLE], base="https://mirror.example/api/")
        self.assertTrue(items[0].thumbnail.startswith("https://mirror.example/api/"))

    def test_default_base_unchanged(self) -> None:
        item = _article_to_item(SAMPLE)
        assert item is not None and item.thumbnail
        self.assertIn("cmsapi-frontend.idolmaster-official.jp", item.thumbnail)


class TestMinUpdatedFilter(unittest.TestCase):
    """min_updated 时间截断（M7 追加，向后兼容）：只保留启动时间之后更新/发布的条目。"""

    _MISSING = object()

    def _art(self, path: str, startdate: int, updated=_MISSING) -> dict:
        a = dict(SAMPLE, path=path, startdate=startdate)
        if updated is self._MISSING:
            a.pop("updated", None)  # SAMPLE 自带 updated，这里按需移除以测回退
        else:
            a["updated"] = updated
        return a

    def test_updated_gte_cutoff_kept(self) -> None:
        arts = [self._art("01_10001", 1000, updated=5000),
                self._art("01_10002", 1000, updated=100)]
        items = _articles_to_items(arts, min_updated=2000)
        self.assertEqual([i.id for i in items], ["01_10001"])

    def test_updated_missing_falls_back_to_startdate(self) -> None:
        arts = [self._art("01_10001", startdate=5000),
                self._art("01_10002", startdate=100)]
        items = _articles_to_items(arts, min_updated=2000)
        self.assertEqual([i.id for i in items], ["01_10001"])

    def test_no_cutoff_keeps_all(self) -> None:
        arts = [self._art("01_10001", 1000), self._art("01_10002", 1000)]
        self.assertEqual(len(_articles_to_items(arts, min_updated=None)), 2)


class TestEntry(unittest.TestCase):
    def test_invalid_limit(self) -> None:
        with self.assertRaises(ValueError):
            fetch_news_list(limit=0)


if __name__ == "__main__":
    unittest.main()
