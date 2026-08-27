"""S5 事件接收 + 会话 — 歌曲列表 bot（songbot）.

接收 NapCat 推送的 OneBot 11 群消息事件（HTTP POST 上报到本地接收器），
识别 ``@bot``，维护两段交互会话状态（``(group_id, user_id) -> context``，默认 5 分钟 TTL）。

全部标准库实现（``http.server`` / ``threading`` / ``json``），零第三方依赖。

模块组成
--------
- ``Incoming``       一条已解析的群消息（group_id / user_id / at_bot / 纯文本 text）
- ``parse_event``    纯函数：OneBot 事件 dict -> Incoming | None
- ``SessionStore``   线程安全会话表：set / get / clear，TTL 惰性清理，clock 可注入（单测 mock 时间）
- ``EventReceiver``  本地 HTTP 服务：POST /event 读 JSON -> parse_event -> **立即回 200**
  （不阻塞 NapCat 上报）-> 回调在请求线程执行（ThreadingHTTPServer = 线程池语义）

OneBot 事件形态（messagePostFormat=array，NapCat 配置见主仓库 docs/modules/M6-napcat-setup.md）::

    {"post_type": "message", "message_type": "group",
     "group_id": 827029417, "user_id": 123456, "self_id": 1666562110,
     "message": [{"type": "at", "data": {"qq": "1666562110"}},
                 {"type": "text", "data": {"text": "DAY1"}}],
     "raw_message": "[CQ:at,qq=1666562110]DAY1"}

- ``message`` 为 array：逐段解析——``at`` 且 ``data.qq == self_id`` -> at_bot；``text`` 拼接正文。
- ``message`` 为 string（或缺失，回退 ``raw_message``）：正则识别 ``[CQ:at,qq=...]`` 并去掉全部 CQ 码。

设计决策（2026-08-27，详见 docs/modules/S5-receiver-worklog.md §3）
- 无文本的群消息（纯图片/表情/仅 @ 无文字）-> ``parse_event`` 返回 None（bot 只响应带文字的群消息）。
- ``self_id`` 取自事件 payload（NapCat 每事件自带）；缺失时 at 识别关闭（at_bot=False）。
- 会话键 ``(group_id, user_id)`` 统一字符串化；get 时惰性清理过期项；读写全程加锁防并发串线。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

logger = logging.getLogger("songbot.s5_receiver")

# ---------------------------------------------------------------------------
# 常量（选择器/地址集中，改动只改这里）
# ---------------------------------------------------------------------------
DEFAULT_HOST = "127.0.0.1"        # 仅本机监听（NapCat 同机上报）
DEFAULT_PORT = 8090               # NapCat postUrls 指向: http://127.0.0.1:8090/event
DEFAULT_TTL_SEC = 300.0           # 会话默认 5 分钟（施工图 §S5）
EVENT_PATH = "/event"             # 上报路径

# CQ 码（message 为 string / raw_message 形态时用）
_CQ_AT_RE = re.compile(r"\[CQ:at,qq=([0-9]+)[^\]]*\]")   # 兼容 at 后带 name 等参数
_CQ_CODE_RE = re.compile(r"\[CQ:[^\]]*\]")


# ---------------------------------------------------------------------------
# 数据契约：一条群消息事件（已解析，去掉 CQ 码）
# ---------------------------------------------------------------------------
@dataclass
class Incoming:
    """一条群消息事件（S6 主控处理链的输入）。"""

    group_id: str                 # 群号（字符串化）
    user_id: str                  # 发送者 QQ（字符串化）
    at_bot: bool                  # 是否 @ 了 bot（self_id 比对）
    text: str                     # 去掉 CQ 码后的纯文本正文
    role: str = "member"          # 发送者在群内角色：owner（群主）/ administrator（管理员）/ member（普通成员）；缺失默认 member（最安全）


# ---------------------------------------------------------------------------
# 纯解析（不联网，便于离线单测）
# ---------------------------------------------------------------------------
def parse_event(payload: dict) -> Optional[Incoming]:
    """OneBot 11 群消息事件 -> Incoming；非群消息 / 无文本 / 缺字段返回 None。

    :param payload: NapCat HTTP POST 上报的完整事件 dict（JSON 反序列化后）
    :return: 群文本消息对应的 Incoming；不满足条件返回 None（调用方直接忽略）
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("post_type") != "message" or payload.get("message_type") != "group":
        return None
    group_id = str(payload.get("group_id") or "")
    user_id = str(payload.get("user_id") or "")
    if not group_id or not user_id:
        return None
    self_id = str(payload.get("self_id") or "")

    # 发送者角色（OneBot 11：payload.sender.role ∈ owner/administrator/member）。
    # 缺失/非法值一律回退 "member"（权限控制默认收紧，防止绕过）。
    sender = payload.get("sender")
    role = (sender or {}).get("role") if isinstance(sender, dict) else None
    role = str(role or "").strip().casefold()
    if role not in ("owner", "administrator", "member"):
        role = "member"

    message = payload.get("message")
    if isinstance(message, list):
        # messagePostFormat=array：逐段解析
        at_bot = False
        parts: list[str] = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type")
            data = seg.get("data")
            data = data if isinstance(data, dict) else {}
            if seg_type == "at":
                qq = str(data.get("qq") or "")
                if qq and self_id and qq == self_id:
                    at_bot = True
            elif seg_type == "text":
                text = data.get("text")
                if text:
                    parts.append(str(text))
        text = "".join(parts).strip()
    else:
        # string 形态（或缺失）：raw_message 正则提取
        raw = message if isinstance(message, str) else payload.get("raw_message", "")
        raw = str(raw or "")
        at_bot = bool(self_id) and any(qq == self_id for qq in _CQ_AT_RE.findall(raw))
        text = _CQ_CODE_RE.sub("", raw).strip()

    if not text:
        # 无文本的群消息（纯图片/表情/仅 @ 无文字）不产生 Incoming
        return None
    return Incoming(group_id=group_id, user_id=user_id, at_bot=at_bot, text=text, role=role)


