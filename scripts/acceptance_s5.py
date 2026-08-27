"""S5 acceptance checks: 事件解析 + 会话 TTL + 本地 HTTP 接收（全部离线，无需 NapCat）。

用法：
    python scripts/acceptance_s5.py [--listen] [--port 0]
- 模拟 POST 两条 OneBot 群消息事件（@bot+IWSF2026 / 无@+DAY1）-> 校验 200 应答与回调解析；
- SessionStore set/get/clear + TTL 过期（注入假时钟）；
- --listen：模拟 POST 后常驻监听（配合 http://127.0.0.1:<port>/healthz 做排障/联调）。

说明：真实「群内 @bot」联调属于 S6（需 NapCat 配 postUrls 指向本接收器 + 测试群），
本脚本只验收 S5 本身（S5 验收清单：模拟 POST 事件被正确解析；会话 set/get/超时通过）。
"""
import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "vendor"))

from songbot.s5_receiver import (  # noqa: E402
    DEFAULT_PORT,
    EVENT_PATH,
    EventReceiver,
    SessionStore,
    parse_event,
)

SELF_ID = "1666562110"      # bot 小号 QQ（与主项目 M6 一致：時津風）
GROUP_ID = "827029417"      # 测试群
USER_ID = "123456789"


def check_parse() -> None:
    """事件解析：array / string 两种形态 + 非群消息/无文本返回 None。"""
    inc = parse_event({"post_type": "message", "message_type": "group",
                       "group_id": GROUP_ID, "user_id": USER_ID, "self_id": SELF_ID,
                       "message": [{"type": "at", "data": {"qq": SELF_ID}},
                                   {"type": "text", "data": {"text": "IWSF2026"}}]})
    assert inc is not None and inc.at_bot and inc.text == "IWSF2026", f"array 形态解析异常: {inc!r}"

    inc2 = parse_event({"post_type": "message", "message_type": "group",
                        "group_id": GROUP_ID, "user_id": USER_ID, "self_id": SELF_ID,
                        "message": f"[CQ:at,qq={SELF_ID}]13thLIVE"})
    assert inc2 is not None and inc2.at_bot and inc2.text == "13thLIVE", f"string 形态解析异常: {inc2!r}"

    assert parse_event({"post_type": "notice", "message_type": "group",
                        "group_id": GROUP_ID, "user_id": USER_ID}) is None, "非消息事件应返回 None"
    assert parse_event({"post_type": "message", "message_type": "group",
                        "group_id": GROUP_ID, "user_id": USER_ID, "self_id": SELF_ID,
                        "message": [{"type": "image", "data": {"file": "x.png"}}]}) is None, \
        "无文本消息应返回 None"
    print("[parse] array/string 形态正确；非群消息/无文本 -> None → PASS")


def check_session() -> None:
    """会话 set/get/clear + TTL 过期（注入假时钟，离线）。"""
    class FakeClock:
        def __init__(self):
            self.now = 1000.0

        def __call__(self) -> float:
            return self.now

    clock = FakeClock()
    store = SessionStore(ttl=300, clock=clock)
    store.set(GROUP_ID, USER_ID, {"stage": "candidates"})
    assert store.get(GROUP_ID, USER_ID) == {"stage": "candidates"}, "set/get 未命中"
    clock.now += 299
    assert store.get(GROUP_ID, USER_ID) is not None, "未过期应命中"
    clock.now += 2
    assert store.get(GROUP_ID, USER_ID) is None, "过期应失效（TTL 300s）"
    store.set(GROUP_ID, USER_ID, "ctx")
    store.clear(GROUP_ID, USER_ID)
    assert store.get(GROUP_ID, USER_ID) is None, "clear 后应无会话"
    print("[session] set/get/clear + TTL 过期 → PASS")


def check_http(host: str, port: int, listen: bool) -> None:
    """本地 HTTP 接收：模拟 POST 两条事件，校验 200 应答与回调解析。"""
    seen: list = []

    def on_incoming(inc) -> None:
        seen.append(inc)
        print(f"[recv] group={inc.group_id} user={inc.user_id} at_bot={inc.at_bot} text={inc.text!r}")

    with EventReceiver(on_incoming, host=host, port=port) as receiver:
        port = receiver.bound_port
        base = f"http://{host}:{port}"
        print(f"[http] 接收器已启动: {base}{EVENT_PATH}")

        def post(payload: dict) -> int:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{base}{EVENT_PATH}", data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status

        # 1) 第一段交互：@bot + Live 名
        s1 = post({"post_type": "message", "message_type": "group",
                   "group_id": GROUP_ID, "user_id": USER_ID, "self_id": SELF_ID,
                   "message": [{"type": "at", "data": {"qq": SELF_ID}},
                               {"type": "text", "data": {"text": "IWSF2026"}}],
                   "raw_message": f"[CQ:at,qq={SELF_ID}]IWSF2026"})
        # 2) 第二段确认：无 @ + 公演名（S6 走会话）
        s2 = post({"post_type": "message", "message_type": "group",
                   "group_id": GROUP_ID, "user_id": USER_ID, "self_id": SELF_ID,
                   "message": [{"type": "text", "data": {"text": "DAY1"}}],
                   "raw_message": "DAY1"})
        time.sleep(0.3)  # 等回调线程打印（daemon 线程）
        assert (s1, s2) == (200, 200), f"POST 应答异常: {s1}/{s2}"
        assert len(seen) == 2, f"回调应收到 2 条，实际 {len(seen)}"
        inc1, inc2 = seen
        assert inc1.at_bot and inc1.text == "IWSF2026", f"第一条解析异常: {inc1!r}"
        assert not inc2.at_bot and inc2.text == "DAY1", f"第二条解析异常: {inc2!r}"
        print("[http] 模拟 POST 两条事件：200 应答 + 回调解析正确 → PASS")

        if listen:
            print(f"[http] 常驻监听中…（健康检查 http://{host}:{port}/healthz，Ctrl+C 退出）")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[http] 收到 Ctrl+C，退出")


def main() -> int:
    parser = argparse.ArgumentParser(description="S5 事件接收+会话验收（离线，无需 NapCat）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0,
                        help=f"监听端口（默认 0=自动分配；生产默认 {DEFAULT_PORT}）")
    parser.add_argument("--listen", action="store_true", help="模拟 POST 后常驻监听")
    args = parser.parse_args()

    check_parse()
    check_session()
    check_http(args.host, args.port, args.listen)
    print("[ALL PASS]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
