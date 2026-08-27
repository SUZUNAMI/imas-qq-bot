"""S1 单测：列表抓取 + 解析（songbot.s1_fetch_events）.

约定（docs/S1-S7-taskplan.md §0.4）：
- 解析类单测用 fixtures/imas_db_song_event.html 本地样本，**不联网**；
- 抓取层用 httpx.MockTransport 注入 client，同样不联网。

运行（本机无 pytest，pip 被拦截，用标准库 unittest；后续合并回主仓库后 pytest 也可直接发现）：
    python -m unittest discover -s tests -p "test_s1*.py" -v
    （或 python -m unittest tests.test_s1_fetch_events -v）
"""

import os
import sys
import unittest
from unittest import mock

# 路径引导：仓库根加入 sys.path，使 `import songbot` 可用（unittest 默认不会加根目录）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from songbot import s1_fetch_events  # noqa: E402  （先导入以触发 vendor 兜底）

import httpx  # noqa: E402  （vendor 由 s1_fetch_events 自身兜底）
from songbot.models_song import Event, SubEvent  # noqa: E402
from songbot.s1_fetch_events import (  # noqa: E402
    EVENT_LIST_URL,
    FetchError,
    fetch_events,
    parse_events_html,
)

FIXTURE = os.path.join(_ROOT, "fixtures", "imas_db_song_event.html")
FIXTURE_BASE = "http://imas-db.jp/song/event/"   # 与 PAGE_BASE_URL 同义，独立写死防漂移


def _load_fixture() -> str:
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


def _parse_fixture() -> list[Event]:
    return parse_events_html(_load_fixture(), base_url=FIXTURE_BASE)


def _find(events: list[Event], keyword: str) -> Event:
    """按 Event.title 包含关键字查找（唯一）。"""
    hits = [e for e in events if keyword in e.title]
    if len(hits) != 1:
        raise AssertionError(f"期望唯一命中 {keyword!r}，实际 {len(hits)} 个: {[e.title for e in hits]}")
    return hits[0]


class TestParseTotalAndOrder(unittest.TestCase):
    def test_total_125(self):
        self.assertEqual(len(_parse_fixture()), 125)

    def test_year_groups_2013_to_2026(self):
        years = {e.year for e in _parse_fixture()}
        self.assertEqual(years, {str(y) for y in range(2013, 2027)})

    def test_year_striped_no_suffix(self):
        events = _parse_fixture()
        self.assertEqual(events[0].year, "2026")
        self.assertNotIn("年", events[0].year)

    def test_order_newest_first(self):
        events = _parse_fixture()
        years = [int(e.year) for e in events]
        self.assertEqual(years, sorted(years, reverse=True))


class TestMultiDayEvent(unittest.TestCase):
    def test_13thlive_two_subs(self):
        ev = _find(_parse_fixture(), "13thLIVE")
        self.assertEqual(ev.year, "2026")
        self.assertEqual(ev.url, "", "多日事件顶层 url 应为空")
        self.assertEqual(len(ev.sub_events), 2)
        d1, d2 = ev.sub_events
        self.assertIsInstance(d1, SubEvent)
        self.assertEqual(d1.title, "DAY1 全力援走")
        self.assertEqual(d1.date, "2026/05/05(火祝)")
        self.assertTrue(d1.url.startswith("http://"))
        self.assertTrue(d1.url.endswith("million_13th_day1.html"))
        self.assertEqual(d2.title, "DAY2 Grand bal masqué")
        self.assertEqual(d2.date, "2026/05/06(水祝)")
        self.assertTrue(d2.url.endswith("million_13th_day2.html"))
        self.assertIn("13thLIVE", d1.full_title)

    def test_iwsf_three_subs_and_full_titles(self):
        ev = _find(_parse_fixture(), "IDOL WORLD SUPER FESTIVAL 2026")
        self.assertEqual(len(ev.sub_events), 3)
        self.assertEqual(ev.sub_events[0].title, "第一公演 -YAKUDOU-")
        self.assertEqual(ev.sub_events[0].date, "2026/07/24(金)")
        self.assertTrue(ev.sub_events[0].url.endswith("idolmaster_iwsf_day1.html"))
        self.assertEqual(
            ev.sub_events[0].full_title,
            "IDOL WORLD SUPER FESTIVAL 2026 第一公演 -YAKUDOU-",
        )

    def test_multi_title_cleaned_no_badges_no_date(self):
        # 13thLIVE 标题清洗后不含徽章文本/日期
        ev = _find(_parse_fixture(), "13thLIVE")
        self.assertEqual(ev.title, "THE IDOLM@STER MILLION LIVE! 13thLIVE")
        self.assertNotIn("ミリオン", ev.title)
        self.assertNotIn("2026", ev.title)

    def test_multi_title_ruby_known_item(self):
        # 已知项：<ruby> 取文本为 rb+rt 连写（接受，不影响匹配）
        ev = _find(_parse_fixture(), "H.I.F")
        self.assertIn("選抜試験", ev.title)
        self.assertIn("H.I.F", ev.title)


