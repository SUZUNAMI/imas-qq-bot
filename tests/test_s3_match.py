"""S3 单测：查询判别 + 模糊匹配 + 时间筛选（songbot.s3_match）.

约定（docs/S1-S7-taskplan.md §0.4 / §S3）：
- 纯函数零网络；名称匹配类用 fixtures/imas_db_song_event.html 解析出的真实事件
  （125 个，2026 年 14 个）离线断言；
- 时间判别/规范化/解析类为纯函数直测。

运行（本机无 pytest，用标准库 unittest；后续合并回主仓库后 pytest 也可直接发现）：
    python -m unittest tests.test_s3_match -v
"""

import os
import sys
import unittest

# 路径引导：仓库根加入 sys.path，使 `import songbot` 可用
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from songbot import s1_fetch_events  # noqa: E402  （先导入以触发 vendor 兜底）
from songbot.models_song import Event, SubEvent  # noqa: E402
from songbot.s1_fetch_events import PAGE_BASE_URL, parse_events_html  # noqa: E402
from songbot.s3_match import (  # noqa: E402
    DEFAULT_TOP_N,
    classify_query,
    filter_by_time,
    match_events,
    match_sub,
    normalize,
    normalize_light,
    parse_month,
    parse_time_query,
    split_command,
)

FIXTURE = os.path.join(_ROOT, "fixtures", "imas_db_song_event.html")


def _load_fixture() -> str:
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


def _parse_fixture() -> list[Event]:
    return parse_events_html(_load_fixture(), base_url=PAGE_BASE_URL)


def _find(events: list[Event], keyword: str) -> Event:
    """按 Event.title 包含关键字查找（唯一）。"""
    hits = [e for e in events if keyword in e.title]
    if len(hits) != 1:
        raise AssertionError(f"期望唯一命中 {keyword!r}，实际 {len(hits)} 个: {[e.title for e in hits]}")
    return hits[0]


class TestNormalize(unittest.TestCase):
    """normalize（名称匹配用）：NFKC + casefold + 去空白与分隔符。"""

    def test_fullwidth_to_halfwidth(self):
        self.assertEqual(normalize("ＭＩＬＬＩＯＮ"), normalize("million"))
        self.assertEqual(normalize("ＭＩＬＬＩＯＮ"), "million")

    def test_space_and_separators_removed(self):
        self.assertEqual(normalize("I W S F"), normalize("IWSF"))
        self.assertEqual(normalize("I・W・S・F"), normalize("IWSF"))
        self.assertEqual(normalize("IDOL WORLD SUPER FESTIVAL 2026"), "idolworldsuperfestival2026")

    def test_casefold(self):
        self.assertEqual(normalize("13thLIVE"), "13thlive")
        self.assertEqual(normalize("iwsf2026"), "iwsf2026")

    def test_japanese_preserved(self):
        self.assertEqual(normalize("学園アイドルマスター"), "学園アイドルマスター")
        self.assertEqual(normalize("シャニ"), "シャニ")

    def test_symbols_removed(self):
        # @ / ! 等非字母数字日文字符一律去除
        self.assertEqual(normalize("THE IDOLM@STER MILLION LIVE! 13thLIVE"),
                         "theidolmstermillionlive13thlive")

    def test_empty(self):
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize("  ・  "), "")


class TestNormalizeLight(unittest.TestCase):
    """normalize_light（时间判别用）：NFKC + casefold + 去首尾空白（保留 / - 分隔符）。"""

    def test_keeps_separators(self):
        self.assertEqual(normalize_light(" ２０２６－０７ "), "2026-07")
        self.assertEqual(normalize_light("2026/07"), "2026/07")

    def test_strip_only_edges(self):
        self.assertEqual(normalize_light("  2026年7月  "), "2026年7月")


class TestClassifyQuery(unittest.TestCase):
    """查询类型判别："time" | "name"。"""

    def test_time_queries(self):
        for q in ("2026年7月", "2026年", "2026", "2026-07", "2026/07", "2026.07", "7月"):
            self.assertEqual(classify_query(q), "time", q)

    def test_name_queries(self):
        for q in ("13thLIVE", "IWSF2026", "IWSF", "シャニ", "学園", "DERE of the DEAD",
                  "2026年7月14日", "全力援走"):
            self.assertEqual(classify_query(q), "name", q)

    def test_fullwidth_time_query(self):
        self.assertEqual(classify_query("２０２６年７月"), "time")


