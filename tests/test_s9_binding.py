"""S9 单测：绑定别名存储（songbot.s9_binding）.

约定（docs/S1-S7-taskplan.md §0.4）：离线、零网络；临时文件一律放工作区内
``.tmp_test/``（沙箱禁止写系统 %TEMP%，S4 同坑）。

覆盖（S9 计划 §4 S9.1 验收）：
- set / get / remove / list / resolve 正确（含 normalize 精确匹配、覆盖）；
- JSON 持久化读回一致；缺失/损坏文件回退空表；
- 并发安全（多线程 set/get 不丢数据）；
- event_to_dict / event_from_dict 与 Event 结构一致（含多日子事件）。

运行：
    python -m unittest tests.test_s9_binding -v
"""

import json
import os
import shutil
import sys
import threading
import unittest
import uuid
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from songbot.models_song import Event, SubEvent  # noqa: E402
from songbot.s9_binding import BindingStore, event_from_dict, event_to_dict  # noqa: E402

TMP_ROOT = Path(_ROOT) / ".tmp_test" / "s9"
TMP_ROOT.mkdir(parents=True, exist_ok=True)


def _ws_tmp(prefix: str) -> str:
    d = os.path.join(str(TMP_ROOT), prefix + uuid.uuid4().hex[:8])
    os.makedirs(d, exist_ok=True)
    return d


def _rm_ws_tmp(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _sample_event() -> Event:
    return Event(
        title="THE IDOLM@STER MILLION LIVE! 13thLIVE",
        year="2026", date="",
        brands=["ミリオンライブ！"],
        sub_events=[
            SubEvent(title="DAY1 全力援走", full_title="…DAY1", url="http://x/million_13th_day1.html",
                     date="2026/05/05(火祝)"),
            SubEvent(title="DAY2 Grand bal masqué", full_title="…DAY2", url="http://x/million_13th_day2.html",
                     date="2026/05/06(水祝)"),
        ],
    )


class TestSerialization(unittest.TestCase):
    def test_event_roundtrip(self):
        ev = _sample_event()
        restored = event_from_dict(event_to_dict(ev))
        self.assertEqual(restored, ev)
        self.assertEqual(restored.sub_events[0].url, "http://x/million_13th_day1.html")

    def test_single_page_event_roundtrip(self):
        ev = Event(title="CINDERELLA GIRLS MUSICAL DERE of the DEAD", year="2026",
                   date="2026/07/04(土)・05(日)", brands=["シンデレラ"], url="http://x/cg_musical_dd.html")
        self.assertEqual(event_from_dict(event_to_dict(ev)), ev)

    def test_empty_sub_events(self):
        ev = Event(title="X", year="2026")
        self.assertEqual(event_from_dict(event_to_dict(ev)), ev)


class TestBindingStore(unittest.TestCase):
    def setUp(self):
        self.td = _ws_tmp("bind_")
        self.path = os.path.join(self.td, "bindings.json")

    def tearDown(self):
        _rm_ws_tmp(self.td)

    def test_set_get(self):
        store = BindingStore(path=self.path)
        store.set("13th", _sample_event())
        got = store.get("13th")
        self.assertEqual(got.title, "THE IDOLM@STER MILLION LIVE! 13thLIVE")
        self.assertEqual(got.sub_events[0].title, "DAY1 全力援走")

    def test_normalize_key(self):
        store = BindingStore(path=self.path)
        store.set("１３ｔｈ ＬＩＶＥ", _sample_event())       # 全角/空格
        self.assertIsNotNone(store.get("13th LIVE"))           # normalize 后一致
        self.assertIsNotNone(store.get("13thlive"))
        self.assertIsNone(store.get("13th"))                   # 非精确匹配不中

    def test_resolve_exact_normalize(self):
        store = BindingStore(path=self.path)
        store.set("iwsf", _sample_event())
        self.assertIsNotNone(store.resolve("I W S F"))         # 分隔符忽略后精确匹配
        self.assertIsNotNone(store.resolve("IWSF"))
        self.assertIsNone(store.resolve("IWSF2026"))           # 不子串匹配

    def test_overwrite(self):
        store = BindingStore(path=self.path)
        store.set("x", _sample_event())
        other = Event(title="另一个事件", year="2025", url="http://x/other.html")
        store.set("x", other)
        self.assertEqual(store.get("x").title, "另一个事件")
        self.assertEqual(len(store), 1)

    def test_remove(self):
        store = BindingStore(path=self.path)
        store.set("x", _sample_event())
        self.assertTrue(store.remove("X"))                     # normalize 后大小写不敏感
        self.assertFalse(store.remove("X"))                    # 已删
        self.assertIsNone(store.get("x"))

    def test_list(self):
        store = BindingStore(path=self.path)
        store.set("Zzz", _sample_event())
        store.set("aaa", _sample_event())
        items = store.list()
        self.assertEqual([a for a, _ in items], ["aaa", "Zzz"])  # casefold 排序
        self.assertEqual(items[0][1].title, _sample_event().title)

    def test_empty_store(self):
        store = BindingStore(path=self.path)
        self.assertEqual(store.list(), [])
        self.assertIsNone(store.get("x"))
        self.assertFalse(store.remove("x"))
        self.assertEqual(len(store), 0)

    def test_set_empty_alias_raises(self):
        store = BindingStore(path=self.path)
        with self.assertRaises(ValueError):
            store.set("  ・  ", _sample_event())

    def test_persistence_roundtrip(self):
        store = BindingStore(path=self.path)
        store.set("13th", _sample_event())
        store.set("dere", Event(title="DERE", year="2026", url="http://x/d.html"))
        store2 = BindingStore(path=self.path)                  # 重新加载
        self.assertEqual(len(store2), 2)
        self.assertEqual(store2.get("13th").title, "THE IDOLM@STER MILLION LIVE! 13thLIVE")
        self.assertEqual(store2.get("dere").title, "DERE")

    def test_missing_file_loads_empty(self):
        store = BindingStore(path=os.path.join(self.td, "no_such.json"))
        self.assertEqual(len(store), 0)

    def test_corrupt_file_falls_back_empty(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ not json !!")
        store = BindingStore(path=self.path)
        self.assertEqual(len(store), 0)                        # 损坏 -> 空表，不崩

    def test_concurrent_sets(self):
        store = BindingStore(path=self.path)
        n = 20
        results: list[bool] = []

        def worker(i: int) -> None:
            for k in range(5):
                store.set(f"alias{i}", Event(title=f"E{i}-{k}", year="2026"))
            results.append(True)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), n)
        self.assertEqual(len(store), n)                        # 20 个不同 key 都在
        reloaded = BindingStore(path=self.path)
        self.assertEqual(len(reloaded), n)


if __name__ == "__main__":
    unittest.main(verbosity=2)
