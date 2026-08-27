"""S8 单测：歌曲反向索引（songbot.s8_song_index）.

约定（docs/S1-S7-taskplan.md §0.4 / docs/modules/S8-song-lookup-plan.md §5）：
- 全离线：fixtures/*.html + mock fetch_setlist，零网络；
- 覆盖：_appearance_specs（单页/多日映射）、build（fixture 真实数据/去重/坏页跳过/空）、
  refresh（全已知零抓取 / 新增在顶部只抓新增、首个已收录即停止 / 保留既有）、
  save/load（roundtrip 一致 / 缺失 / 损坏）、match_songs（精确/子串/多候选/无命中/空/候选列表入参）。

注：临时文件一律放工作区内的 `.tmp_test/`（沙箱禁止写系统 %TEMP%，S4/S6 同坑）。

运行：
    python -m unittest tests.test_s8_song_index -v
"""

import json
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from songbot.models_song import Event, Setlist, SongEntry, SubEvent, Track  # noqa: E402
from songbot.s1_fetch_events import PAGE_BASE_URL, parse_events_html  # noqa: E402
from songbot.s2_fetch_setlist import parse_setlist_html  # noqa: E402
from songbot.s3_match import normalize  # noqa: E402
from songbot.s8_song_index import (  # noqa: E402
    SongIndex,
    _appearance_specs,
    build_song_index,
    load_song_index,
    match_songs,
    refresh_song_index,
    save_song_index,
)

FIXTURES = Path(_ROOT) / "fixtures"
TMP_ROOT = Path(_ROOT) / ".tmp_test" / "s8"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

# 详情 URL 末尾文件名 -> fixture 文件名（离线映射）
FIXTURE_PAGES = {
    "iwsf_day1.html": "imas_db_iwsf_day1.html",
    "million_13th_day1.html": "imas_db_million_13th_day1.html",
    "cg_musical_dd.html": "imas_db_cg_musical_dd.html",
}


def _ws_tmp(prefix: str) -> str:
    """工作区内临时目录（不用 tempfile：Windows 受限 ACL 沙箱写被拒，S4 同坑）。"""
    d = os.path.join(str(TMP_ROOT), prefix + uuid.uuid4().hex[:8])
    os.makedirs(d, exist_ok=True)
    return d


