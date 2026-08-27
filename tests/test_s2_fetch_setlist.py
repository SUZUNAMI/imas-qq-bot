"""S2 单测：详情抓取 + 解析（songbot.s2_fetch_setlist）.

约定（docs/S1-S7-taskplan.md §0.4）：
- 解析类单测用 fixtures/*.html 本地样本，**不联网**（三份 fixture 覆盖站点三种详情页版式）；
- 抓取层用 httpx.MockTransport 注入 client，同样不联网。

运行（本机无 pytest，pip 被拦截，用标准库 unittest；后续合并回主仓库后 pytest 也可直接发现）：
    python -m unittest tests.test_s2_fetch_setlist -v
"""

import os
import sys
import unittest
from unittest import mock

# 路径引导：仓库根加入 sys.path，使 `import songbot` 可用
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 先导入 songbot 模块（s1 顶部自带 vendor 兜底，会先把 ../vendor 插入 sys.path）
from songbot import s1_fetch_events  # noqa: E402
from songbot import s2_fetch_setlist  # noqa: E402
from songbot.models_song import Setlist, Track  # noqa: E402
from songbot.s2_fetch_setlist import (  # noqa: E402
    FetchError,
    fetch_setlist,
    parse_setlist_html,
)

import httpx  # noqa: E402  （vendor 已由 songbot 模块兜底）

FIX_IWSF = os.path.join(_ROOT, "fixtures", "imas_db_iwsf_day1.html")            # 版式 A
FIX_13TH = os.path.join(_ROOT, "fixtures", "imas_db_million_13th_day1.html")    # 版式 B
FIX_DERE = os.path.join(_ROOT, "fixtures", "imas_db_cg_musical_dd.html")        # 版式 C（音乐剧）
BASE = "http://imas-db.jp/song/event/"   # 与 PAGE_BASE_URL 同义，独立写死防漂移
URL_IWSF = BASE + "idolmaster_iwsf_day1.html"


