"""S5 单测：事件接收 + 会话（songbot.s5_receiver）.

约定（docs/S1-S7-taskplan.md §0.4）：解析类单测不联网（本文件全部离线）。
覆盖：
- parse_event：array / string 两种 message 形态、at 识别（self_id 比对）、文本拼接、
  非群消息 / 无文本 / 缺字段 -> None；
- SessionStore：set/get/clear、TTL 过期（注入假时钟）、惰性清理、并发 smoke；
- EventReceiver：本机回环 port=0 起真实 HTTP 服务，httpx 模拟 POST，验证
  200 应答 + 回调收到 Incoming、400 坏 JSON、404 错误路径、健康检查、上下文管理器。

运行（本机无 pytest，pip 被拦截，用标准库 unittest）：
    python -m unittest tests.test_s5_receiver -v
    （或 python -m unittest discover -s tests -p "test_s*.py" -v，全量回归）
"""

import os
import sys
import threading
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

import httpx  # noqa: E402

from songbot.s5_receiver import (  # noqa: E402
    EVENT_PATH,
    EventReceiver,
    Incoming,
    SessionStore,
    parse_event,
)

SELF_ID = "1666562110"
GROUP_ID = "827029417"
USER_ID = "123456789"


def _event(**overrides) -> dict:
    """构造一条标准的 OneBot 11 群消息事件（array 形态，@bot + 文字）。"""
    payload = {
        "post_type": "message",
        "message_type": "group",
        "group_id": GROUP_ID,
        "user_id": USER_ID,
        "self_id": SELF_ID,
        "message": [
            {"type": "at", "data": {"qq": SELF_ID}},
            {"type": "text", "data": {"text": "IWSF2026"}},
        ],
        "raw_message": f"[CQ:at,qq={SELF_ID}]IWSF2026",
    }
    payload.update(overrides)
    return payload


class TestParseEventArray(unittest.TestCase):
    """message 为 array 形态（messagePostFormat=array）。"""

    def test_at_bot_with_text(self):
        inc = parse_event(_event())
        self.assertIsNotNone(inc)
        assert inc is not None
        self.assertEqual(inc.group_id, GROUP_ID)
        self.assertEqual(inc.user_id, USER_ID)
        self.assertTrue(inc.at_bot)
        self.assertEqual(inc.text, "IWSF2026")

    def test_at_other_user_not_bot(self):
        inc = parse_event(_event(message=[
            {"type": "at", "data": {"qq": "999999"}},
            {"type": "text", "data": {"text": "hi"}},
        ]))
        assert inc is not None
        self.assertFalse(inc.at_bot)
        self.assertEqual(inc.text, "hi")

    def test_multiple_at_one_is_self(self):
        inc = parse_event(_event(message=[
            {"type": "at", "data": {"qq": "999999"}},
            {"type": "at", "data": {"qq": SELF_ID}},
            {"type": "text", "data": {"text": "DAY1"}},
        ]))
        assert inc is not None
        self.assertTrue(inc.at_bot)

    def test_at_all_is_not_bot(self):
        inc = parse_event(_event(message=[
            {"type": "at", "data": {"qq": "all"}},
            {"type": "text", "data": {"text": "大家"}},
        ]))
        assert inc is not None
        self.assertFalse(inc.at_bot)

    def test_text_segments_joined_skip_non_text(self):
        inc = parse_event(_event(message=[
            {"type": "face", "data": {"id": "1"}},
            {"type": "text", "data": {"text": "第一段"}},
            {"type": "image", "data": {"file": "x.png"}},
            {"type": "text", "data": {"text": "第二段"}},
        ]))
        assert inc is not None
        self.assertEqual(inc.text, "第一段第二段")
        self.assertFalse(inc.at_bot)

    def test_at_bot_only_no_text_is_none(self):
        self.assertIsNone(parse_event(_event(
            message=[{"type": "at", "data": {"qq": SELF_ID}}])))

    def test_int_ids_coerced_to_str(self):
        inc = parse_event(_event(
            group_id=827029417, user_id=123456789, self_id=1666562110,
            message=[{"type": "at", "data": {"qq": 1666562110}},
                     {"type": "text", "data": {"text": "13thLIVE"}}]))
        assert inc is not None
        self.assertEqual(inc.group_id, GROUP_ID)
        self.assertEqual(inc.user_id, USER_ID)
        self.assertTrue(inc.at_bot)

    def test_missing_self_id_at_not_detected(self):
        inc = parse_event(_event(
            self_id="",
            message=[{"type": "at", "data": {"qq": SELF_ID}},
                     {"type": "text", "data": {"text": "x"}}]))
        assert inc is not None
        self.assertFalse(inc.at_bot)
        self.assertEqual(inc.text, "x")