def _rm_ws_tmp(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _events_all() -> list[Event]:
    html = (FIXTURES / "imas_db_song_event.html").read_text(encoding="utf-8")
    return parse_events_html(html, base_url=PAGE_BASE_URL)


def _events_subset() -> list[Event]:
    """只保留详情 URL 能映射到 fixture 的事件（IWSF / 13thLIVE / DERE）。

    注意：IWSF 的 day2/day3、13thLIVE 的 day2 等无 fixture -> _local_fetch 抛错 ->
    构建时记日志跳过（顺带覆盖「坏页跳过」路径）。
    """
    out = []
    for ev in _events_all():
        urls = [ev.url] if ev.url else [s.url for s in ev.sub_events]
        if any(any(u.rstrip("/").endswith(k) for k in FIXTURE_PAGES) for u in urls):
            out.append(ev)
    return out


def _local_fetch(url: str) -> Setlist:
    """离线抓取：URL 末尾文件名（endswith 匹配，与 S2 MockTransport 同约定）-> fixture 详情页解析；
    无映射抛错（模拟抓取失败）。"""
    for key, name in FIXTURE_PAGES.items():
        if url.rstrip("/").endswith(key):
            html = (FIXTURES / name).read_text(encoding="utf-8")
            return parse_setlist_html(html, url=url)
    raise FileNotFoundError(f"no fixture for {url}")


class TestAppearanceSpecs(unittest.TestCase):
    """事件列表 -> 详情页清单（单页/多日映射 + 顺序保持）。"""

    def test_multi_day_and_single_page(self):
        ev_multi = Event(title="MILLION 13thLIVE", year="2026",
                         sub_events=[SubEvent("DAY1 全力援走", "…DAY1", "http://x/1.html", "2026/05/05(火祝)"),
                                     SubEvent("DAY2 Grand bal masqué", "…DAY2", "http://x/2.html", "2026/05/06(水)")])
        ev_single = Event(title="DERE of the DEAD", year="2026",
                          date="2026/07/04(土)・05(日)", url="http://x/s.html")
        specs = _appearance_specs([ev_multi, ev_single])
        self.assertEqual(len(specs), 3)
        self.assertEqual(specs[0], {"url": "http://x/1.html", "event_title": "MILLION 13thLIVE",
                                    "event_year": "2026", "sub_title": "DAY1 全力援走",
                                    "date": "2026/05/05(火祝)"})
        self.assertEqual(specs[1]["sub_title"], "DAY2 Grand bal masqué")
        self.assertEqual(specs[2], {"url": "http://x/s.html", "event_title": "DERE of the DEAD",
                                    "event_year": "2026", "sub_title": "",
                                    "date": "2026/07/04(土)・05(日)"})

    def test_skips_empty_urls(self):
        ev = Event(title="无URL", year="2026", sub_events=[SubEvent("DAY1", "…", "", "2026/01/01(木)")])
        self.assertEqual(_appearance_specs([ev]), [])


class TestBuild(unittest.TestCase):
    def test_build_covers_fixture_songs(self):
        idx = build_song_index(_events_subset(), _local_fetch)
        self.assertGreater(len(idx.entries), 0)
        # 只成功抓 3 个详情页（其余无 fixture 被跳过）
        self.assertEqual(len(idx.source_urls), 3)
        # Dance in the Light 出现在 IWSF day1 + 13th day1 两场
        entry = idx.entries.get(normalize("Dance in the Light"))
        self.assertIsNotNone(entry, "fixture 中应有 Dance in the Light")
        self.assertEqual(len(entry.appearances), 2)
        urls = {a.url for a in entry.appearances}
        self.assertTrue(any(u.endswith("iwsf_day1.html") for u in urls))
        self.assertTrue(any(u.endswith("million_13th_day1.html") for u in urls))
        self.assertEqual(entry.title, "Dance in the Light")
        # Marionetteは眠らない 只出现在 IWSF day1
        entry2 = idx.entries.get(normalize("Marionetteは眠らない"))
        self.assertIsNotNone(entry2)
        self.assertEqual(len(entry2.appearances), 1)
        self.assertTrue(entry2.appearances[0].url.endswith("iwsf_day1.html"))
        # appearance 元信息完整（事件名/年份/日期）
        a = entry2.appearances[0]
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", a.event_title)
        self.assertEqual(a.event_year, "2026")
        self.assertTrue(a.date)

    def test_dedupe_same_live(self):
        """同一场 LIVE 内同名曲多次演唱只记一次 appearance。"""
        def fetch(url):
            return Setlist(title="X", date_venue="", url=url, tracks=[
                Track(no=1, title="同曲"), Track(no=2, title="同曲"),
            ])
        ev = Event(title="EV", year="2026", date="2026/07/01(水)", url="http://x/one.html")
        idx = build_song_index([ev], fetch)
        entry = idx.entries[normalize("同曲")]
        self.assertEqual(len(entry.appearances), 1)
        self.assertEqual(entry.appearances[0].url, "http://x/one.html")
        self.assertEqual(idx.source_urls, {"http://x/one.html"})

    def test_bad_url_skipped_not_fatal(self):
        def fetch(url):
            if url.endswith("bad.html"):
                raise RuntimeError("boom")
            return Setlist(title="OK", date_venue="", url=url, tracks=[Track(no=1, title="好曲")])
        good = Event(title="G", year="2026", url="http://x/good.html")
        bad = Event(title="B", year="2026", url="http://x/bad.html")
        idx = build_song_index([bad, good], fetch)
        self.assertIn(normalize("好曲"), idx.entries)
        self.assertEqual(idx.source_urls, {"http://x/good.html"})

    def test_empty_events(self):
        idx = build_song_index([], lambda url: None)
        self.assertEqual(len(idx.entries), 0)
        self.assertEqual(len(idx.source_urls), 0)


class TestRefresh(unittest.TestCase):
    def test_all_known_zero_fetch(self):
        """事件列表首个详情 URL 已收录 -> 立即停止，零抓取。"""
        idx = build_song_index(_events_subset(), _local_fetch)
        calls: list[str] = []

        def recording(url):
            calls.append(url)
            return _local_fetch(url)

        refresh_song_index(idx, _events_subset(), recording)
        self.assertEqual(calls, [], "全已知不应触发任何抓取")
        self.assertEqual(len(idx.source_urls), 3)

    def test_new_event_on_top_fetches_only_new(self):
        """新增事件在列表顶部（首个 URL 未收录）-> 只抓新增，遇到第一个已收录即停止。"""
        idx = SongIndex()
        idx.source_urls.add("http://x/known.html")
        calls: list[str] = []

        def recording(url):
            calls.append(url)
            return Setlist(title="T", date_venue="", url=url, tracks=[Track(no=1, title="新曲テスト")])

        new_ev = Event(title="新LIVE", year="2026",
                       sub_events=[SubEvent("DAY1", "…", "http://x/new.html", "2026/08/01(土)")])
        known_ev = Event(title="旧LIVE", year="2026",
                         sub_events=[SubEvent("DAY1", "…", "http://x/known.html", "2026/07/01(水)")])
        refresh_song_index(idx, [new_ev, known_ev], recording)
        self.assertEqual(calls, ["http://x/new.html"], "应只抓新增 URL 并在首个已收录处停止")
        self.assertIn(normalize("新曲テスト"), idx.entries)
        self.assertIn("http://x/new.html", idx.source_urls)
        self.assertIn("http://x/known.html", idx.source_urls)   # 既有保留

    def test_refresh_keeps_existing_entries(self):
        idx = build_song_index(_events_subset(), _local_fetch)
        before = {k: len(v.appearances) for k, v in idx.entries.items()}
        refresh_song_index(idx, _events_subset(), _local_fetch)
        after = {k: len(v.appearances) for k, v in idx.entries.items()}
        self.assertEqual(after, before)


class TestSerialization(unittest.TestCase):
    def test_roundtrip(self):
        idx = build_song_index(_events_subset(), _local_fetch)
        td = _ws_tmp("s8idx_")
        try:
            p = os.path.join(td, "song_index.json")
            save_song_index(idx, p)
            loaded = load_song_index(p)
            self.assertIsNotNone(loaded)
            self.assertEqual(len(loaded.entries), len(idx.entries))
            self.assertEqual(loaded.source_urls, idx.source_urls)
            e1 = loaded.entries[normalize("Dance in the Light")]
            self.assertEqual(len(e1.appearances), 2)
            self.assertEqual(e1.appearances[0].url, idx.entries[normalize("Dance in the Light")].appearances[0].url)
            self.assertAlmostEqual(loaded.fetched_at, idx.fetched_at, places=2)
        finally:
            _rm_ws_tmp(td)

    def test_load_missing_returns_none(self):
        self.assertIsNone(load_song_index(os.path.join(_ws_tmp("none_"), "no.json")))

    def test_load_corrupt_returns_none(self):
        td = _ws_tmp("bad_")
        try:
            p = os.path.join(td, "bad.json")
            Path(p).write_text("{ not json", encoding="utf-8")
            self.assertIsNone(load_song_index(p))
        finally:
            _rm_ws_tmp(td)

    def test_save_json_structure(self):
        idx = SongIndex()
        idx.source_urls.add("http://x/1.html")
        idx.entries["abc"] = SongEntry(title="ABC", appearances=[])
        td = _ws_tmp("json_")
        try:
            p = os.path.join(td, "x.json")
            save_song_index(idx, p)
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            self.assertEqual(data["source_urls"], ["http://x/1.html"])
            self.assertEqual(data["songs"][0]["title"], "ABC")
        finally:
            _rm_ws_tmp(td)


class TestMatchSongs(unittest.TestCase):
    def setUp(self):
        self.idx = build_song_index(_events_subset(), _local_fetch)

    def test_exact(self):
        hits = match_songs("Marionetteは眠らない", self.idx)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "Marionetteは眠らない")

    def test_fullwidth(self):
        hits = match_songs("Ｍａｒｉｏｎｅｔｔｅは眠らない", self.idx)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "Marionetteは眠らない")

    def test_substring(self):
        hits = match_songs("DANCE IN THE LIGHT", self.idx)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "Dance in the Light")

    def test_multi_candidate_top_n(self):
        idx = SongIndex()
        idx.entries["brandnewwave"] = SongEntry(title="Brand New Wave!", appearances=[])
        idx.entries["brandnew"] = SongEntry(title="Brand New!!", appearances=[])
        idx.entries["other"] = SongEntry(title="Other Song", appearances=[])
        hits = match_songs("brand new", idx)
        self.assertEqual(len(hits), 2)
        # 精确命中的排最前（Brand New!! 100 > Brand New Wave! 80）
        self.assertEqual(hits[0].title, "Brand New!!")

    def test_no_hit(self):
        self.assertEqual(match_songs("不存在的歌xyz", self.idx), [])

    def test_short_substring_candidate_noise_guard(self):
        """过短歌名（如 "i"）作为 query 子串时不进候选（防 80 分包含噪声）；精确查询仍命中。"""
        idx = SongIndex()
        idx.entries["marionetteは眠らない"] = SongEntry(title="Marionetteは眠らない", appearances=[])
        idx.entries["i"] = SongEntry(title="i", appearances=[])
        hits = match_songs("Marionetteは眠らない", idx)
        self.assertEqual(len(hits), 1)                      # 不出现候选 "i"
        self.assertEqual(hits[0].title, "Marionetteは眠らない")
        exact = match_songs("i", idx)                        # 精确查询 "i" 仍命中且排最前
        self.assertGreaterEqual(len(exact), 1)
        self.assertEqual(exact[0].title, "i")

    def test_empty_query(self):
        self.assertEqual(match_songs("", self.idx), [])
        self.assertEqual(match_songs("  ・  ", self.idx), [])

    def test_list_input(self):
        entry = self.idx.entries[normalize("Dance in the Light")]
        hits = match_songs("dance in the light", [entry])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "Dance in the Light")


if __name__ == "__main__":
    unittest.main(verbosity=2)