class TestParseTimeQuery(unittest.TestCase):
    """时间查询解析 -> (year, month)。"""

    def test_year_month(self):
        self.assertEqual(parse_time_query("2026年7月", 2026), (2026, 7))
        self.assertEqual(parse_time_query("2026-07", 2026), (2026, 7))
        self.assertEqual(parse_time_query("2026/07", 2026), (2026, 7))
        self.assertEqual(parse_time_query("2026.07", 2026), (2026, 7))

    def test_year_only(self):
        self.assertEqual(parse_time_query("2026年", 2026), (2026, None))
        self.assertEqual(parse_time_query("2026", 2026), (2026, None))

    def test_month_only_uses_latest_year(self):
        self.assertEqual(parse_time_query("7月", 2026), (2026, 7))
        self.assertEqual(parse_time_query("12月", 2026), (2026, 12))

    def test_not_time(self):
        self.assertIsNone(parse_time_query("13thLIVE", 2026))
        self.assertIsNone(parse_time_query("IWSF2026", 2026))
        self.assertIsNone(parse_time_query("", 2026))


class TestParseMonth(unittest.TestCase):
    """日期文本取首个 YYYY/MM 的月份；无匹配 None。"""

    def test_normal(self):
        self.assertEqual(parse_month("2026/07/04(土)・05(日)"), 7)
        self.assertEqual(parse_month("2026/12/31(火)"), 12)
        self.assertEqual(parse_month("中部夏時間 2026/05/15 (日本時間 2026/05/16)"), 5)

    def test_cross_month_uses_first(self):
        self.assertEqual(parse_month("2026/07/24(金)・26(日)"), 7)
        self.assertEqual(parse_month("2026/12/31(火)・2027/01/01(水)"), 12)

    def test_no_match(self):
        self.assertIsNone(parse_month("(DAY1夜・DAY2昼)"))
        self.assertIsNone(parse_month(""))
        self.assertIsNone(parse_month("2026年7月"))  # 非 YYYY/MM 分隔格式


class TestFilterByTime(unittest.TestCase):
    """时间筛选（fixture 真实事件，2026 年 14 个）。"""

    def setUp(self):
        self.events = _parse_fixture()

    def test_year_only_returns_all_14(self):
        hits = filter_by_time(self.events, 2026)
        self.assertEqual(len(hits), 14)
        self.assertTrue(all(e.year == "2026" for e in hits))

    def test_year_month_july(self):
        hits = filter_by_time(self.events, 2026, 7)
        titles = [e.title for e in hits]
        self.assertEqual(len(hits), 2)
        # IWSF 2026（多日 7/24–26）与 DERE of the DEAD（单页 7/4・5）
        self.assertTrue(any("IDOL WORLD SUPER FESTIVAL 2026" in t for t in titles))
        self.assertTrue(any("DERE of the DEAD" in t for t in titles))

    def test_year_month_may(self):
        hits = filter_by_time(self.events, 2026, 5)
        titles = [e.title for e in hits]
        self.assertTrue(any("13thLIVE" in t for t in titles), titles)   # 5/5–6
        self.assertTrue(any("H.I.F 選抜試験" in t for t in titles), titles)  # 5/16–17

    def test_year_month_march(self):
        hits = filter_by_time(self.events, 2026, 3)
        self.assertTrue(any("11thLIVE" in e.title for e in hits))   # 3/14–15

    def test_no_such_month(self):
        self.assertEqual(filter_by_time(self.events, 2026, 13), [])

    def test_no_such_year(self):
        self.assertEqual(filter_by_time(self.events, 1999), [])
        self.assertEqual(filter_by_time(self.events, 1999, 7), [])

    def test_order_preserved(self):
        hits = filter_by_time(self.events, 2026, 7)
        # 保持 events 原顺序：IWSF（列表靠前）在 DERE 之前
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", hits[0].title)


