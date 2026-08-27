"""M7 主控 — 纯逻辑单测（不访问网络：企划选择解析 / 上次记忆 / 单轮流水线 mock / 本地留档 / 配置）。

运行：python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import main as m7  # noqa: E402
from m6_notifier import NotifierConfig  # noqa: E402
from models import NewsDetail, NewsItem, PushMessage, PushResult, TranslationResult  # noqa: E402

# 测试临时目录放在仓库内 .tmp/（已 gitignore）——本机沙箱只允许写工作区（系统 %TEMP% 被拒）。
_TMP_BASE = os.path.join(ROOT, ".tmp", "m7_tests")

_CODES = list(m7.BRAND_CODES)


def _item(nid="01_10001") -> NewsItem:
    return NewsItem(id=nid, url=f"https://idolmaster-official.jp/news/{nid}",
                    title="【イベント】テスト", date="2026-08-26")


def _detail(nid="01_10001") -> NewsDetail:
    return NewsDetail(id=nid, url=f"https://idolmaster-official.jp/news/{nid}",
                      title="【イベント】テスト", date="2026-08-26",
                      body_text="第一段落。\n\n第二段落。", images=[])


def _tr() -> TranslationResult:
    return TranslationResult(title_zh="【活动】测试", body_zh="第一段。\n\n第二段。")


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(api_key="sk-test", group_ids=["111", "222"])


def _ok_push(*a, **k):
    return [PushResult(group_id="111", ok=True, message_id="m1")]


class BrandInputTests(unittest.TestCase):
    def test_valid_multi(self):
        self.assertEqual(m7.parse_brand_input("1,5,6", _CODES),
                         ["IDOLMASTER", "SHINYCOLORS", "GAKUEN"])

    def test_valid_with_chinese_comma_and_spaces(self):
        self.assertEqual(m7.parse_brand_input(" 2 ， 3 ", _CODES),
                         ["CINDERELLAGIRLS", "MILLIONLIVE"])

    def test_zero_or_all_means_all(self):
        for raw in ("0", "all", "ALL", "全部"):
            self.assertIsNone(m7.parse_brand_input(raw, _CODES), raw)

    def test_invalid_inputs(self):
        for raw in ("abc", "9", "1,99", "1,x", "0,1", "-1", "1.5"):
            self.assertEqual(m7.parse_brand_input(raw, _CODES), [], raw)

    def test_dedup_keep_order(self):
        self.assertEqual(m7.parse_brand_input("3,1,3,1", _CODES),
                         ["MILLIONLIVE", "IDOLMASTER"])

    def test_empty_returns_empty(self):
        # 空输入由 select_brands 处理为沿用默认；parse 层返回 []
        self.assertEqual(m7.parse_brand_input("", _CODES), [])


class BrandsMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(_TMP_BASE, "memory")
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.makedirs(self.tmp, exist_ok=True)
        self._file_patch = mock.patch.object(m7, "BRANDS_FILE", Path(self.tmp) / "m7_brands.json")
        self._file_patch.start()
        self.addCleanup(self._file_patch.stop)

    def test_roundtrip(self):
        m7._save_last_brands(["SHINYCOLORS", "GAKUEN"])
        self.assertEqual(m7._load_last_brands(), ["SHINYCOLORS", "GAKUEN"])

    def test_save_all_means_none(self):
        m7._save_last_brands(None)
        self.assertIsNone(m7._load_last_brands())

    def test_missing_file(self):
        self.assertIsNone(m7._load_last_brands())

    def test_corrupt_file(self):
        with open(m7.BRANDS_FILE, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        self.assertIsNone(m7._load_last_brands())

    def test_unknown_codes_filtered(self):
        m7._save_last_brands(["SHINYCOLORS", "NOPE", "gakuen"])
        self.assertEqual(m7._load_last_brands(), ["SHINYCOLORS", "GAKUEN"])


class OrchestratorConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(_TMP_BASE, "orch_cfg")
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.makedirs(self.tmp, exist_ok=True)

    def test_load_orchestrator_section(self):
        path = os.path.join(self.tmp, "config.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# comment\norchestrator:\n  poll_interval_sec: 120\n"
                     "  api_base: https://x/api/\n")
        cfg = m7.load_orchestrator_config(path)
        self.assertEqual(cfg.get("poll_interval_sec"), 120)
        self.assertEqual(cfg.get("api_base"), "https://x/api/")

    def test_missing_file_returns_empty(self):
        self.assertEqual(m7.load_orchestrator_config(os.path.join(self.tmp, "nope.yaml")), {})

    def test_no_orchestrator_section(self):
        path = os.path.join(self.tmp, "config.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("napcat:\n  base_url: http://127.0.0.1:3000\n")
        self.assertEqual(m7.load_orchestrator_config(path), {})

    def test_bad_yaml_returns_empty(self):
        path = os.path.join(self.tmp, "config.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("orchestrator:\n   not valid : : :\n")
        # 解析器容错：非法行跳过，仍返回可读部分或空
        self.assertIsInstance(m7.load_orchestrator_config(path), dict)


class RunOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(_TMP_BASE, "pipeline")
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.makedirs(self.tmp, exist_ok=True)
        self.db_path = os.path.join(self.tmp, "state.db")
        m7._TRANSLATE_FAILED.clear()  # 进程内失败记忆，避免跨测试污染

    def _run(self, archive_dir=None, api_base=None, min_updated=None, **patches):
        """以 patch 字典 {'attr': value} 方式运行 run_once，返回 (stats, patchers)。"""
        patchers = {k: mock.patch.object(m7, k, v) for k, v in patches.items()}
        for p in patchers.values():
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers.values()])
        stats = m7.run_once(
            ["SHINYCOLORS"],
            limit=5, max_len=3500,
            translator_cfg=_cfg(), notifier_cfg=_cfg(),
            dry_run=False, db_path=self.db_path,
            archive_dir=archive_dir, api_base=api_base, min_updated=min_updated,
        )
        return stats, patchers

    def test_no_new_items(self):
        stats, _ = self._run(fetch_news_list=lambda *a, **k: [],
                             get_new_items=lambda *a, **k: [],
                             get_unpushed=lambda *a, **k: [])
        self.assertEqual(stats["fetched"], 0)
        self.assertEqual(stats["new"], 0)
        self.assertEqual(stats["errors"], 0)

    def test_api_base_passed_to_fetcher(self):
        item = _item()
        captured = {}

        def fake_fetch(limit, brands=None, api_base=None, min_updated=None):
            captured["api_base"] = api_base
            return [item]

        self._run(
            api_base="https://mirror.example/api/",
            fetch_news_list=fake_fetch,
            get_new_items=lambda *a, **k: [item],
            parse_detail=lambda *a, **k: _detail(),
            translate=lambda *a, **k: _tr(),
            push=_ok_push,
            get_unpushed=lambda *a, **k: [],
        )
        self.assertEqual(captured["api_base"], "https://mirror.example/api/")

    def test_min_updated_passed_to_fetcher(self):
        item = _item()
        captured = {}

        def fake_fetch(limit, brands=None, api_base=None, min_updated=None):
            captured["min_updated"] = min_updated
            return [item]

        self._run(
            min_updated=12345,
            fetch_news_list=fake_fetch,
            get_new_items=lambda *a, **k: [item],
            parse_detail=lambda *a, **k: _detail(),
            translate=lambda *a, **k: _tr(),
            push=_ok_push,
            get_unpushed=lambda *a, **k: [],
        )
        self.assertEqual(captured["min_updated"], 12345)

    def test_suppress_preexisting_unpushed(self):
        """启动截断：历史遗留未推送条目标记为已处理（跳过补推）。"""
        item = _item()
        with mock.patch.object(m7, "get_unpushed", return_value=[item]), \
             mock.patch.object(m7, "mark_pushed") as mark:
            n = m7._suppress_preexisting_unpushed(self.db_path)
        self.assertEqual(n, 1)
        mark.assert_called_once_with("01_10001", self.db_path)

    def test_suppress_preexisting_unpushed_empty(self):
        with mock.patch.object(m7, "get_unpushed", return_value=[]):
            self.assertEqual(m7._suppress_preexisting_unpushed(self.db_path), 0)

    def test_full_pipeline_success(self):
        item, detail, tr = _item(), _detail(), _tr()
        with mock.patch.object(m7, "mark_pushed") as mark, \
             mock.patch.object(m7, "record_push_result") as record:
            stats, _ = self._run(
                fetch_news_list=lambda *a, **k: [item],
                get_new_items=lambda *a, **k: [item],
                parse_detail=lambda *a, **k: detail,
                translate=lambda *a, **k: tr,
                push=_ok_push,
                get_unpushed=lambda *a, **k: [],
            )
        self.assertEqual(stats, {"fetched": 1, "new": 1, "pushed_ok": 1,
                                 "pushed_fail": 0, "errors": 0})
        mark.assert_called_once_with("01_10001", self.db_path)
        record.assert_called_once()
        r = record.call_args.args[0]
        self.assertTrue(r.ok)

    def test_push_failure_not_marked(self):
        item, detail, tr = _item(), _detail(), _tr()
        with mock.patch.object(m7, "mark_pushed") as mark:
            stats, _ = self._run(
                fetch_news_list=lambda *a, **k: [item],
                get_new_items=lambda *a, **k: [item],
                parse_detail=lambda *a, **k: detail,
                translate=lambda *a, **k: tr,
                push=lambda *a, **k: [PushResult(group_id="111", ok=False, message_id="", error="boom")],
                get_unpushed=lambda *a, **k: [],
            )
        self.assertEqual(stats["pushed_fail"], 1)
        self.assertEqual(stats["errors"], 1)
        mark.assert_not_called()

    def test_unpushed_retry(self):
        """历史失败条目经 get_unpushed 补救成功 -> 计入成功并回写。"""
        item, detail, tr = _item(), _detail(), _tr()
        with mock.patch.object(m7, "mark_pushed") as mark:
            stats, _ = self._run(
                fetch_news_list=lambda *a, **k: [],
                get_new_items=lambda *a, **k: [],
                get_unpushed=lambda *a, **k: [item],
                parse_detail=lambda *a, **k: detail,
                translate=lambda *a, **k: tr,
                push=_ok_push,
            )
        self.assertEqual(stats["pushed_ok"], 1)
        mark.assert_called_once_with("01_10001", self.db_path)

    def test_translate_failure_pushes_original(self):
        """翻译失败 -> 留档原文(带 translation_error) + 直发原文并附失败说明，计入成功。"""
        item, detail = _item(), _detail()
        captured = {}

        def fake_push(msg, **kw):
            captured["msg"] = msg
            return [PushResult(group_id="111", ok=True, message_id="m1")]

        with mock.patch.object(m7, "archive_news") as archive, \
             mock.patch.object(m7, "mark_pushed") as mark:
            stats, _ = self._run(
                archive_dir=Path(self.tmp) / "archive",
                fetch_news_list=lambda *a, **k: [item],
                get_new_items=lambda *a, **k: [item],
                parse_detail=lambda *a, **k: detail,
                translate=mock.Mock(side_effect=RuntimeError("no key")),
                push=fake_push,
                get_unpushed=lambda *a, **k: [],
            )
        self.assertEqual(stats["pushed_ok"], 1)
        self.assertEqual(stats["errors"], 0)
        mark.assert_called_once_with("01_10001", self.db_path)
        archive.assert_called_once()
        self.assertIsNone(archive.call_args.args[2])  # tr=None
        self.assertIn("translation_error", archive.call_args.kwargs)
        # 推送的是原文直发消息：含原标题 + 翻译失败说明
        full = "\n".join(captured["msg"].segments)
        self.assertIn("【イベント】テスト", full)
        self.assertIn("第一段落。", full)
        self.assertIn("翻译失败", full)
        self.assertIn("原文直发", full)

    def test_translate_failure_remembered_skip_retry(self):
        """同一条翻译失败被进程内记忆：下轮不再调翻译 API，直接直发原文。"""
        item, detail = _item(), _detail()
        translate_mock = mock.Mock(side_effect=RuntimeError("no key"))
        for _ in range(2):
            self._run(
                archive_dir=Path(self.tmp) / "archive",
                fetch_news_list=lambda *a, **k: [item],
                get_new_items=lambda *a, **k: [item],
                parse_detail=lambda *a, **k: detail,
                translate=translate_mock,
                push=_ok_push,
                get_unpushed=lambda *a, **k: [],
            )
        self.assertEqual(translate_mock.call_count, 1)  # 第二轮跳过重试

    def test_translate_failure_fallback_push_fails(self):
        """直发原文也推送失败（如 NapCat 断开）-> 计入失败、不回写，留给下轮补救。"""
        item, detail = _item(), _detail()
        with mock.patch.object(m7, "mark_pushed") as mark:
            stats, _ = self._run(
                archive_dir=Path(self.tmp) / "archive",
                fetch_news_list=lambda *a, **k: [item],
                get_new_items=lambda *a, **k: [item],
                parse_detail=lambda *a, **k: detail,
                translate=mock.Mock(side_effect=RuntimeError("no key")),
                push=lambda *a, **k: [PushResult(group_id="111", ok=False, message_id="", error="net")],
                get_unpushed=lambda *a, **k: [],
            )
        self.assertEqual(stats["pushed_fail"], 1)
        self.assertEqual(stats["errors"], 1)
        mark.assert_not_called()


class NotifyTests(unittest.TestCase):
    """开机/关机状态通知（orchestrator.notify_groups）。"""

    def test_load_notify_groups_list(self):
        self.assertEqual(m7._load_notify_groups({"notify_groups": ["1", "2"]}), ["1", "2"])

    def test_load_notify_groups_json_array_string(self):
        # YAML 子集解析器把 ["450599137"] 解析为裸字符串，需归一化
        self.assertEqual(m7._load_notify_groups({"notify_groups": '["450599137"]'}), ["450599137"])

    def test_load_notify_groups_comma_string(self):
        self.assertEqual(m7._load_notify_groups({"notify_groups": "111, 222"}), ["111", "222"])

    def test_load_notify_groups_empty_or_missing(self):
        self.assertEqual(m7._load_notify_groups({}), [])
        self.assertEqual(m7._load_notify_groups({"notify_groups": []}), [])
        self.assertEqual(m7._load_notify_groups({"notify_groups": ""}), [])

    def test_send_notification_plain_text_no_merge(self):
        sent = {}

        def fake_push(msg, **kw):
            sent["msg"] = msg
            sent["cfg"] = kw["config"]
            return [PushResult(group_id="450599137", ok=True, message_id="m1")]

        with mock.patch.object(m7, "push", fake_push):
            m7._send_notification(NotifierConfig(group_ids=["9"]), ["450599137"], "🤖 测试通知")
        self.assertIn("🤖 测试通知", sent["msg"].segments[0])
        self.assertEqual(sent["msg"].group_ids, ["450599137"])
        self.assertFalse(sent["cfg"].merge_forward)  # 通知用普通文本，不包合并记录

    def test_send_notification_empty_groups_noop(self):
        with mock.patch.object(m7, "push") as p:
            m7._send_notification(NotifierConfig(), [], "x")
        p.assert_not_called()

    def test_send_notification_failure_does_not_raise(self):
        with mock.patch.object(m7, "push", side_effect=RuntimeError("net down")):
            m7._send_notification(NotifierConfig(), ["1"], "x")  # 只告警不抛出

    def test_render_template_fills_placeholders(self):
        text = m7._render_template("🤖 {a} 来了\n{b} 秒", a="M7", b=60)
        self.assertEqual(text, "🤖 M7 来了\n60 秒")

    def test_render_template_unknown_placeholder_kept(self):
        text = m7._render_template("开机：{brands}（{nope}）", brands="SHINYCOLORS")
        self.assertEqual(text, "开机：SHINYCOLORS（{nope}）")  # 不崩溃，未知占位符保留

    def test_notify_template_default_when_missing(self):
        self.assertEqual(m7._notify_template({}, "notify_startup", "默认文案"), "默认文案")
        self.assertEqual(m7._notify_template({"notify_startup": "  "}, "notify_startup", "默认文案"), "默认文案")

    def test_notify_template_unescapes_newline(self):
        orch = {"notify_startup": "第一行\\n第二行"}
        self.assertEqual(m7._notify_template(orch, "notify_startup", "默认"), "第一行\n第二行")

    def test_notify_template_uses_config_value(self):
        orch = {"notify_startup": "🤖 已启动 {brands}"}
        self.assertEqual(m7._notify_template(orch, "notify_startup", "默认"), "🤖 已启动 {brands}")


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(_TMP_BASE, "archive")
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.makedirs(self.tmp, exist_ok=True)

    def test_archive_with_translation_by_date_folder(self):
        item, detail, tr = _item("01_20001"), _detail("01_20001"), _tr()
        target = m7.archive_news(item, detail, tr, base_dir=Path(self.tmp))
        self.assertEqual(target, Path(self.tmp) / "2026-08-26" / "01_20001")
        original = (target / "原文.md").read_text(encoding="utf-8")
        self.assertIn("【イベント】テスト", original)
        self.assertIn("第一段落。", original)
        self.assertIn(detail.url, original)
        self.assertIn("【活动】测试", (target / "译文.md").read_text(encoding="utf-8"))
        meta = json.loads((target / "meta.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["translated"])
        self.assertEqual(meta["id"], "01_20001")

    def test_archive_without_translation_records_error(self):
        item, detail = _item(), _detail()
        target = m7.archive_news(item, detail, None, base_dir=Path(self.tmp),
                                 translation_error="RuntimeError: no key")
        self.assertFalse((target / "译文.md").exists())
        meta = json.loads((target / "meta.json").read_text(encoding="utf-8"))
        self.assertFalse(meta["translated"])
        self.assertEqual(meta["translation_error"], "RuntimeError: no key")

    def test_archive_empty_date_falls_back(self):
        item = NewsItem(id="01_30001", url="https://x/01_30001", title="t", date="")
        detail = NewsDetail(id="01_30001", url="https://x/01_30001", title="t", date="",
                            body_text="b", images=[])
        target = m7.archive_news(item, detail, None, base_dir=Path(self.tmp))
        self.assertEqual(len(target.parent.name), 10)  # YYYY-MM-DD
        self.assertEqual(target.name, "01_30001")

    def test_images_download_once_and_skip_existing(self):
        item, detail = _item(), _detail()
        detail.images = ["https://cmsapi.example/Image/get?path=uploads/a/b.jpg"]
        with mock.patch.object(m7, "_download_image") as dl:
            dl.side_effect = lambda url, dest: (dest.write_bytes(b"fake-jpg") or True)
            m7.archive_news(item, detail, _tr(), base_dir=Path(self.tmp))
            m7.archive_news(item, detail, _tr(), base_dir=Path(self.tmp))  # 第二次应跳过
        self.assertEqual(dl.call_count, 1)  # 只下载一次（幂等）
        img = Path(self.tmp) / "2026-08-26" / "01_10001" / "images" / "01.jpg"
        self.assertTrue(img.exists() and img.stat().st_size > 0)

    def test_guess_image_ext(self):
        self.assertEqual(m7._guess_image_ext(
            "https://cmsapi.example/Image/get?path=uploads/a/b.png"), ".png")
        self.assertEqual(m7._guess_image_ext("https://x/y.webp"), ".webp")
        self.assertEqual(m7._guess_image_ext("https://x/noext"), ".jpg")


if __name__ == "__main__":
    unittest.main()