# ---------------------------------------------------------------------------
# 会话表：线程安全，TTL 惰性清理，clock 可注入
# ---------------------------------------------------------------------------
class SessionStore:
    """线程安全会话表：``(group_id, user_id) -> context``，TTL 惰性清理。

    - ``set`` 覆盖式写入并重置截止时间；``get`` 命中且未过期返回 context，过期即删；
    - ``clear`` 删除；``cleanup`` 显式清理过期项（返回清理条数）；
    - 键统一字符串化；``clock`` 可注入（单测用假时钟推进时间验证 TTL）。
    """

    def __init__(self, ttl: float = DEFAULT_TTL_SEC, clock: Callable[[], float] = time.time):
        self._ttl = float(ttl)
        self._clock = clock
        self._data: dict[tuple[str, str], tuple[object, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(group_id, user_id) -> tuple[str, str]:
        return (str(group_id), str(user_id))

    def set(self, group_id, user_id, context) -> None:
        """写入/覆盖会话（重置截止时间）。"""
        key = self._key(group_id, user_id)
        with self._lock:
            self._data[key] = (context, self._clock() + self._ttl)

    def get(self, group_id, user_id):
        """读取会话；不存在或已过期返回 None（过期项顺手删除）。"""
        key = self._key(group_id, user_id)
        now = self._clock()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            context, deadline = entry
            if deadline <= now:
                del self._data[key]
                return None
            return context

    def clear(self, group_id, user_id) -> None:
        """删除会话（不存在时静默）。"""
        with self._lock:
            self._data.pop(self._key(group_id, user_id), None)

    def cleanup(self) -> int:
        """显式清理全部过期项，返回清理条数（get 已惰性清理，本方法供批量/维护用）。"""
        now = self._clock()
        expired: list[tuple[str, str]] = []
        with self._lock:
            for key, (_, deadline) in self._data.items():
                if deadline <= now:
                    expired.append(key)
            for key in expired:
                del self._data[key]
        return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


# ---------------------------------------------------------------------------
# 本地 HTTP 接收服务（标准库 http.server，零第三方依赖）
# ---------------------------------------------------------------------------
def _make_handler(receiver: "EventReceiver") -> type[BaseHTTPRequestHandler]:
    """为每个 EventReceiver 实例生成专属 Handler 类（闭包绑定 receiver）。"""

    class _Handler(BaseHTTPRequestHandler):
        """POST /event：JSON -> parse_event -> 立即回 200 -> 回调（请求线程内执行）。"""

        MAX_BODY = 10 * 1024 * 1024   # 请求体读取上限（防异常超大上报撑爆内存）

        def _send(self, code: int, body: bytes = b"") -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _read_body(self) -> bytes:
            """读取请求体（兼容三种形态，2026-08-27 S6 live 实测补强）。

            - 有 Content-Length：按长度读（常规 JSON POST）；
            - Transfer-Encoding: chunked：按 chunk 解析（NapCat httpClients 上报用）；
            - 两者皆无：读到 EOF（兜底，上限 MAX_BODY）。
            """
            te = (self.headers.get("Transfer-Encoding") or "").lower()
            cl = self.headers.get("Content-Length")
            if cl:
                try:
                    n = int(cl)
                except ValueError:
                    n = 0
                return self.rfile.read(min(n, self.MAX_BODY)) if n > 0 else b""
            if "chunked" in te:
                chunks: list[bytes] = []
                total = 0
                while total < self.MAX_BODY:
                    line = self.rfile.readline(1024).strip()
                    try:
                        size = int(line.split(b";", 1)[0], 16)
                    except ValueError:
                        break
                    if size <= 0:
                        self.rfile.readline(1024)   # 吃掉 chunked 结束空行
                        break
                    chunk = self.rfile.read(size)
                    if len(chunk) < size:
                        break
                    chunks.append(chunk)
                    total += size
                    self.rfile.readline(1024)       # 吃 chunk 尾 CRLF
                return b"".join(chunks)
            return self.rfile.read(self.MAX_BODY)

        def do_POST(self) -> None:
            if self.path != EVENT_PATH:
                self._send(404, b"not found")
                return
            raw = b""
            try:
                raw = self._read_body()
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception as exc:
                # JSON 解析失败等 -> 400，不影响 NapCat 继续上报
                logger.warning("事件体解析失败: %s (path=%s, body=%d bytes)",
                               exc, self.path, len(raw))
                self._send(400, b"bad request")
                return
            incoming = parse_event(payload)
            self._send(200, b"ok")          # 先回 200，不阻塞上报
            if incoming is not None:
                try:
                    receiver._dispatch(incoming)   # 回调异常只记日志，不回错误给上报方
                except Exception:
                    logger.exception("事件回调处理异常: %r", incoming)

        def do_GET(self) -> None:
            # 健康检查：验收/排障用（acceptance_s5.py --listen 常驻时探测）
            if self.path in ("/healthz", "/health"):
                self._send(200, b"ok")
            else:
                self._send(404, b"not found")

        def log_message(self, fmt: str, *args) -> None:
            # 静默默认访问日志（避免刷 stderr），走 logger 便于统一日志
            logger.debug("http: " + fmt, *args)

    return _Handler


class EventReceiver:
    """本地 OneBot 事件接收服务（ThreadingHTTPServer：每请求一线程 = 线程池语义）。

    :param handler: 回调 ``callable(Incoming)``（S6 主控注入处理链）
    :param host: 监听地址（默认 127.0.0.1，仅本机）
    :param port: 监听端口（默认 8090；测试/验收传 0 自动分配）
    """

    def __init__(self, handler: Callable[[Incoming], None], *,
                 host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self._handler = handler
        self.host = host
        self.port = port
        self._bound_port: int = port
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def bound_port(self) -> int:
        """实际绑定端口（port=0 时由系统分配；stop 后仍返回最后一次绑定值）。"""
        return self._bound_port

    def start(self) -> "EventReceiver":
        """启动服务（幂等：已启动则直接返回）。"""
        if self._server is not None:
            return self
        self._server = ThreadingHTTPServer((self.host, self.port), _make_handler(self))
        self._bound_port = self._server.server_address[1]
        self._server.daemon_threads = True       # 回调线程不阻塞退出
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("S5 接收器已启动: http://%s:%d%s", self.host, self.bound_port, EVENT_PATH)
        return self

    def stop(self) -> None:
        """停止服务并回收端口（幂等）。"""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _dispatch(self, incoming: Incoming) -> None:
        """把已解析事件交给用户回调（请求线程内执行）。"""
        self._handler(incoming)

    def __enter__(self) -> "EventReceiver":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# 命令行自测/验收演示：python -m songbot.s5_receiver [--port 0] [--listen]
# ---------------------------------------------------------------------------
def _main(argv: Optional[list[str]] = None) -> int:
    """自测演示：启动接收器 -> 模拟 POST 两条事件（@bot+文字 / 无@+文字）-> 打印解析结果。

    --listen: 模拟 POST 后不退出，常驻监听（配合 http://127.0.0.1:<port>/healthz 排障/联调）。
    """
    import argparse
    import urllib.request

    parser = argparse.ArgumentParser(
        prog="s5_receiver", description="S5 事件接收器自测（模拟 OneBot 群消息 POST）")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=0,
                        help=f"监听端口（默认 0=自动分配；生产默认 {DEFAULT_PORT}）")
    parser.add_argument("--listen", action="store_true", help="模拟 POST 后常驻监听")
    args = parser.parse_args(argv)

    seen: list[Incoming] = []

    def on_incoming(inc: Incoming) -> None:
        seen.append(inc)
        print(f"[recv] group={inc.group_id} user={inc.user_id} at_bot={inc.at_bot} text={inc.text!r}")

    with EventReceiver(on_incoming, host=args.host, port=args.port) as receiver:
        port = receiver.bound_port
        base = f"http://{args.host}:{port}"
        print(f"接收器已启动: {base}{EVENT_PATH}")

        def post(payload: dict) -> int:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{base}{EVENT_PATH}", data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status

        # 1) 第一段交互：@bot + Live 名
        s1 = post({"post_type": "message", "message_type": "group",
                   "group_id": 827029417, "user_id": 123456, "self_id": 1666562110,
                   "message": [{"type": "at", "data": {"qq": "1666562110"}},
                               {"type": "text", "data": {"text": "IWSF2026"}}],
                   "raw_message": "[CQ:at,qq=1666562110]IWSF2026"})
        # 2) 第二段确认：无 @ + 公演名（走会话）
        s2 = post({"post_type": "message", "message_type": "group",
                   "group_id": 827029417, "user_id": 123456, "self_id": 1666562110,
                   "message": [{"type": "text", "data": {"text": "DAY1"}}],
                   "raw_message": "DAY1"})
        time.sleep(0.3)  # 等回调线程打印（daemon 线程）

        ok = (s1, s2) == (200, 200) and len(seen) == 2 \
            and seen[0].at_bot and seen[0].text == "IWSF2026" \
            and not seen[1].at_bot and seen[1].text == "DAY1"
        print(f"模拟 POST 应答: {s1} / {s2}；回调收到 {len(seen)} 条 -> {'[PASS]' if ok else '[FAIL]'}")

        if args.listen:
            print("常驻监听中…（Ctrl+C 退出）")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n收到 Ctrl+C，退出")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main())