class TestFilterByTimeDefensive(unittest.TestCase):
    """日期文本无 YYYY/MM 不抛异常；该事件仅按年保留。"""

    def test_single_page_no_date_kept_by_year(self):
        ev = Event(title="X", year="2026", date="(DAY1夜・DAY2昼)")
        self.assertEqual(filter_by_time([ev], 2026, 7), [ev], "无 YYYY/MM → 仅按年保留")
        self.assertEqual(filter_by_time([ev], 2026), [ev])
        self.assertEqual(filter_by_time([ev], 2025, 7), [])

    def test_multi_day_subs_without_dates(self):
        sub = SubEvent(title="DAY1", full_title="", url="", date="")
        ev = Event(title="X", year="2026", sub_events=[sub])
        self.assertEqual(filter_by_time([ev], 2026, 7), [ev])

    def test_multi_day_one_sub_without_date_still_kept_by_month(self):
        sub1 = SubEvent(title="DAY1", full_title="", url="", date="2026/07/24(金)")
        sub2 = SubEvent(title="DAY2", full_title="", url="", date="")
        ev = Event(title="X", year="2026", sub_events=[sub1, sub2])
        self.assertEqual(filter_by_time([ev], 2026, 7), [ev])
        self.assertEqual(filter_by_time([ev], 2026, 8), [], "无月份信息子项不引入 8 月命中")

    def test_empty_events(self):
        self.assertEqual(filter_by_time([], 2026), [])
        self.assertEqual(filter_by_time([], 2026, 7), [])


class TestMatchEvents(unittest.TestCase):
    """名称匹配（fixture 真实事件）。"""

    def setUp(self):
        self.events = _parse_fixture()

    def test_iwsf2026_abbrev(self):
        hits = match_events("IWSF2026", self.events)
        self.assertEqual(len(hits), 1)
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", hits[0].title)

    def test_iwsf_abbrev(self):
        hits = match_events("IWSF", self.events)
        self.assertEqual(len(hits), 1)
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", hits[0].title)

    def test_13thlive(self):
        hits = match_events("13thLIVE", self.events)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "THE IDOLM@STER MILLION LIVE! 13thLIVE")

    def test_shani(self):
        hits = match_events("シャニ", self.events)
        self.assertGreaterEqual(len(hits), 1)
        self.assertTrue(any("シャニマス大感謝祭" in e.title for e in hits),
                        "应命中 シャニマス大感謝祭（SHINY COLORS 相关）")
        self.assertTrue(all("SHINY COLORS" in e.title or "シャニマス" in e.title for e in hits))

    def test_gakuen_top_n(self):
        hits = match_events("学園", self.events)
        self.assertEqual(len(hits), DEFAULT_TOP_N, "多候选取 top N")
        self.assertTrue(all("学園アイドルマスター" in e.title for e in hits))
        # 2026 的三个排在最前（原顺序，稳定排序）
        self.assertEqual(hits[0].year, "2026")

    def test_dere_full_name(self):
        hits = match_events("DERE of the DEAD", self.events)
        self.assertEqual(len(hits), 1)
        self.assertIn("DERE of the DEAD", hits[0].title)

    def test_moiw_alias_with_year(self):
        """MOIW 别名（2026-08-27 live 补漏）：MOIW = M@STERS OF IDOL WORLD。"""
        hits = match_events("MOIW 2025", self.events)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "THE IDOLM@STER M@STERS OF IDOL WORLD 2025")
        hits2 = match_events("MOIW2023", self.events)
        self.assertEqual(len(hits2), 1)
        self.assertIn("2023", hits2[0].title)
        self.assertNotIn("2025", hits2[0].title)

    def test_moiw_alias_bare_lists_candidates(self):
        hits = match_events("MOIW", self.events)
        self.assertGreaterEqual(len(hits), 2)
        self.assertTrue(all("M@STERS OF IDOL WORLD" in e.title for e in hits))

    def test_fullwidth_query(self):
        hits = match_events("１３ｔｈＬＩＶＥ", self.events)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "THE IDOLM@STER MILLION LIVE! 13thLIVE")

    def test_no_match(self):
        self.assertEqual(match_events("不存在的演出xyz", self.events), [])

    def test_empty_query(self):
        self.assertEqual(match_events("", self.events), [])
        self.assertEqual(match_events("  ・  ", self.events), [])

    def test_empty_events(self):
        self.assertEqual(match_events("13thLIVE", []), [])

    def test_not_confused_by_other_th_lives(self):
        # "13thLIVE" 不得误中 11thLIVE / 12thLIVE / SHINY COLORS 7th LIVE 等近似串
        hits = match_events("13thLIVE", self.events)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "THE IDOLM@STER MILLION LIVE! 13thLIVE")

    def test_not_confused_by_other_2026_events(self):
        # "IWSF2026" 只命中 IWSF，不误中其他含 2026 的事件
        hits = match_events("IWSF2026", self.events)
        self.assertEqual(len(hits), 1)
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", hits[0].title)