class TestSinglePageEvent(unittest.TestCase):
    def test_dere_of_the_dead(self):
        ev = _find(_parse_fixture(), "DERE of the DEAD")
        self.assertTrue(ev.url.startswith("http://"))
        self.assertTrue(ev.url.endswith("cinderella_cg_musical_dd.html"))
        self.assertEqual(ev.sub_events, [])

    def test_single_page_event_date(self):
        # 单页事件 date：去 "- " 前缀的日期文本（S3 时间筛选用）
        ev = _find(_parse_fixture(), "DERE of the DEAD")
        self.assertEqual(ev.date, "2026/07/04(土)・05(日)")

    def test_multi_day_event_date_empty(self):
        # 多日事件顶层 date 为空串，日期在子事件（契约约定，S3 时间筛选走 sub_events）
        ev = _find(_parse_fixture(), "13thLIVE")
        self.assertEqual(ev.date, "")
        self.assertEqual(ev.sub_events[0].date, "2026/05/05(火祝)")

    def test_brand_badge_title_preferred(self):
        ev = _find(_parse_fixture(), "DERE of the DEAD")
        # badge 文本是「シンデレラ」，title 是「シンデレラガールズ」→ 取 title
        self.assertEqual(ev.brands, ["シンデレラガールズ"])


class TestBrands(unittest.TestCase):
    def test_iwsf_brands_contains_all(self):
        ev = _find(_parse_fixture(), "IDOL WORLD SUPER FESTIVAL 2026")
        for expected in ("765PRO ALLSTARS", "シンデレラガールズ", "ミリオンライブ！",
                         "SideM", "シャイニーカラーズ", "学園アイドルマスター"):
            self.assertIn(expected, ev.brands)

    def test_mr_badge_title(self):
        ev = _find(_parse_fixture(), "IDOL WORLD SUPER FESTIVAL 2026")
        self.assertIn('"MORE RE@LITY"形式のイベント', ev.brands)


