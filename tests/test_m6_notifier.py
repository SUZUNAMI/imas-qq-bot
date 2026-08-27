"""M6 QQ 推送 — 纯逻辑单测（不访问网络，注入 fake client / 临时配置）。

运行：python -m unittest discover -s tests -v
"""
import json
import logging
import os
import shutil
import sys
import unittest
from dataclasses import dataclass
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    import httpx  # noqa: E402
except ImportError:  # pragma: no cover — 沙箱环境 httpx 在 vendor/
    sys.path.insert(0, os.path.join(ROOT, "vendor"))
    import httpx  # noqa: E402

import m6_notifier as m6  # noqa: E402
from models import PushMessage, PushResult  # noqa: E402

# 测试临时目录放在仓库内 .tmp/（已 gitignore）——本机沙箱只允许写工作区（系统 %TEMP% 被拒）。
_TMP_BASE = os.path.join(ROOT, ".tmp", "m6_tests")


def ok_response(message_id=12345, status="ok", retcode=0):
    return FakeResponse(200, {"status": status, "retcode": retcode, "data": {"message_id": message_id}})


def fail_response(status_code=200, status="failed", retcode=100):
    return FakeResponse(status_code, {"status": status, "retcode": retcode, "message": "msg failed"})


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=None):
        self.status_code = status_code
        self._json = json_data
        self.text = text if text is not None else (
            json.dumps(json_data, ensure_ascii=False) if json_data is not None else ""
        )

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeClient:
    """记录调用并依次弹出预设响应；fail 若给出则在 post 时抛出（模拟网络错误）。"""

    def __init__(self, responses=None, fail=None):
        self.responses = list(responses or [])
        self.fail = fail
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.fail is not None:
            raise self.fail
        if self.responses:
            r = self.responses.pop(0)
            if callable(r):
                return r(self)
            return r
        return ok_response()

    def get(self, url, timeout=None):
        self.calls.append({"url": url, "get": True, "timeout": timeout})
        if self.responses:
            r = self.responses.pop(0)
            if callable(r):
                return r(self)
            return r
        return ok_response()


SAMPLE_MSG = PushMessage(
    group_ids=["123456789"],
    segments=["第一段\n\n第二段", "后续分片"],
    images=["https://example.com/a.jpg", "https://example.com/b.jpg"],
    link="https://idolmaster-official.jp/news/01_17821",
)


class TestContractReExport(unittest.TestCase):
    def test_types_come_from_models(self):
        self.assertIs(m6.PushMessage, PushMessage)
        self.assertIs(m6.PushResult, PushResult)