class TestParseEventRole(unittest.TestCase):
    """sender.role 解析（S9 权限控制：管理命令仅 owner/administrator 可用）。"""

    def test_owner(self):
        inc = parse_event(_event(sender={"user_id": int(USER_ID), "role": "owner"}))
        assert inc is not None
        self.assertEqual(inc.role, "owner")

    def test_administrator(self):
        inc = parse_event(_event(sender={"user_id": int(USER_ID), "role": "administrator"}))
        assert inc is not None
        self.assertEqual(inc.role, "administrator")

    def test_member(self):
        inc = parse_event(_event(sender={"user_id": int(USER_ID), "role": "member"}))
        assert inc is not None
        self.assertEqual(inc.role, "member")

    def test_missing_sender_defaults_member(self):
        # 无 sender / sender 缺 role -> 默认 member（权限收紧，防绕过）
        inc = parse_event(_event())
        assert inc is not None
        self.assertEqual(inc.role, "member")
        inc2 = parse_event(_event(sender={}))
        assert inc2 is not None
        self.assertEqual(inc2.role, "member")

    def test_sender_not_dict_defaults_member(self):
        inc = parse_event(_event(sender="owner"))
        assert inc is not None
        self.assertEqual(inc.role, "member")

    def test_invalid_role_defaults_member(self):
        inc = parse_event(_event(sender={"user_id": int(USER_ID), "role": "mod"}))
        assert inc is not None
        self.assertEqual(inc.role, "member")

    def test_role_case_insensitive(self):
        inc = parse_event(_event(sender={"user_id": int(USER_ID), "role": "OWNER"}))
        assert inc is not None
        self.assertEqual(inc.role, "owner")

    def test_string_message_keeps_role(self):
        inc = parse_event(_event(message="DAY1",
                                 sender={"user_id": int(USER_ID), "role": "administrator"}))
        assert inc is not None
        self.assertEqual(inc.role, "administrator")


class TestParseEventString(unittest.TestCase):
    """message 为 string 形态（或缺省走 raw_message），正则提取 CQ 码。"""

    def test_string_message_at_bot(self):
        inc = parse_event(_event(message=f"[CQ:at,qq={SELF_ID}]DAY1"))
        assert inc is not None
        self.assertTrue(inc.at_bot)
        self.assertEqual(inc.text, "DAY1")

    def test_raw_message_fallback_with_extra_at_params(self):
        # message 非 list 非 str -> 用 raw_message；at 后带 name 参数也应识别
        inc = parse_event(_event(
            message=None,
            raw_message=f"[CQ:at,qq={SELF_ID},name=bot] [CQ:face,id=1] 13thLIVE"))
        assert inc is not None
        self.assertTrue(inc.at_bot)
        self.assertEqual(inc.text, "13thLIVE")

    def test_string_other_user_not_bot(self):
        inc = parse_event(_event(message="[CQ:at,qq=999999]hi"))
        assert inc is not None
        self.assertFalse(inc.at_bot)
        self.assertEqual(inc.text, "hi")

    def test_empty_string_message_is_none(self):
        self.assertIsNone(parse_event(_event(message="", raw_message="")))