def _load(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _parse_iwsf() -> Setlist:
    return parse_setlist_html(_load(FIX_IWSF), url=URL_IWSF)


def _parse_13th() -> Setlist:
    return parse_setlist_html(_load(FIX_13TH), url=BASE + "million_13th_day1.html")


def _parse_dere() -> Setlist:
    return parse_setlist_html(_load(FIX_DERE), url=BASE + "cinderella_cg_musical_dd.html")


class TestIwsfLayoutA(unittest.TestCase):
    """版式 A（div.m-2 日期场馆 / 出演アイドル）——计划 §S2 单测要点的基准样本。"""

    def test_title(self):
        self.assertEqual(_parse_iwsf().title, "IDOL WORLD SUPER FESTIVAL 2026 -YAKUDOU- [DAY1]")

    def test_date_venue_contains_date_and_venue_no_detail_link(self):
        dv = _parse_iwsf().date_venue
        self.assertIn("2026/07/24", dv)
        self.assertIn("京王アリーナTOKYO", dv)
        self.assertNotIn("詳細", dv, "日期场馆行应去掉「詳細」链接文本")
        self.assertNotIn("http", dv)

    def test_performers(self):
        p = _parse_iwsf().performers
        self.assertEqual(len(p), 14)
        self.assertIn("舞浜歩", p)
        self.assertIn("玲音", p)
        self.assertTrue(all(n and "CV" not in n for n in p), "出演者应只取显示名，不含 CV title")

    def test_track_count(self):
        self.assertEqual(len(_parse_iwsf().tracks), 21)

    def test_first_track(self):
        t = _parse_iwsf().tracks[0]
        self.assertIsInstance(t, Track)
        self.assertEqual(t.no, 1)
        self.assertEqual(t.title, "Dance in the Light")
        self.assertEqual(t.brand, "ミリオンライブ！")
        self.assertEqual(t.performers, ["舞浜歩", "菊地真", "城ヶ崎美嘉", "牙崎漣"])
        self.assertIsNone(t.link)

    def test_title_cleaned_no_badge_no_parens(self):
        for t in _parse_iwsf().tracks:
            self.assertNotIn("ミリオン", t.title)          # 徽章文本不得混入歌名
            self.assertNotIn("(", t.title)
            self.assertNotIn(")", t.title)
            self.assertNotIn("シンデレラ", t.title)

    def test_performers_no_separators(self):
        # 行 2：'十王星南 / 舞浜歩, 神谷奈緒' → 只取 idol-name，3 个，无分隔符残留
        t = _parse_iwsf().tracks[1]
        self.assertEqual(t.title, "Choo Choo Choo")
        self.assertEqual(t.performers, ["十王星南", "舞浜歩", "神谷奈緒"])

    def test_linked_track(self):
        t = _parse_iwsf().tracks[6]  # Marionetteは眠らない
        self.assertEqual(t.no, 7)
        self.assertEqual(t.title, "Marionetteは眠らない")
        self.assertEqual(t.link, "http://imas-db.jp/song/detail/285.html")
        self.assertEqual(t.brand, "ミリオンライブ！")

    def test_last_track_all_members(self):
        t = _parse_iwsf().tracks[-1]
        self.assertEqual(t.no, 21)
        self.assertEqual(t.title, "ダンス・ダンス・ダンス")
        self.assertEqual(t.performers, ["全員"])
        self.assertEqual(t.brand, "THE IDOLM@STERシリーズ")

    def test_url_field(self):
        self.assertEqual(_parse_iwsf().url, URL_IWSF)


class TestMillion13thLayoutB(unittest.TestCase):
    """版式 B（<p> 日期场馆 / my-2 出演 / 无徽章 / notes 保留）。"""

    def test_title(self):
        title = _parse_13th().title
        self.assertIn("13thLIVE", title)
        self.assertIn("DAY1", title)

    def test_date_venue_exact(self):
        # <p>2026/05/05(火/祝) 有明アリーナ, 開場17:00/開演18:00 <a>詳細</a></p>（去链接后精确匹配）
        self.assertEqual(_parse_13th().date_venue, "2026/05/05(火/祝) 有明アリーナ, 開場17:00/開演18:00")

    def test_performers(self):
        p = _parse_13th().performers
        self.assertEqual(len(p), 14)
        self.assertIn("駒形友梨", p)
        self.assertIn("山崎はるか", p)

    def test_track_count_and_no_brands(self):
        tracks = _parse_13th().tracks
        self.assertEqual(len(tracks), 23)
        self.assertTrue(all(t.brand is None for t in tracks), "该页 tracklist 无徽章，brand 应全为 None")

    def test_first_track(self):
        t = _parse_13th().tracks[0]
        self.assertEqual((t.no, t.title, t.performers), (1, "Only One Second", ["全員"]))

    def test_notes_preserved_in_title(self):
        # <small class="notes">(新曲)</small> 非徽章，保留（忠实显示）
        t = [x for x in _parse_13th().tracks if "SPARKERS" in x.title][0]
        self.assertEqual(t.no, 21)
        self.assertEqual(t.title, "SPARKERS (新曲)")
        self.assertEqual(t.performers, ["駒形友梨"])
        self.assertIsNone(t.link)

    def test_last_track(self):
        t = _parse_13th().tracks[-1]
        self.assertEqual((t.no, t.title, t.performers), (23, "咲くは浮世の君花火", ["全員"]))


class TestDereMusicalLayoutC(unittest.TestCase):
    """版式 C（音乐剧）：公演日程表不误当日期场馆、幕标题行跳过、无序号行回退 no。"""

    def test_title(self):
        self.assertEqual(_parse_dere().title, "CINDERELLA GIRLS MUSICAL DERE of the DEAD")

    def test_date_venue_is_venue_line_not_schedule_cell(self):
        # 关键泛化：必须取「公演概要」的日期/场馆行，而不是公演日程表（DAY1/DAY2 開場/開演）单元格
        self.assertEqual(_parse_dere().date_venue, "2026/07/04(土)・05(日) オリックス劇場(大阪)")

    def test_performers(self):
        self.assertEqual(
            _parse_dere().performers,
            ["佐倉薫", "花谷麻妃", "田辺留依", "長島光那", "松永あかね",
             "立花日菜", "集貝はな", "原優子", "星希成奏"],
        )

    def test_part_header_rows_skipped(self):
        tracks = _parse_dere().tracks
        self.assertEqual(len(tracks), 21, "23 行 - 2 个幕标题行 = 21 首")
        self.assertFalse(any(t.title.startswith("【") for t in tracks), "幕标题行不得混入曲目")

    def test_first_track_unnumbered_fallback_no(self):
        t = _parse_dere().tracks[0]
        self.assertEqual(t.no, 1, "无序号行 no 应回退为运行序号 1")
        self.assertEqual(t.title, "CoCo夏夏夏 Holiday")
        self.assertTrue(t.link and t.link.endswith("/song/detail/674.html"))
        self.assertEqual(
            t.performers,
            ["田辺留依", "長島光那", "松永あかね", "立花日菜", "集貝はな", "原優子", "星希成奏"],
        )

    def test_second_track_plain_text_performer(self):
        t = _parse_dere().tracks[1]
        self.assertEqual(t.no, 2)
        self.assertEqual(t.title, "THE VILLAIN'S NIGHT")
        self.assertEqual(t.performers, ["城主(穴沢裕介)"])

    def test_numbered_row_preserved(self):
        t = [x for x in _parse_dere().tracks if x.no == 14][0]
        self.assertEqual(t.title, "パ・リ・ラ")
        self.assertEqual(t.performers, ["アイドル全員"])


class TestDefensiveParsing(unittest.TestCase):
    """坏输入不抛异常：缺表格 / 缺 tbody / 缺标题 / 缺日期行 / 缺出演块 / 坏行。"""

    def test_no_table(self):
        sl = parse_setlist_html("<html><body><h1 id='page_title'>X</h1></body></html>")
        self.assertEqual(sl.tracks, [])
        self.assertEqual(sl.title, "X")

    def test_table_without_tbody(self):
        html = "<table class='tracklist'><tr><td>1</td><td>A</td><td>B</td></tr></table>"
        self.assertEqual(parse_setlist_html(html).tracks, [])

    def test_tr_with_less_than_3_td_skipped(self):
        html = ("<table class='tracklist'><tbody>"
                "<tr><th colspan='3'>【幕】</th></tr>"
                "<tr><td></td><td>A</td><td>全員</td></tr>"
                "</tbody></table>")
        tracks = parse_setlist_html(html).tracks
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "A")

    def test_non_numeric_no_fallback_running_index(self):
        html = ("<table class='tracklist'><tbody>"
                "<tr><td></td><td>X</td><td>全員</td></tr>"
                "<tr><td></td><td>Y</td><td>全員</td></tr>"
                "</tbody></table>")
        tracks = parse_setlist_html(html).tracks
        self.assertEqual([t.no for t in tracks], [1, 2])

    def test_mixed_numbered_and_unnumbered(self):
        html = ("<table class='tracklist'><tbody>"
                "<tr><td>5</td><td>X</td><td>全員</td></tr>"
                "<tr><td></td><td>Y</td><td>全員</td></tr>"
                "</tbody></table>")
        tracks = parse_setlist_html(html).tracks
        self.assertEqual([t.no for t in tracks], [5, 2])

    def test_missing_page_title(self):
        sl = parse_setlist_html("<html><body><p>no h1</p></body></html>")
        self.assertEqual(sl.title, "")

    def test_missing_date_venue(self):
        sl = parse_setlist_html("<html><body><h1 id='page_title'>X</h1></body></html>")
        self.assertEqual(sl.date_venue, "")

    def test_missing_performers_block(self):
        sl = parse_setlist_html("<html><body><h1 id='page_title'>X</h1></body></html>")
        self.assertEqual(sl.performers, [])

    def test_empty_html(self):
        sl = parse_setlist_html("")
        self.assertEqual(sl.title, "")
        self.assertEqual(sl.date_venue, "")
        self.assertEqual(sl.performers, [])
        self.assertEqual(sl.tracks, [])

    def test_date_fallback_keywords_without_detail_link(self):
        # 无「詳細」链接时，兜底取含 開演/開場 的最短 div/p
        html = ("<html><body><h1 id='page_title'>X</h1>"
                "<div class='section'><p>2026/08/01(土) 東京ドーム, 開場16:00/開演17:00</p>"
                "<p>其后的长文本不能抢先匹配</p></div></body></html>")
        self.assertEqual(parse_setlist_html(html).date_venue, "2026/08/01(土) 東京ドーム, 開場16:00/開演17:00")

    def test_urljoin_base_uses_url(self):
        html = "<table class='tracklist'><tbody><tr><td>1</td><td><a href='/song/detail/42.html'>A</a></td><td>全員</td></tr></tbody></table>"
        sl = parse_setlist_html(html, url="http://imas-db.jp/song/event/xxx_day1.html")
        self.assertEqual(sl.tracks[0].link, "http://imas-db.jp/song/detail/42.html")
        self.assertEqual(sl.url, "http://imas-db.jp/song/event/xxx_day1.html")