class TestDefensiveParsing(unittest.TestCase):
    """坏条目不抛异常：缺 date / 缺 href / 既无 a 也无 ul / 缺 h2 / 缺 ul。"""

    def test_missing_date_and_href(self):
        html = """<div class="section"><h2>2026年</h2><ul>
            <li data-brand-ids="1"><a href="./x.html">EVENT X</a></li>
            <li data-brand-ids="2"><ul><li><a href="./y.html">DAY1</a></li></ul></li>
        </ul></div>"""
        events = parse_events_html(html, base_url=FIXTURE_BASE)
        self.assertEqual(len(events), 2)
        single, multi = events
        self.assertEqual(single.url, FIXTURE_BASE + "x.html")
        self.assertEqual(single.date, "", "缺 small.date 单页事件 date 应给空串")
        self.assertEqual(multi.date, "", "多日事件顶层 date 应为空串")
        self.assertEqual(multi.sub_events[0].date, "", "缺 small.date 应给空串")
        self.assertEqual(multi.sub_events[0].full_title, "", "缺 title 属性应给空串")

    def test_single_date_strips_dash_prefix(self):
        html = """<div class="section"><h2>2026年</h2><ul>
            <li data-brand-ids="1"><a href="./x.html">EVENT X</a>
                <small class="date">- 2026/08/01(土)</small></li>
        </ul></div>"""
        events = parse_events_html(html, base_url=FIXTURE_BASE)
        self.assertEqual(events[0].date, "2026/08/01(土)", "date 应去掉 '- ' 前缀")

    def test_missing_href(self):
        html = """<div class="section"><h2>2026年</h2><ul>
            <li data-brand-ids="1"><a>NO HREF</a></li>
        </ul></div>"""
        events = parse_events_html(html, base_url=FIXTURE_BASE)
        self.assertEqual(events[0].title, "NO HREF")
        self.assertEqual(events[0].url, "", "缺 href 应给空串")

    def test_li_with_neither_a_nor_ul_skipped(self):
        html = """<div class="section"><h2>2026年</h2><ul>
            <li data-brand-ids="1">裸文本无 a 无 ul</li>
            <li data-brand-ids="2"><a href="./ok.html">OK</a></li>
        </ul></div>"""
        events = parse_events_html(html, base_url=FIXTURE_BASE)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "OK")

    def test_sub_without_a_skipped(self):
        html = """<div class="section"><h2>2026年</h2><ul>
            <li data-brand-ids="1"><ul><li>无 a 的子事件</li></ul></li>
        </ul></div>"""
        events = parse_events_html(html, base_url=FIXTURE_BASE)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].sub_events, [])

    def test_missing_h2_section_skipped(self):
        html = """<div class="section"><ul>
            <li data-brand-ids="1"><a href="./x.html">X</a></li>
        </ul></div>"""
        self.assertEqual(parse_events_html(html, base_url=FIXTURE_BASE), [])

    def test_missing_ul_section_skipped(self):
        html = """<div class="section"><h2>2026年</h2><p>no ul</p></div>"""
        self.assertEqual(parse_events_html(html, base_url=FIXTURE_BASE), [])

    def test_empty_html(self):
        self.assertEqual(parse_events_html("", base_url=FIXTURE_BASE), [])

    def test_li_without_data_brand_ids_ignored(self):
        html = """<div class="section"><h2>2026年</h2><ul>
            <li><a href="./not_top.html">不是顶层事件</a></li>
        </ul></div>"""
        self.assertEqual(parse_events_html(html, base_url=FIXTURE_BASE), [])


class TestFetchEvents(unittest.TestCase):
    """抓取层：httpx.MockTransport 注入，不联网。"""

    def _client(self, handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_fetch_with_mock_transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            # 模拟站点：Content-Type 无 charset
            return httpx.Response(200, content=_load_fixture().encode("utf-8"),
                                  headers={"Content-Type": "text/html"})

        events = fetch_events(EVENT_LIST_URL, client=self._client(handler))
        self.assertEqual(len(events), 125)
        self.assertEqual(events[0].year, "2026")
        # urljoin 基于传入 url 推导的目录基准
        self.assertTrue(events[0].sub_events or events[0].url)

    def test_utf8_decode_explicit(self):
        # 站点无 charset，必须按字节 UTF-8 解码；若按默认 latin-1，日文会变乱码
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_load_fixture().encode("utf-8"),
                                  headers={"Content-Type": "text/html; charset=iso-8859-1"})

        events = fetch_events(EVENT_LIST_URL, client=self._client(handler))
        titles = " ".join(e.title for e in events)
        self.assertIn("13thLIVE", titles)
        self.assertIn("学園", titles)   # 日文正常（乱码则此处失败）

    def test_http_error_raises_fetch_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        with self.assertRaises(FetchError):
            fetch_events(EVENT_LIST_URL, client=self._client(handler))

    def test_transport_error_retries_then_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused (mock)")

        with mock.patch.object(s1_fetch_events, "RETRY_ATTEMPTS", 1), \
             mock.patch.object(s1_fetch_events, "RETRY_BASE_DELAY", 0):
            with self.assertRaises(FetchError):
                fetch_events(EVENT_LIST_URL, client=self._client(handler))

    def test_5xx_retries_then_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="unavailable")

        with mock.patch.object(s1_fetch_events, "RETRY_ATTEMPTS", 1), \
             mock.patch.object(s1_fetch_events, "RETRY_BASE_DELAY", 0):
            with self.assertRaises(FetchError):
                fetch_events(EVENT_LIST_URL, client=self._client(handler))


if __name__ == "__main__":
    unittest.main(verbosity=2)