class TestParseEventNone(unittest.TestCase):
    """非群消息 / 无文本 / 缺字段 / 非 dict -> None。"""

    def test_not_dict(self):
        for bad in (None, [], "hello", 42):
            self.assertIsNone(parse_event(bad))

    def test_post_type_not_message(self):
        self.assertIsNone(parse_event({
            "post_type": "notice", "message_type": "group",
            "group_id": GROUP_ID, "user_id": USER_ID,
            "message": [{"type": "text", "data": {"text": "x"}}]}))
        self.assertIsNone(parse_event({
            "post_type": "request", "message_type": "group",
            "group_id": GROUP_ID, "user_id": USER_ID}))

    def test_private_message(self):
        self.assertIsNone(parse_event(_event(message_type="private")))

    def test_missing_group_or_user(self):
        self.assertIsNone(parse_event(_event(group_id="")))
        self.assertIsNone(parse_event(_event(user_id="")))
        self.assertIsNone(parse_event(_event(group_id=None, user_id=None)))

    def test_no_text_image_only(self):
        self.assertIsNone(parse_event(_event(
            message=[{"type": "image", "data": {"file": "x.png"}}])))

    def test_blank_text(self):
        self.assertIsNone(parse_event(_event(
            message=[{"type": "text", "data": {"text": "   "}}])))


class FakeClock:
    """可推进的假时钟（SessionStore 注入用）。"""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class TestSessionStore(unittest.TestCase):
    def test_set_get_clear(self):
        store = SessionStore()
        store.set(GROUP_ID, USER_ID, {"stage": "candidates"})
        self.assertEqual(store.get(GROUP_ID, USER_ID), {"stage": "candidates"})
        store.clear(GROUP_ID, USER_ID)
        self.assertIsNone(store.get(GROUP_ID, USER_ID))

    def test_missing_returns_none(self):
        self.assertIsNone(SessionStore().get(GROUP_ID, USER_ID))

    def test_keys_scoped_by_group_and_user(self):
        store = SessionStore()
        store.set(GROUP_ID, "u1", "ctx-u1")
        store.set(GROUP_ID, "u2", "ctx-u2")
        store.set("G2", "u1", "ctx-g2")
        self.assertEqual(store.get(GROUP_ID, "u1"), "ctx-u1")
        self.assertEqual(store.get(GROUP_ID, "u2"), "ctx-u2")
        self.assertEqual(store.get("G2", "u1"), "ctx-g2")

    def test_ttl_expiry_fake_clock(self):
        clock = FakeClock()
        store = SessionStore(ttl=300, clock=clock)
        store.set(GROUP_ID, USER_ID, "ctx")
        clock.now += 299
        self.assertEqual(store.get(GROUP_ID, USER_ID), "ctx")   # 未过期
        clock.now += 2
        self.assertIsNone(store.get(GROUP_ID, USER_ID))          # 过期即失效

    def test_set_refreshes_deadline(self):
        clock = FakeClock()
        store = SessionStore(ttl=300, clock=clock)
        store.set(GROUP_ID, USER_ID, "v1")
        clock.now += 200
        store.set(GROUP_ID, USER_ID, "v2")    # 覆盖写入并重置截止时间
        clock.now += 200
        self.assertEqual(store.get(GROUP_ID, USER_ID), "v2")    # 距上次 set 200s < 300s
        clock.now += 150
        self.assertIsNone(store.get(GROUP_ID, USER_ID))          # 350s > 300s

    def test_cleanup_returns_count_and_removes(self):
        clock = FakeClock()
        store = SessionStore(ttl=300, clock=clock)
        store.set(GROUP_ID, USER_ID, "a")
        store.set(GROUP_ID, "u2", "b")
        clock.now += 301
        self.assertEqual(store.cleanup(), 2)
        self.assertEqual(len(store), 0)
        store.set(GROUP_ID, USER_ID, "c")
        self.assertEqual(store.cleanup(), 0)
        self.assertEqual(len(store), 1)

    def test_override_ttl_real_clock(self):
        store = SessionStore(ttl=0.001)
        store.set(GROUP_ID, USER_ID, "x")
        time.sleep(0.01)
        self.assertIsNone(store.get(GROUP_ID, USER_ID))

    def test_concurrent_smoke(self):
        store = SessionStore()
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                for _ in range(200):
                    key = f"u{i % 5}"
                    store.set(GROUP_ID, key, i)
                    assert store.get(GROUP_ID, key) == i
                    store.clear(GROUP_ID, key)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])