class TestFetchSetlist(unittest.TestCase):
    """抓取层：httpx.MockTransport 注入，不联网。"""

    def _client(self, handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_fetch_with_mock_transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_load(FIX_IWSF).encode("utf-8"),
                                  headers={"Content-Type": "text/html"})

        sl = fetch_setlist(URL_IWSF, client=self._client(handler))
        self.assertEqual(len(sl.tracks), 21)
        self.assertIn("-YAKUDOU-", sl.title)
        self.assertEqual(sl.url, URL_IWSF)

    def test_utf8_decode_explicit(self):
        # 站点无 charset；若按默认 latin-1，日文会变乱码
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_load(FIX_13TH).encode("utf-8"),
                                  headers={"Content-Type": "text/html; charset=iso-8859-1"})

        sl = fetch_setlist(BASE + "million_13th_day1.html", client=self._client(handler))
        self.assertIn("13thLIVE", sl.title)
        self.assertIn("有明アリーナ", sl.date_venue)
        self.assertEqual(sl.tracks[0].title, "Only One Second")

    def test_http_error_raises_fetch_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        with self.assertRaises(FetchError):
            fetch_setlist(URL_IWSF, client=self._client(handler))

    def test_transport_error_retries_then_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused (mock)")

        with mock.patch.object(s1_fetch_events, "RETRY_ATTEMPTS", 1), \
             mock.patch.object(s1_fetch_events, "RETRY_BASE_DELAY", 0):
            with self.assertRaises(FetchError):
                fetch_setlist(URL_IWSF, client=self._client(handler))

    def test_5xx_retries_then_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="unavailable")

        with mock.patch.object(s1_fetch_events, "RETRY_ATTEMPTS", 1), \
             mock.patch.object(s1_fetch_events, "RETRY_BASE_DELAY", 0):
            with self.assertRaises(FetchError):
                fetch_setlist(URL_IWSF, client=self._client(handler))