class TestConfigLoading(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(_TMP_BASE, ignore_errors=True)
        os.makedirs(_TMP_BASE, exist_ok=True)
        self._env_backup = {
            k: os.environ.get(k)
            for k in ("NAPCAT_BASE_URL", "NAPCAT_TOKEN", "NAPCAT_GROUP_IDS",
                      "NAPCAT_INTERVAL_SEC", "NAPCAT_MERGE_FORWARD")
        }
        for k in self._env_backup:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(_TMP_BASE, ignore_errors=True)

    def _path(self, name: str) -> str:
        return os.path.join(_TMP_BASE, name)

    def _write(self, name: str, content: str) -> str:
        p = self._path(name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
        return p

    def test_defaults_without_files(self):
        cfg = m6.load_config(config_path=self._path("nope.yaml"), env_path=self._path("nope.env"))
        self.assertEqual(cfg.base_url, m6.DEFAULT_BASE_URL)
        self.assertEqual(cfg.token, "")
        self.assertEqual(cfg.group_ids, [])
        self.assertEqual(cfg.interval_sec, 1.5)
        self.assertEqual(cfg.timeout, 15.0)
        self.assertEqual(cfg.max_retries, 1)

    def test_config_yaml_napcat_section(self):
        cfg_path = self._write("config.yaml", (
            "napcat:\n"
            '  base_url: "http://127.0.0.1:6099"\n'
            '  token: "t0k3n"\n'
            '  group_ids: ["111", "222"]\n'
            "  interval_sec: 2.5\n"
            "  timeout: 30\n"
            "  max_retries: 3\n"
        ))
        cfg = m6.load_config(config_path=cfg_path, env_path=self._path("nope.env"))
        self.assertEqual(cfg.base_url, "http://127.0.0.1:6099")
        self.assertEqual(cfg.token, "t0k3n")
        self.assertEqual(cfg.group_ids, ["111", "222"])
        self.assertEqual(cfg.interval_sec, 2.5)
        self.assertEqual(cfg.timeout, 30.0)
        self.assertEqual(cfg.max_retries, 3)

    def test_config_json_fallback(self):
        cfg_path = self._write("config.json", json.dumps({
            "napcat": {"base_url": "http://127.0.0.1:7777", "group_ids": ["333"]}
        }))
        cfg = m6.load_config(config_path=cfg_path, env_path=self._path("nope.env"))
        self.assertEqual(cfg.base_url, "http://127.0.0.1:7777")
        self.assertEqual(cfg.group_ids, ["333"])

    def test_env_file_overrides_config(self):
        cfg_path = self._write("config.yaml", 'napcat:\n  base_url: "http://cfg"\n  token: "cfg-token"\n')
        env_path = self._write(".env", "NAPCAT_BASE_URL=http://env\nNAPCAT_TOKEN=env-token\n")
        cfg = m6.load_config(config_path=cfg_path, env_path=env_path)
        self.assertEqual(cfg.base_url, "http://env")
        self.assertEqual(cfg.token, "env-token")

    def test_os_environ_wins(self):
        os.environ["NAPCAT_BASE_URL"] = "http://os"
        os.environ["NAPCAT_GROUP_IDS"] = "444, 555"
        cfg = m6.load_config(config_path=self._path("nope.yaml"), env_path=self._path("nope.env"))
        self.assertEqual(cfg.base_url, "http://os")
        self.assertEqual(cfg.group_ids, ["444", "555"])

    def test_bad_interval_keeps_default(self):
        env_path = self._write(".env", "NAPCAT_INTERVAL_SEC=abc\n")
        cfg = m6.load_config(config_path=self._path("nope.yaml"), env_path=env_path)
        self.assertEqual(cfg.interval_sec, 1.5)

    def test_merge_forward_default_false(self):
        cfg = m6.load_config(config_path=self._path("nope.yaml"), env_path=self._path("nope.env"))
        self.assertFalse(cfg.merge_forward)

    def test_merge_forward_from_yaml_true(self):
        cfg_path = self._write("config.yaml", "napcat:\n  merge_forward: true\n")
        cfg = m6.load_config(config_path=cfg_path, env_path=self._path("nope.env"))
        self.assertTrue(cfg.merge_forward)

    def test_merge_forward_env_overrides_yaml_false(self):
        cfg_path = self._write("config.yaml", "napcat:\n  merge_forward: false\n")
        env_path = self._write(".env", "NAPCAT_MERGE_FORWARD=1\n")
        cfg = m6.load_config(config_path=cfg_path, env_path=env_path)
        self.assertTrue(cfg.merge_forward)  # .env 覆盖 config.yaml


class TestInputCoercion(unittest.TestCase):
    def test_models_message_passthrough(self):
        self.assertIs(m6._coerce_message(SAMPLE_MSG), SAMPLE_MSG)

    def test_dict_input(self):
        msg = m6._coerce_message({
            "group_ids": ["1"],
            "segments": ["seg"],
            "images": [],
            "link": "http://x",
        })
        self.assertIsInstance(msg, PushMessage)
        self.assertEqual(msg.group_ids, ["1"])

    def test_heterogeneous_dataclass(self):
        @dataclass
        class Other:
            group_ids: list
            segments: list
            images: list
            link: str

        msg = m6._coerce_message(Other(["9"], ["s"], [], "http://x"))
        self.assertEqual(msg.group_ids, ["9"])
        self.assertEqual(msg.segments, ["s"])

    def test_missing_field_raises(self):
        with self.assertRaises(m6.PushError):
            m6._coerce_message({"group_ids": ["1"], "segments": ["s"], "link": "http://x"})

    def test_bad_type_raises(self):
        with self.assertRaises(m6.PushError):
            m6._coerce_message(42)

    def test_group_id_parsing(self):
        self.assertEqual(m6._parse_group_id("123456789"), 123456789)
        self.assertEqual(m6._parse_group_id("99999999999999999999"), 99999999999999999999)
        with self.assertRaises(m6.PushError):
            m6._parse_group_id("abc")
        with self.assertRaises(m6.PushError):
            m6._parse_group_id("")


class TestPushSingleGroup(unittest.TestCase):
    def test_single_group_single_segment(self):
        client = FakeClient([ok_response(777)])
        msg = PushMessage(group_ids=["123456789"], segments=["hello"], images=[], link="http://x")
        results = m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.group_id, "123456789")
        self.assertTrue(r.ok)
        self.assertEqual(r.message_id, "777")
        self.assertIsNone(r.error)
        # 请求体校验：group_id 为 int，消息段为 text
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["url"], "http://127.0.0.1:3000/send_group_msg")
        self.assertEqual(call["json"]["group_id"], 123456789)
        self.assertEqual(call["json"]["message"], [{"type": "text", "data": {"text": "hello"}}])

    def test_segments_sent_in_order(self):
        client = FakeClient([ok_response(1), ok_response(2)])
        msg = PushMessage(group_ids=["9"], segments=["甲", "乙", "丙"], images=[], link="http://x")
        results = m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertTrue(results[0].ok)
        texts = [c["json"]["message"][0]["data"]["text"] for c in client.calls]
        self.assertEqual(texts, ["甲", "乙", "丙"])

    def test_images_merged_into_one_multiple_image_message(self):
        client = FakeClient([ok_response(1), ok_response(2)])
        msg = PushMessage(group_ids=["9"], segments=["文本"], images=["http://i/1.jpg", "http://i/2.jpg"], link="http://x")
        m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertEqual(len(client.calls), 2)
        # 第一条：文本；第二条：两条 image 段合并
        self.assertEqual(client.calls[1]["json"]["message"], [
            {"type": "image", "data": {"file": "http://i/1.jpg"}},
            {"type": "image", "data": {"file": "http://i/2.jpg"}},
        ])

    def test_no_images_no_image_call(self):
        client = FakeClient([ok_response(1)])
        msg = PushMessage(group_ids=["9"], segments=["文本"], images=[], link="http://x")
        m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertEqual(len(client.calls), 1)

    def test_auth_header_when_token_set(self):
        client = FakeClient([ok_response(1)])
        msg = PushMessage(group_ids=["9"], segments=["s"], images=[], link="http://x")
        m6.push(msg, config=m6.NotifierConfig(token="secret"), client=client)
        self.assertEqual(client.calls[0]["headers"].get("Authorization"), "Bearer secret")

    def test_no_auth_header_without_token(self):
        client = FakeClient([ok_response(1)])
        msg = PushMessage(group_ids=["9"], segments=["s"], images=[], link="http://x")
        m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertNotIn("Authorization", client.calls[0]["headers"])

    def test_empty_message_defensive(self):
        client = FakeClient()
        msg = PushMessage(group_ids=["9"], segments=[], images=[], link="http://x")
        results = m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertFalse(results[0].ok)
        self.assertIn("空消息", results[0].error)
        self.assertEqual(client.calls, [])

    def test_bad_group_id_fails_that_group_only(self):
        client = FakeClient([ok_response(1)])
        msg = PushMessage(group_ids=["not-a-number", "123"], segments=["s"], images=[], link="http://x")
        results = m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertFalse(results[0].ok)
        self.assertIn("非法群号", results[0].error)
        self.assertTrue(results[1].ok)
        self.assertEqual(len(client.calls), 1)  # 只给合法群发