class TestEventReceiverHTTP(unittest.TestCase):
    """本地 HTTP 服务：本机回环 port=0 起真实服务 + httpx 模拟 POST（离线）。"""

    def _start(self, callback) -> EventReceiver:
        receiver = EventReceiver(callback, port=0).start()
        self.addCleanup(receiver.stop)
        return receiver

    def _url(self, receiver: EventReceiver, path: str = EVENT_PATH) -> str:
        return f"http://127.0.0.1:{receiver.bound_port}{path}"

    def test_post_event_200_and_dispatches(self):
        received: list[Incoming] = []
        done = threading.Event()

        def cb(inc: Incoming) -> None:
            received.append(inc)
            done.set()

        receiver = self._start(cb)
        resp = httpx.post(self._url(receiver), json=_event())
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(done.wait(timeout=5), "回调应在 200 应答后执行")
        self.assertEqual(len(received), 1)
        self.assertTrue(received[0].at_bot)
        self.assertEqual(received[0].text, "IWSF2026")

    def test_non_group_event_200_but_no_dispatch(self):
        received: list[Incoming] = []
        receiver = self._start(received.append)
        resp = httpx.post(self._url(receiver), json=_event(post_type="notice"))
        self.assertEqual(resp.status_code, 200)
        time.sleep(0.2)
        self.assertEqual(received, [])

    def test_no_text_event_200_but_no_dispatch(self):
        received: list[Incoming] = []
        receiver = self._start(received.append)
        resp = httpx.post(self._url(receiver), json=_event(
            message=[{"type": "image", "data": {"file": "x.png"}}]))
        self.assertEqual(resp.status_code, 200)
        time.sleep(0.2)
        self.assertEqual(received, [])

    def test_invalid_json_400(self):
        receiver = self._start(lambda inc: None)
        resp = httpx.post(self._url(receiver), content=b"{not json",
                          headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 400)

    def test_wrong_path_404(self):
        receiver = self._start(lambda inc: None)
        resp = httpx.post(self._url(receiver, "/other"), json={})
        self.assertEqual(resp.status_code, 404)

    def test_healthz(self):
        receiver = self._start(lambda inc: None)
        resp = httpx.get(self._url(receiver, "/healthz"))
        self.assertEqual(resp.status_code, 200)

    def test_context_manager_stops_server(self):
        received: list[Incoming] = []
        with EventReceiver(received.append, port=0) as receiver:
            url = self._url(receiver)          # 退出前先记住 URL（stop 后端口已关闭）
            resp = httpx.post(url, json=_event())
            self.assertEqual(resp.status_code, 200)
        # 退出后端口已关闭：再次连接应失败（Windows 回环上可能是拒绝或超时，两者均可）
        with self.assertRaises((httpx.ConnectError, httpx.ConnectTimeout)):
            httpx.post(url, json=_event(), timeout=2.0)

    def test_callback_exception_does_not_break_response(self):
        def boom(inc: Incoming) -> None:  # noqa: ARG001
            raise RuntimeError("boom")

        receiver = self._start(boom)
        resp = httpx.post(self._url(receiver), json=_event())
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
