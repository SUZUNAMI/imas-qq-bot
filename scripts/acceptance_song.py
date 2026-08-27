"""S6 acceptance checks: 主控两段交互全链路（离线默认 + --live）。

离线（默认，零网络/QQ）：
    1. fixture 索引 + MockTransport 详情抓取 + 渲染（默认 mock PNG，--real-render 用真实 Edge）+ capture 发送，
       跑通：名称唯一多日 -> 子列表+会话；无@ DAY1 -> 抓详情+渲染+发图+清会话；
       时间筛选 -> 列表+候选会话；序号取候选；单页事件直接发图；无命中提示；
    2. 起真实 EventReceiver + 模拟 HTTP POST 两条事件（@bot / 无@），端到端验证 200 + 回调；
    3. **S8 song 流程**：迷你歌曲索引（3 fixture 构建）+ patch fetch_events（增量刷新零抓取），
       跑通：@bot song 唯一 -> LIVE 列表 + 会话 -> 回复序号 -> 发图；多候选 -> 候选歌 -> 选歌 -> LIVE 列表。

    注：真实渲染（playwright → Edge）在 DSH 沙箱内被拒（Node 驱动走命名管道），
    沙箱环境默认用 mock 渲染器验证链条；真实渲染由 S4 验收产物 + live 验收（Phase B）覆盖。

--live：真实索引 + 真实渲染 + 真实 NapCat 发送，常驻等待群内验收。
    前置（Phase B）：
      - NapCat OneBot HTTP 3000 在线（Desktop 启动 bot）；
      - OneBot 配置追加 postUrls: ["http://127.0.0.1:8090/event"]（NapCat WebUI API）；
      - 测试群（666 群 827029417）内发 @bot 消息验收。

用法：
    python scripts/acceptance_song.py                 # 离线全链路（mock 渲染）
    python scripts/acceptance_song.py --real-render   # 离线全链路（真实 Edge 渲染，非沙箱环境）
    python scripts/acceptance_song.py --live [--group 827029417] [--port 8090]
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "vendor"))

import httpx  # noqa: E402

import songbot.bot as bot_mod  # noqa: E402
from songbot.bot import BotConfig, SongBot, load_bot_config  # noqa: E402
from songbot.s1_fetch_events import PAGE_BASE_URL, parse_events_html  # noqa: E402
from songbot.s2_fetch_setlist import parse_setlist_html  # noqa: E402
from songbot.s4_render import render_setlist  # noqa: E402
from songbot.s5_receiver import EVENT_PATH, EventReceiver, Incoming  # noqa: E402
from songbot.s8_song_index import build_song_index  # noqa: E402

SELF_ID = "1666562110"
GROUP_ID = "827029417"      # 测试群（QQ 群 live 测试约定：默认只发测试群）
USER_ID = "123456789"
FIXTURES = Path(ROOT) / "fixtures"

# S8 迷你索引：详情 URL 末尾文件名 -> fixture 详情页
_SONG_PAGES = {
    "iwsf_day1.html": "imas_db_iwsf_day1.html",
    "million_13th_day1.html": "imas_db_million_13th_day1.html",
    "cg_musical_dd.html": "imas_db_cg_musical_dd.html",
}


def _inc(at_bot: bool, text: str) -> Incoming:
    return Incoming(group_id=GROUP_ID, user_id=USER_ID, at_bot=at_bot, text=text)


def _fixture_index() -> list:
    html = (FIXTURES / "imas_db_song_event.html").read_text(encoding="utf-8")
    return parse_events_html(html, base_url=PAGE_BASE_URL)


def _transport() -> httpx.Client:
    """MockTransport：详情 URL -> fixture 页（离线）。"""
    pages = {
        "iwsf_day1.html": "imas_db_iwsf_day1.html",
        "million_13th_day1.html": "imas_db_million_13th_day1.html",
        "cg_musical_dd.html": "imas_db_cg_musical_dd.html",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, fixture in pages.items():
            if url.rstrip("/").endswith(key):
                text = (FIXTURES / fixture).read_text(encoding="utf-8")
                return httpx.Response(200, text=text)
        return httpx.Response(404, text="not found")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _mock_renderer(sl, *, out_dir=None):
    """mock 渲染：写一个非空占位 PNG（沙箱内真实渲染被拒时的回退）。"""
    base = Path(out_dir) if out_dir else Path("data") / "songbot_img" / time.strftime("%Y%m%d_%H%M%S")
    base.mkdir(parents=True, exist_ok=True)
    p = base / "setlist_mock.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 256)
    return [p]


def _capture_bot(real_render: bool = False) -> tuple[SongBot, list]:
    """离线 bot：fixture 索引 + MockTransport 抓取 + capture 发送。

    :param real_render: True 用真实 Edge 渲染（非沙箱环境）；False 用 mock 渲染器
    """
    sends: list[tuple] = []

    def sender(group_id: str, text: str, image_paths) -> bool:
        paths = [str(p) for p in (image_paths or [])]
        sends.append((str(group_id), text, paths))
        print(f"    [send] 群 {group_id} | 文本 {len(text)} 字 | 图片 {len(paths)} 张")
        for p in paths:
            print(f"      -> {p}（{os.path.getsize(p) if os.path.isfile(p) else 0} bytes）")
        return True

    renderer = render_setlist if real_render else _mock_renderer
    if not real_render:
        print("    [renderer] mock 渲染器（沙箱内真实渲染被拒；--real-render 用真实 Edge）")
    return SongBot(events=_fixture_index(), setlist_client=_transport(),
                   renderer=renderer, sender=sender), sends


# ---------------------------------------------------------------------------
# S8 迷你歌曲索引（离线）
# ---------------------------------------------------------------------------
def _song_events() -> list:
    """只保留详情 URL 能映射到 fixture 的事件（IWSF / 13thLIVE / DERE）。"""
    out = []
    for ev in _fixture_index():
        urls = [ev.url] if ev.url else [s.url for s in ev.sub_events]
        if any(any(u.rstrip("/").endswith(k) for k in _SONG_PAGES) for u in urls):
            out.append(ev)
    return out


def _local_setlist_fetch(url: str):
    for key, name in _SONG_PAGES.items():
        if url.rstrip("/").endswith(key):
            html = (FIXTURES / name).read_text(encoding="utf-8")
            return parse_setlist_html(html, url=url)
    raise FileNotFoundError(f"no fixture for {url}")


def _song_capture_bot(real_render: bool = False) -> tuple[SongBot, list]:
    """S8 测试 bot：迷你歌曲索引 + capture 发送 + patch fetch_events（增量刷新零抓取）。

    显式传 ``config=BotConfig()``：不继承 config.yaml 真实缓存路径（防写坏 data/）。
    """
    sends: list[tuple] = []

    def sender(group_id: str, text: str, image_paths) -> bool:
        paths = [str(p) for p in (image_paths or [])]
        sends.append((str(group_id), text, paths))
        print(f"    [send] 群 {group_id} | 文本 {len(text)} 字 | 图片 {len(paths)} 张")
        return True

    renderer = render_setlist if real_render else _mock_renderer
    bot = SongBot(
        config=BotConfig(),
        events=_fixture_index(),
        setlist_client=_transport(),
        renderer=renderer,
        sender=sender,
        song_index=build_song_index(_song_events(), _local_setlist_fetch),
    )
    return bot, sends


def check_song_flow(real_render: bool = False) -> None:
    """S8：@bot song 唯一 -> LIVE 列表（2 场）+ 会话；回复序号 -> 发图 + 清会话。"""
    bot, sends = _song_capture_bot(real_render)
    with mock.patch("songbot.bot.fetch_events", return_value=_song_events()):
        bot.handle(_inc(True, "song Dance in the Light"))
    assert len(sends) == 1, f"应回复 1 条，实际 {len(sends)}"
    text = sends[0][1]
    assert "「Dance in the Light」出现在 2 场 LIVE" in text, f"LIVE 列表缺失: {text[:160]}"
    assert "IDOL WORLD SUPER FESTIVAL 2026" in text and "MILLION LIVE! 13thLIVE" in text, \
        f"LIVE 列表缺命中: {text[:200]}"
    ctx = bot.session.get(GROUP_ID, USER_ID)
    assert ctx and ctx["kind"] == "song_lives" and len(ctx["lives"]) == 2, f"会话异常: {ctx}"
    print("[S8-song] @bot song Dance in the Light -> 2 场 LIVE 列表 + 会话 → PASS")
    sends.clear()
    bot.handle(_inc(False, "1"))                          # 首个 LIVE = IWSF day1
    assert len(sends) == 1, f"应发图 1 次，实际 {len(sends)}"
    assert sends[0][1] == f"[CQ:at,qq={USER_ID}]" and len(sends[0][2]) == 1, \
        f"应只发 @归属 + 1 张图: {sends[0]}"
    assert os.path.getsize(sends[0][2][0]) > 0, "PNG 为空"
    assert bot.session.get(GROUP_ID, USER_ID) is None, "会话应已清空"
    print("[S8-song] 回复序号 -> 抓详情 + 渲染 PNG + 发图 + 清会话 → PASS")


def check_song_candidates(real_render: bool = False) -> None:
    """S8：多候选 -> 候选歌列表 + 会话；选歌 -> LIVE 列表。"""
    bot, sends = _song_capture_bot(real_render)
    from songbot.models_song import Appearance, SongEntry
    from songbot.s8_song_index import SongIndex
    idx = SongIndex()
    idx.entries["brandnewwave"] = SongEntry(title="Brand New Wave!", appearances=[
        Appearance(event_title="EV1", event_year="2026", sub_title="", date="2026/01/01(木)",
                   url="http://x/ev1.html")])
    idx.entries["brandnew"] = SongEntry(title="Brand New!!", appearances=[
        Appearance(event_title="EV2", event_year="2026", sub_title="DAY1", date="2026/02/01(日)",
                   url="http://x/ev2.html")])
    idx.source_urls = {u for ev in _song_events()
                       for u in ([ev.url] if ev.url else [s.url for s in ev.sub_events]) if u}
    bot.song_index = idx
    with mock.patch("songbot.bot.fetch_events", return_value=_song_events()):
        bot.handle(_inc(True, "song brand new"))
    assert len(sends) == 1, f"应回复 1 条，实际 {len(sends)}"
    assert "找到多首候选歌曲" in sends[0][1] and "Brand New!!" in sends[0][1], f"候选缺失: {sends[0][1][:160]}"
    ctx = bot.session.get(GROUP_ID, USER_ID)
    assert ctx and ctx["kind"] == "song_candidates" and len(ctx["songs"]) == 2, f"会话异常: {ctx}"
    sends.clear()
    bot.handle(_inc(False, "1"))
    assert len(sends) == 1 and "「Brand New!!」出现在 1 场 LIVE" in sends[0][1], f"选歌后 LIVE 列表异常: {sends}"
    assert bot.session.get(GROUP_ID, USER_ID)["kind"] == "song_lives"
    print("[S8-song] 多候选 -> 候选歌 -> 选歌 -> LIVE 列表 → PASS")


def check_song_no_hit(real_render: bool = False) -> None:
    bot, sends = _song_capture_bot(real_render)
    with mock.patch("songbot.bot.fetch_events", return_value=_song_events()):
        bot.handle(_inc(True, "song 不存在的歌曲xyz"))
    assert len(sends) == 1 and "没有找到" in sends[0][1], f"未命中提示异常: {sends}"
    assert bot.session.get(GROUP_ID, USER_ID) is None
    print("[S8-song] @bot song 无命中 -> 未找到 + 用法 → PASS")


# ---------------------------------------------------------------------------
# 离线检查
# ---------------------------------------------------------------------------
def check_name_multiday(real_render: bool = False) -> None:
    """@bot live 13thLIVE -> 子列表（DAY1/DAY2）+ 会话 event。"""
    bot, sends = _capture_bot(real_render)
    bot.handle(_inc(True, "live 13thLIVE"))
    assert len(sends) == 1, f"应回复 1 条，实际 {len(sends)}"
    text = sends[0][1]
    assert "13thLIVE" in text and "DAY1" in text and "DAY2" in text, f"子列表缺失: {text[:120]}"
    ctx = bot.session.get(GROUP_ID, USER_ID)
    assert ctx and ctx["kind"] == "event", f"会话应记 event: {ctx}"
    print("[name-多日] @bot live 13thLIVE -> 子列表（DAY1/DAY2）+ 会话 → PASS")


def check_confirm_day1_render(real_render: bool = False) -> None:
    """无@ DAY1 -> 抓详情 + 渲染 -> 发图（PNG 非空，无冗余文字）+ 清会话。"""
    bot, sends = _capture_bot(real_render)
    bot.handle(_inc(True, "live 13thLIVE"))
    sends.clear()
    bot.handle(_inc(False, "DAY1"))
    assert len(sends) == 1, f"应发图 1 次，实际 {len(sends)}"
    text, images = sends[0][1], sends[0][2]
    assert text == f"[CQ:at,qq={USER_ID}]", \
        f"发图应带 @归属（图片内已含标题/日期/出演），实际: {text[:60]!r}"
    assert len(images) == 1, f"应 1 张图，实际 {len(images)}"
    assert os.path.isfile(images[0]) and os.path.getsize(images[0]) > 0, f"PNG 为空: {images}"
    assert bot.session.get(GROUP_ID, USER_ID) is None, "会话应已清空"
    print("[确认-发图] 无@ DAY1 -> 抓详情+渲染 PNG（%d bytes，无文字消息）+ 清会话 → PASS"
          % os.path.getsize(images[0]))


def check_time_query(real_render: bool = False) -> None:
    """@bot live 2026年7月 -> 时间列表（IWSF + DERE）+ 候选会话。"""
    bot, sends = _capture_bot(real_render)
    bot.handle(_inc(True, "live 2026年7月"))
    assert len(sends) == 1
    text = sends[0][1]
    assert "2026年7月 的 LIVE" in text, f"时间列表头缺失: {text[:80]}"
    assert "IDOL WORLD SUPER FESTIVAL 2026" in text and "DERE of the DEAD" in text, \
        f"时间筛选缺命中: {text[:160]}"
    ctx = bot.session.get(GROUP_ID, USER_ID)
    assert ctx and ctx["kind"] == "candidates" and len(ctx["events"]) == 2, f"候选会话异常: {ctx}"
    print("[时间筛选] @bot live 2026年7月 -> IWSF+DERE 2 场 + 候选会话 → PASS")


def check_candidate_number(real_render: bool = False) -> None:
    """无@ 1 -> 取首候选（IWSF 多日）-> 子列表 + 会话更新为 event。"""
    bot, sends = _capture_bot(real_render)
    bot.handle(_inc(True, "live 2026年7月"))
    sends.clear()
    bot.handle(_inc(False, "1"))
    assert len(sends) == 1, f"应回复 1 条，实际 {len(sends)}"
    assert "IDOL WORLD SUPER FESTIVAL 2026" in sends[0][1], f"首候选不对: {sends[0][1][:120]}"
    ctx = bot.session.get(GROUP_ID, USER_ID)
    assert ctx and ctx["kind"] == "event", f"会话应更新为 event: {ctx}"
    print("[序号确认] 无@ 1 -> 首候选 IWSF 子列表 + 会话 → PASS")


def check_single_page_direct(real_render: bool = False) -> None:
    """@bot live 单页事件（DERE）-> 直接抓详情+渲染+发图。"""
    bot, sends = _capture_bot(real_render)
    bot.handle(_inc(True, "live DERE of the DEAD"))
    assert len(sends) == 1, f"应发图 1 次，实际 {len(sends)}"
    images = sends[0][2]
    assert len(images) == 1 and os.path.getsize(images[0]) > 0, f"图片异常: {images}"
    assert bot.session.get(GROUP_ID, USER_ID) is None, "单页事件不应留会话"
    print("[单页直发] @bot live DERE of the DEAD -> 渲染 PNG（%d bytes）→ PASS" % os.path.getsize(images[0]))


def check_no_hit(real_render: bool = False) -> None:
    bot, sends = _capture_bot(real_render)
    bot.handle(_inc(True, "live 不存在的公演"))
    assert len(sends) == 1 and "没有找到" in sends[0][1], f"未命中提示异常: {sends}"
    print("[无命中] @bot live 不存在 -> 未找到 + 用法 → PASS")


def check_http_end_to_end(host: str, port: int, real_render: bool = False) -> None:
    """真实 EventReceiver + 模拟 POST：@bot live 13thLIVE -> 无@ DAY1，端到端发图。"""
    bot, sends = _capture_bot(real_render)

    def on_incoming(inc: Incoming) -> None:
        bot.handle(inc)

    with EventReceiver(on_incoming, host=host, port=port) as receiver:
        port = receiver.bound_port
        base = f"http://{host}:{port}"

        def post(payload: dict) -> int:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{base}{EVENT_PATH}", data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status

        s1 = post({"post_type": "message", "message_type": "group",
                   "group_id": GROUP_ID, "user_id": USER_ID, "self_id": SELF_ID,
                   "message": [{"type": "at", "data": {"qq": SELF_ID}},
                               {"type": "text", "data": {"text": "live 13thLIVE"}}]})
        s2 = post({"post_type": "message", "message_type": "group",
                   "group_id": GROUP_ID, "user_id": USER_ID, "self_id": SELF_ID,
                   "message": [{"type": "text", "data": {"text": "DAY1"}}]})
        # 等回调线程跑完（真实渲染 ~1-3s）
        deadline = time.time() + 30
        while time.time() < deadline and len(sends) < 2:
            time.sleep(0.3)
        assert (s1, s2) == (200, 200), f"POST 应答异常: {s1}/{s2}"
        assert len(sends) == 2, f"回调应 2 次发送，实际 {len(sends)}"
        sublist, image = sends
        assert "13thLIVE" in sublist[1] and "DAY1" in sublist[1], "子列表回调异常"
        assert len(image[2]) == 1 and os.path.getsize(image[2][0]) > 0, "发图回调异常"
        print("[HTTP 端到端] POST @bot live 13thLIVE + 无@ DAY1 -> 200/200，子列表 + PNG → PASS")


def check_offline(real_render: bool = False) -> None:
    mode = "真实 Edge 渲染" if real_render else "mock 渲染"
    print(f"=== S6 离线全链路验收（fixture 索引 + MockTransport + {mode} + capture 发送）===")
    check_name_multiday(real_render)
    check_confirm_day1_render(real_render)
    check_time_query(real_render)
    check_candidate_number(real_render)
    check_single_page_direct(real_render)
    check_no_hit(real_render)
    check_http_end_to_end("127.0.0.1", 0, real_render)
    check_song_flow(real_render)
    check_song_candidates(real_render)
    check_song_no_hit(real_render)
    print("[ALL PASS]")


# ---------------------------------------------------------------------------
# live 模式（Phase B）
# ---------------------------------------------------------------------------
def check_napcat(cfg: BotConfig) -> bool:
    """探测 NapCat OneBot 是否在线；返回是否可用。"""
    base = "http://127.0.0.1:3000"
    try:
        r = httpx.get(f"{base}/get_login_info", timeout=5)
        data = r.json().get("data") or {}
        print(f"[napcat] OneBot 在线：bot {data.get('user_id')}（{data.get('nickname')}）")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[napcat] ❌ OneBot 不可达（{base}）: {exc}")
        print("        请在 NapCatQQ Desktop 中启动 bot（OneBot HTTP 3000）。")
        return False


def run_live(args: argparse.Namespace) -> int:
    print("=== S6 live 验收（真实索引 + 真实渲染 + 真实 NapCat 发送）===")
    if not check_napcat(load_bot_config(args.config)):
        return 1
    print(f"[postUrls] 请确认 NapCat OneBot 配置已追加 postUrls: "
          f'["http://127.0.0.1:{args.port}/event"]（messagePostFormat=array，'
          "NapCat WebUI API POST /api/OB11Config/SetConfig）")
    if args.group:
        print(f"[验收步骤] 在测试群 {args.group}：\n"
              f"  1) @bot live IWSF2026（或 live 13thLIVE / live 2026年7月）→ 收到子列表/候选\n"
              f"  2) 回复 DAY1（或序号）→ 收到歌曲列表图片\n"
              f"  （S9：@bot binding iwsf IDOL WORLD SUPER FESTIVAL 2026 后，live iwsf 直接命中）\n"
              f"  （S8：@bot song Dance in the Light → 收到该歌出现的 LIVE 列表 → 回复序号 → 收到该 LIVE 图片）")
    argv = ["--port", str(args.port)]
    if args.no_cache:
        argv.append("--no-cache")
    if args.dry_run:
        argv.append("--dry-run")
    print(f"启动 bot.py（参数: {' '.join(argv)}）…")
    return bot_mod.main(argv)


def main() -> int:
    parser = argparse.ArgumentParser(description="S6 主控验收（离线默认；--live 真实验收）")
    parser.add_argument("--live", action="store_true", help="live 模式：真实索引/渲染/发送，常驻")
    parser.add_argument("--real-render", action="store_true",
                        help="离线验收用真实 Edge 渲染（非沙箱环境；沙箱内 playwright 被拒）")
    parser.add_argument("--group", default=GROUP_ID, help=f"live 验收群（默认测试群 {GROUP_ID}）")
    parser.add_argument("--port", type=int, default=8090, help="live 接收器端口（默认 8090）")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--no-cache", action="store_true", help="禁用索引缓存")
    parser.add_argument("--dry-run", action="store_true", help="live 预演：只打印不真实发送")
    args = parser.parse_args()

    if args.live:
        return run_live(args)
    check_offline(real_render=args.real_render)
    return 0


if __name__ == "__main__":
    sys.exit(main())