class TestMergeForward(unittest.TestCase):
    """合并转发（send_forward_msg）：文本段 + 图片合并为一条「合并聊天记录」。"""

    def setUp(self) -> None:
        m6._SELF_CACHE.clear()

    def test_build_forward_nodes_structure(self):
        msg = PushMessage(group_ids=["9"], segments=["第一段", "第二段"],
                          images=["http://i/1.jpg", "http://i/2.jpg"], link="http://x")
        nodes = m6._build_forward_nodes(msg, "1666562110", "時津風")
        self.assertEqual(len(nodes), 3)  # 2 个文本 node + 1 个图片 node
        self.assertEqual(nodes[0], {"type": "node", "data": {
            "uin": "1666562110", "name": "時津風",
            "content": [{"type": "text", "data": {"text": "第一段"}}],
        }})
        self.assertEqual(nodes[2]["data"]["content"], [
            {"type": "image", "data": {"file": "http://i/1.jpg"}},
            {"type": "image", "data": {"file": "http://i/2.jpg"}},
        ])

    def test_merged_push_success_single_call(self):
        client = FakeClient([ok_response(999)])
        msg = PushMessage(group_ids=["123"], segments=["段1", "段2"],
                          images=["http://i/1.jpg"], link="http://x")
        with mock.patch.object(m6, "_get_self_info", return_value=("1666562110", "時津風")):
            results = m6.push(msg, config=m6.NotifierConfig(merge_forward=True), client=client)
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].message_id, "999")
        self.assertEqual(len(client.calls), 1)  # 整条新闻只发一条合并记录
        call = client.calls[0]
        self.assertEqual(call["url"], "http://127.0.0.1:3000/send_forward_msg")
        self.assertEqual(call["json"]["group_id"], 123)
        self.assertEqual(len(call["json"]["messages"]), 3)
        self.assertEqual(call["json"]["messages"][0]["data"]["uin"], "1666562110")

    def test_merged_push_without_images(self):
        client = FakeClient([ok_response(1)])
        msg = PushMessage(group_ids=["9"], segments=["只有文本"], images=[], link="http://x")
        with mock.patch.object(m6, "_get_self_info", return_value=("1", "n")):
            m6.push(msg, config=m6.NotifierConfig(merge_forward=True), client=client)
        self.assertEqual(len(client.calls[0]["json"]["messages"]), 1)  # 只有 1 个文本 node

    def test_merged_push_failure_reported(self):
        client = FakeClient([FakeResponse(500, {"status": "failed"}),
                             FakeResponse(500, {"status": "failed"})])
        msg = PushMessage(group_ids=["9"], segments=["s"], images=[], link="http://x")
        with mock.patch.object(m6, "_get_self_info", return_value=("1", "n")):
            results = m6.push(msg, config=m6.NotifierConfig(merge_forward=True, max_retries=1),
                              client=client)
        self.assertFalse(results[0].ok)
        self.assertIn("500", results[0].error)

    def test_non_merge_still_uses_send_group_msg(self):
        client = FakeClient([ok_response(1)])
        msg = PushMessage(group_ids=["9"], segments=["s"], images=[], link="http://x")
        m6.push(msg, config=m6.NotifierConfig(merge_forward=False), client=client)
        self.assertEqual(client.calls[0]["url"], "http://127.0.0.1:3000/send_group_msg")

    def test_self_info_fetched_and_cached(self):
        client = FakeClient([
            FakeResponse(200, {"status": "ok", "retcode": 0,
                               "data": {"user_id": 1666562110, "nickname": "時津風"}}),
            ok_response(1),
        ])
        msg = PushMessage(group_ids=["9", "10"], segments=["s"], images=[], link="http://x")
        m6.push(msg, config=m6.NotifierConfig(merge_forward=True), client=client)
        get_calls = [c for c in client.calls if c.get("get")]
        self.assertEqual(len(get_calls), 1)  # 多群也只查一次（缓存）
        self.assertEqual(get_calls[0]["url"], "http://127.0.0.1:3000/get_login_info")
        post_calls = [c for c in client.calls if not c.get("get")]
        node = post_calls[0]["json"]["messages"][0]
        self.assertEqual(node["data"]["uin"], "1666562110")
        self.assertEqual(node["data"]["name"], "時津風")

    def test_self_info_fallback_on_error(self):
        client = FakeClient([FakeResponse(500, {}), ok_response(1)])
        msg = PushMessage(group_ids=["9"], segments=["s"], images=[], link="http://x")
        m6.push(msg, config=m6.NotifierConfig(merge_forward=True), client=client)
        post_calls = [c for c in client.calls if not c.get("get")]
        node = post_calls[0]["json"]["messages"][0]
        self.assertEqual(node["data"]["uin"], "10001")
        self.assertEqual(node["data"]["name"], "爱马仕新闻")