class TestMatchSub(unittest.TestCase):
    """二次确认：定位多日事件子公演（DAY 名 / 子标题 / 序号）。"""

    def setUp(self):
        self.events = _parse_fixture()
        self.ev13 = _find(self.events, "13thLIVE")   # DAY1 全力援走 / DAY2 Grand bal masqué

    def test_day_keyword(self):
        self.assertEqual(match_sub("DAY1", self.ev13).title, "DAY1 全力援走")
        self.assertEqual(match_sub("DAY2", self.ev13).title, "DAY2 Grand bal masqué")

    def test_day_keyword_lowercase(self):
        self.assertEqual(match_sub("day1", self.ev13).title, "DAY1 全力援走")

    def test_sub_title_keyword(self):
        self.assertEqual(match_sub("全力援走", self.ev13).title, "DAY1 全力援走")
        self.assertEqual(match_sub("Grand bal masqué", self.ev13).title, "DAY2 Grand bal masqué")

    def test_numeric_index(self):
        self.assertEqual(match_sub("1", self.ev13).title, "DAY1 全力援走")
        self.assertEqual(match_sub("2", self.ev13).title, "DAY2 Grand bal masqué")

    def test_out_of_range_index(self):
        self.assertIsNone(match_sub("3", self.ev13))
        self.assertIsNone(match_sub("0", self.ev13))

    def test_no_match(self):
        self.assertIsNone(match_sub("DAY3", self.ev13))
        self.assertIsNone(match_sub("不存在的子公演", self.ev13))

    def test_empty(self):
        self.assertIsNone(match_sub("", self.ev13))
        self.assertIsNone(match_sub("DAY1", None))

    def test_single_page_event_no_subs(self):
        ev_dere = _find(self.events, "DERE of the DEAD")
        self.assertEqual(ev_dere.sub_events, [])
        self.assertIsNone(match_sub("DAY1", ev_dere))

    def test_iwsf_subs_match_by_number(self):
        ev_iwsf = _find(self.events, "IDOL WORLD SUPER FESTIVAL 2026")
        self.assertEqual(match_sub("1", ev_iwsf).title, "第一公演 -YAKUDOU-")
        self.assertEqual(match_sub("YAKUDOU", ev_iwsf).title, "第一公演 -YAKUDOU-")


class TestSplitCommand(unittest.TestCase):
    """命令前缀分流（S9：live/binding/unbind/bindings/update；song 由 S8 接入）。"""

    def test_live(self):
        self.assertEqual(split_command("live IWSF2026"), ("live", "IWSF2026"))
        self.assertEqual(split_command("live 2026年7月"), ("live", "2026年7月"))

    def test_song(self):
        self.assertEqual(split_command("song Marionetteは眠らない"),
                         ("song", "Marionetteは眠らない"))
        self.assertEqual(split_command("SONG dance in the light"), ("song", "dance in the light"))

    def test_binding_rest_keeps_spaces(self):
        self.assertEqual(split_command("binding iwsf IDOL WORLD SUPER FESTIVAL 2026"),
                         ("binding", "iwsf IDOL WORLD SUPER FESTIVAL 2026"))

    def test_unbind(self):
        self.assertEqual(split_command("unbind iwsf"), ("unbind", "iwsf"))

    def test_bindings_no_rest(self):
        self.assertEqual(split_command("bindings"), ("bindings", ""))

    def test_update_live(self):
        self.assertEqual(split_command("update live"), ("update", "live"))

    def test_case_insensitive_command(self):
        self.assertEqual(split_command("LIVE IWSF2026"), ("live", "IWSF2026"))
        self.assertEqual(split_command("Update live"), ("update", "live"))

    def test_no_prefix_returns_none(self):
        self.assertIsNone(split_command("IWSF2026"))
        self.assertIsNone(split_command("13thLIVE"))
        self.assertIsNone(split_command("2026年7月"))
        self.assertIsNone(split_command(""))

    def test_unknown_command_returns_none(self):
        self.assertIsNone(split_command("foobar xxx"))
        self.assertIsNone(split_command("livee xxx"))

    def test_leading_space_tolerated(self):
        self.assertEqual(split_command("  live IWSF2026 "), ("live", "IWSF2026"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
