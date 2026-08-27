"""M3 增量检测 + 状态库（StateStore）— 单测。

覆盖 M3 规格（docs/modules/M3-state-store.md）§7 全部 5 条验收标准 + 边界与可拓展性行为。
运行：python -m unittest discover -s tests -v
"""
import os
import shutil
import sqlite3
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from m3_store import get_new_items, get_unpushed, init_db, mark_pushed, record_push_result
from models import NewsItem, PushResult

# 测试临时目录放在仓库内 .tmp/（已 gitignore）：
# ① 本机沙箱只允许写工作区（系统 %TEMP% 被拒）；② 服务器上同样成立，可移植。
# 注意：不要用 tempfile.TemporaryDirectory —— 本机沙箱下其创建目录的内部写入
# （sqlite/mkdir/rmtree）会被拒（WinError 5 / unable to open database file），
# 普通 os.makedirs 目录则完全正常（见 .tmp/probe_plain.py 验证）。
_TMP_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tmp", "m3_tests")


def _items(*ids: str) -> list[NewsItem]:
    """按给定 id 序列构造 NewsItem（标题/日期固定，仅 id 区分）。"""
    return [
        NewsItem(
            id=i,
            url=f"https://idolmaster-official.jp/news/{i}",
            title=f"标题{i}",
            date="2026-08-26",
        )
        for i in ids
    ]


class M3StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(_TMP_BASE, ignore_errors=True)  # 每个测试独立全新目录
        os.makedirs(_TMP_BASE, exist_ok=True)
        self.db = os.path.join(_TMP_BASE, "state.db")

    def tearDown(self) -> None:
        shutil.rmtree(_TMP_BASE, ignore_errors=True)

    def _seen_rows(self) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM seen_items ORDER BY id").fetchall()
        conn.close()
        return rows

    # --- 验收 1：首喂 10 条 → 10 新增；再喂同样 10 条 → 0 --------------------
    def test_first_feed_all_new_then_none(self) -> None:
        items = _items(*[f"01_{i:05d}" for i in range(10)])
        self.assertEqual(len(get_new_items(items, self.db)), 10)
        self.assertEqual(get_new_items(items, self.db), [])

    # --- 验收 2：8 旧 + 2 新 → 只返回 2 新，顺序与输入一致 --------------------
    def test_only_new_returned_order_preserved(self) -> None:
        old = _items("01_00001", "01_00002")
        get_new_items(old, self.db)
        mixed = old + _items("01_00003", "01_00004")
        new = get_new_items(mixed, self.db)
        self.assertEqual([n.id for n in new], ["01_00003", "01_00004"])

    # --- 验收 3：mark_pushed 后 pushed_at 非空；get_unpushed 不再含该条 --------
    def test_mark_pushed_sets_timestamp(self) -> None:
        get_new_items(_items("01_00001", "01_00002"), self.db)
        mark_pushed("01_00001", self.db)
        rows = {r["id"]: r for r in self._seen_rows()}
        self.assertIsNotNone(rows["01_00001"]["pushed_at"])
        self.assertIsNone(rows["01_00002"]["pushed_at"])
        unpushed = get_unpushed(self.db)
        self.assertEqual([n.id for n in unpushed], ["01_00002"])

    # --- 验收 4：关闭重开后状态仍在（数据持久化） -----------------------------
    def test_persistence_across_connections(self) -> None:
        get_new_items(_items("01_00001"), self.db)
        # 模拟进程重启：全新连接读取同一文件，重复喂同一批 → 无新增
        self.assertEqual(get_new_items(_items("01_00001"), self.db), [])
        self.assertEqual(len(self._seen_rows()), 1)

    # --- 验收 5：并发/快速连调不重复插入报错（每个 id 恰好被一个线程认领） -----
    def test_concurrent_claims_each_id_once(self) -> None:
        items = _items(*[f"01_{i:05d}" for i in range(20)])
        n_threads, results, lock = 4, [], threading.Lock()
        barrier = threading.Barrier(n_threads)

        def worker() -> None:
            barrier.wait()  # 同时起跑，制造竞争
            got = get_new_items(items, self.db)
            with lock:
                results.append(got)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        claimed = [n.id for batch in results for n in batch]
        self.assertEqual(len(claimed), len(set(claimed)), "同一 id 被重复返回")
        self.assertEqual(len(claimed), len(items), "每个 id 恰好被认领一次")
        self.assertEqual(len(self._seen_rows()), len(items))

    # --- 推送结果落库：ok True/False → 1/0 -----------------------------------
    def test_record_push_result(self) -> None:
        record_push_result(PushResult(group_id="123", ok=True, message_id="m1"), "01_00001", self.db)
        record_push_result(
            PushResult(group_id="123", ok=False, message_id="", error="boom"), "01_00002", self.db
        )
        conn = sqlite3.connect(self.db)
        rows = conn.execute(
            "SELECT news_id, group_id, ok, message_id, error FROM push_log ORDER BY id"
        ).fetchall()
        conn.close()
        self.assertEqual(
            rows,
            [
                ("01_00001", "123", 1, "m1", None),
                ("01_00002", "123", 0, "", "boom"),
            ],
        )

    # --- 边界：空列表 --------------------------------------------------------
    def test_empty_items(self) -> None:
        self.assertEqual(get_new_items([], self.db), [])
        self.assertEqual(get_unpushed(self.db), [])

    # --- 幂等：init_db 可重复调用；mark_pushed 对未知 id 静默 no-op ------------
    def test_idempotent_helpers(self) -> None:
        init_db(self.db)
        init_db(self.db)  # 重复建库不报错
        mark_pushed("01_99999", self.db)  # 未知 id no-op
        get_new_items(_items("01_00001"), self.db)
        mark_pushed("01_00001", self.db)
        mark_pushed("01_00001", self.db)  # 重复置位不报错
        rows = self._seen_rows()
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["pushed_at"])

    # --- 可拓展性：环境变量覆盖默认路径（自动建父目录） ------------------------
    def test_default_path_env_override(self) -> None:
        env_db = os.path.join(_TMP_BASE, "env", "state.db")
        os.environ["STATE_DB_PATH"] = env_db
        try:
            get_new_items(_items("01_00001"))  # 不传 db_path，走环境变量
            self.assertTrue(os.path.exists(env_db))
        finally:
            os.environ.pop("STATE_DB_PATH", None)

    # --- 可拓展性：get_unpushed 按首次见到时间升序（旧条目优先补救） ------------
    def test_unpushed_ordered_by_first_seen(self) -> None:
        get_new_items(_items("01_00001"), self.db)
        get_new_items(_items("01_00002"), self.db)  # 后见到
        self.assertEqual([n.id for n in get_unpushed(self.db)], ["01_00001", "01_00002"])
        mark_pushed("01_00001", self.db)
        self.assertEqual([n.id for n in get_unpushed(self.db)], ["01_00002"])


if __name__ == "__main__":
    unittest.main()