class TestRetryAndFailure(unittest.TestCase):
    def test_retry_once_then_success(self):
        # 第一次失败（HTTP 500 → 可重试），第二次成功
        client = FakeClient([FakeResponse(500, {"status": "failed"}), ok_response(42)])
        msg = PushMessage(group_ids=["9"], segments=["s"], images=[], link="http://x")
        with mock.patch.object(m6.time, "sleep"):
            results = m6.push(msg, config=m6.NotifierConfig(max_retries=1), client=client)
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].message_id, "42")
        self.assertEqual(len(client.calls), 2)

    def test_retry_exhausted_fails(self):
        client = FakeClient([FakeResponse(500, {"status": "failed"}), FakeResponse(503, {"status": "failed"})])
        msg = PushMessage(group_ids=["9"], segments=["s"], images=[], link="http://x")
        with mock.patch.object(m6.time, "sleep"):
            results = m6.push(msg, config=m6.NotifierConfig(max_retries=1), client=client)
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].message_id, "")
        self.assertIsNotNone(results[0].error)
        self.assertEqual(len(client.calls), 2)

    def test_auth_4xx_fails_fast_no_retry(self):
        client = FakeClient([FakeResponse(401, {"message": "unauthorized"})])
        msg = PushMessage(group_ids=["9"], segments=["s"], images=[], link="http://x")
        with mock.patch.object(m6.time, "sleep"):
            results = m6.push(msg, config=m6.NotifierConfig(max_retries=1), client=client)
        self.assertFalse(results[0].ok)
        self.assertIn("401", results[0].error)
        self.assertEqual(len(client.calls), 1)  # 鉴权失败不重试

    def test_unparseable_response_fails_not_raises(self):
        client = FakeClient([FakeResponse(200, json_data=None, text="<html>not json</html>")])
        msg = PushMessage(group_ids=["9"], segments=["s"], images=[], link="http://x")
        results = m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertFalse(results[0].ok)
        self.assertIn("解析失败", results[0].error)

    def test_status_failed_response(self):
        client = FakeClient([fail_response()])
        msg = PushMessage(group_ids=["9"], segments=["s"], images=[], link="http://x")
        results = m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertFalse(results[0].ok)
        self.assertIn("msg failed", results[0].error)

    def test_network_down_all_groups_fail_with_log(self):
        client = FakeClient(fail=httpx.ConnectError("connection refused"))
        msg = PushMessage(group_ids=["1", "2"], segments=["s"], images=[], link="http://x")
        with mock.patch.object(m6.time, "sleep"):
            with self.assertLogs("m6_notifier", level=logging.WARNING) as cm:
                results = m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(not r.ok for r in results))
        joined = "\n".join(cm.output)
        self.assertIn("NapCat 未连接", joined)

    def test_partial_failure_does_not_block_other_groups(self):
        # 群1 两段均 401（快速失败不重试）→ 群1 整体失败；群2 正常收到两段
        client = FakeClient([
            FakeResponse(401, {"message": "denied"}),
            FakeResponse(401, {"message": "denied"}),
            ok_response(1),
            ok_response(2),
        ])
        msg = PushMessage(group_ids=["1", "2"], segments=["s", "t"], images=[], link="http://x")
        with mock.patch.object(m6.time, "sleep"):
            results = m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertEqual(len(results), 2)
        self.assertFalse(results[0].ok)
        self.assertIn("401", results[0].error)
        self.assertTrue(results[1].ok)
        # message_id 取该群第一条成功消息的 id（群2 段A 成功 → "1"）
        self.assertEqual(results[1].message_id, "1")