# 显式注入的应援色表（等价 data/songbot_site_colors.json 结构，测试不依赖该文件）
_TABLES = (
    {"1": "765as", "4": "cinderella", "5": "million", "6": "sidem", "8": "shinycolors", "11": "gakuen"},
    {"cinderella-attr-1": "#ef2782", "cinderella-attr-3": "#f49207",
     "million-attr-1": "#ea3f83", "million-attr-2": "#275cf6"},
    {"97": "#101010", "105": "#af011c"},
    {"765as": "#f34f6d", "cinderella": "#2681c8", "million": "#ffc30b",
     "sidem": "#0fbe94", "gakuen": "#f39800", "shinycolors": "#8dbbff"},
    # 角色个人应援色（data-character-id，优先级最高）
    {"16": "#b4e04b", "8": "#515558", "73": "#9678d3", "113": "#fe9d1a",
     "220": "#fed552", "248": "#e25a9b", "312": "#101010", "305": "#fcf",
     "357": "#af011c", "395": "#008e74", "431": "#7cfc00"},
)


class TestIdolColors(unittest.TestCase):
    """应援色：idol-name 的 data-* 属性 → 色（character > group > attr > brand）。"""

    def _parse(self, name: str, url_suffix: str) -> Setlist:
        return parse_setlist_html(_load(os.path.join(_ROOT, "fixtures", name)),
                                  url=BASE + url_suffix, idol_colors=_TABLES)

    def test_character_color_wins_over_brand(self):
        # 星井美希（765AS）：char 16 个人色 #b4e04b 优先于品牌 765as #f34f6d
        sl = self._parse("imas_db_iwsf_day1.html", "idolmaster_iwsf_day1.html")
        colors = dict(zip(sl.performers, sl.performer_colors))
        self.assertEqual(colors["星井美希"], "#b4e04b")
        self.assertEqual(colors["神谷奈緒"], "#9678d3")   # char 73（灰姑娘）
        self.assertEqual(colors["伊吹翼"], "#fed552")     # char 220（百万）

    def test_character_color_wins_over_attr(self):
        # 菊地真（跨品牌，cinderella-attr=1 + million-attr=1）：char 8 个人色 #515558 优先
        sl = self._parse("imas_db_iwsf_day1.html", "idolmaster_iwsf_day1.html")
        t1 = sl.tracks[0]
        colors = dict(zip(t1.performers, t1.performer_colors))
        self.assertEqual(colors["菊地真"], "#515558")
        self.assertEqual(colors["舞浜歩"], "#e25a9b")     # char 248
        self.assertEqual(colors["城ヶ崎美嘉"], "#fe9d1a")  # char 113

    def test_character_and_group_same_value(self):
        # 牙崎漣 char 312 与 group 97 同色 #101010（SideM 组合色=成员个人色），character 优先不改变结果
        sl = self._parse("imas_db_iwsf_day1.html", "idolmaster_iwsf_day1.html")
        colors = dict(zip(sl.performers, sl.performer_colors))
        self.assertEqual(colors["牙崎漣"], "#101010")

    def test_million_attr_colors(self):
        # 13thLIVE 出演者块显示声优名（idol-name span 文本=CV 名，title=「idol(CV:…)」）；
        # 测试表未含 13th 角色 char id → 回退 attr（駒形友梨=高山紗代子 princess）；
        # Machico（伊吹翼 CV）char 220 在表 → 个人色 #fed552 优先于 attr-3
        sl = self._parse("imas_db_million_13th_day1.html", "million_13th_day1.html")
        colors = dict(zip(sl.performers, sl.performer_colors))
        self.assertEqual(colors["駒形友梨"], "#ea3f83")   # million-attr-1 princess（无 char 表时 attr 生效）
        self.assertEqual(colors["Machico"], "#fed552")    # char 220（伊吹翼个人色）优先

    def test_plain_text_performer_no_color(self):
        # 「全員」等无 idol-name span → 颜色 None（与 performers 平行）
        sl = self._parse("imas_db_iwsf_day1.html", "idolmaster_iwsf_day1.html")
        last = sl.tracks[-1]
        self.assertEqual(last.performers, ["全員"])
        self.assertEqual(last.performer_colors, [None])

    def test_default_reading_no_json_crash(self):
        # 不注入色表（读 data/songbot_site_colors.json，存在/缺失都不崩）
        sl = parse_setlist_html(_load(os.path.join(_ROOT, "fixtures", "imas_db_iwsf_day1.html")),
                                url=BASE + "idolmaster_iwsf_day1.html")
        self.assertEqual(len(sl.performer_colors), len(sl.performers))
        for t in sl.tracks:
            self.assertEqual(len(t.performer_colors), len(t.performers))


if __name__ == "__main__":
    unittest.main(verbosity=2)