class TestMultiGroup(unittest.TestCase):
    def test_each_group_gets_full_message(self):
        client = FakeClient([ok_response(1), ok_response(2), ok_response(3), ok_response(4)])
        msg = PushMessage(group_ids=["11", "22"], segments=["A", "B"], images=["http://i/1.jpg"], link="http://x")
        with mock.patch.object(m6.time, "sleep"):
            results = m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertTrue(all(r.ok for r in results))
        # 群1: A, B, images；群2: A, B, images —— 顺序按群
        groups = [c["json"]["group_id"] for c in client.calls]
        self.assertEqual(groups, [11, 11, 11, 22, 22, 22])

    def test_inter_group_sleep_uses_interval(self):
        client = FakeClient([ok_response(1), ok_response(2), ok_response(3)])
        msg = PushMessage(group_ids=["11", "22"], segments=["s"], images=[], link="http://x")
        sleeps = []
        with mock.patch.object(m6.time, "sleep", side_effect=lambda s: sleeps.append(s)):
            m6.push(msg, config=m6.NotifierConfig(interval_sec=2.5), client=client)
        # 群间 sleep 一次（最后一个群后不 sleep），值为 interval_sec
        self.assertEqual(sleeps, [2.5])

    def test_no_sleep_for_single_group(self):
        client = FakeClient([ok_response(1)])
        msg = PushMessage(group_ids=["11"], segments=["s"], images=[], link="http://x")
        sleeps = []
        with mock.patch.object(m6.time, "sleep", side_effect=lambda s: sleeps.append(s)):
            m6.push(msg, config=m6.NotifierConfig(interval_sec=2.5), client=client)
        self.assertEqual(sleeps, [])

    def test_group_ids_from_config_when_message_empty(self):
        client = FakeClient([ok_response(1)])
        msg = PushMessage(group_ids=[], segments=["s"], images=[], link="http://x")
        results = m6.push(msg, config=m6.NotifierConfig(group_ids=["99"]), client=client)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].group_id, "99")

    def test_no_groups_returns_empty(self):
        client = FakeClient()
        msg = PushMessage(group_ids=[], segments=["s"], images=[], link="http://x")
        with self.assertLogs("m6_notifier", level=logging.WARNING):
            results = m6.push(msg, config=m6.NotifierConfig(), client=client)
        self.assertEqual(results, [])
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
