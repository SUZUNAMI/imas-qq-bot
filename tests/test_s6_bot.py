"""S6 单测：主控处理链（songbot.bot）.

约定（docs/S1-S7-taskplan.md §0.4）：解析类单测不联网（本文件全部离线）。
覆盖：
- 纯函数排版：format_event_list（截断 + 「还有 N 场」）/ format_sub_list / setlist_text
  / format_song_candidates / format_song_lives；
- 处理链第一段：@bot 名称唯一多日 -> 子列表 + 会话；唯一单页 -> 全流程发图；
  多候选 -> 候选列表 + 会话；无命中 -> 未找到；时间查询 -> 按年月筛选 + 候选会话；
- 第二段二次确认：DAY1（match_sub）/ 序号 / 候选内名称；序号越界提示；会话优先（@bot 也先确认）；
- 会话回落：有会话但解析失败且 @bot -> 视为新查询；无会话且未 @ -> 忽略；
- 兜底：图片发送失败 / 渲染失败 / 详情抓取失败 -> 纯文本歌单或错误提示；
- 配置：load_bot_config 默认值 + 文件解析；索引缓存写入/读取（patch fetch_events，零网络）；
- S8 song 流程：@bot song <歌名> 唯一 -> LIVE 列表 + 会话；回复序号 -> 发图 + 清会话；
  多候选 -> 候选歌 -> 选歌 -> LIVE 列表；无命中 / 空歌名 / 索引未就绪 / 序号越界提示；
- S7 启动/停止通知：启动/停止文案模板渲染（config `notify_startup`/`notify_shutdown` 可自定义、
  占位符替换、未知占位符保留）/ notify_groups 三种形态解析 / 逐群发送与异常容错 /
  停止文件监听与清理（_wait_for_stop / _remove_stop_file）。

注：临时文件一律放工作区内的 `.tmp_test/`（沙箱禁止写系统 %TEMP%，S4 同坑）。

运行：
    python -m unittest tests.test_s6_bot -v
"""

import json
import os
import shutil
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

import httpx  # noqa: E402

import songbot.bot as bot_mod  # noqa: E402
from songbot.bot import (  # noqa: E402
    CTX_CANDIDATES,
    CTX_EVENT,
    CTX_SONG_CANDIDATES,
    CTX_SONG_LIVES,
    BotConfig,
    SongBot,
    format_event_list,
    format_song_candidates,
    format_song_lives,
    format_sub_list,
    load_bot_config,
    setlist_text,
)
from songbot.models_song import Appearance, Event, Setlist, SongEntry, SubEvent, Track  # noqa: E402
from songbot.s1_fetch_events import PAGE_BASE_URL, parse_events_html  # noqa: E402
from songbot.s2_fetch_setlist import parse_setlist_html  # noqa: E402
from songbot.s3_match import normalize  # noqa: E402
from songbot.s5_receiver import Incoming, SessionStore  # noqa: E402
from songbot.s8_song_index import SongIndex, build_song_index, save_song_index  # noqa: E402
from songbot.s9_binding import DEFAULT_BINDINGS_FILE, BindingStore  # noqa: E402
from m6_notifier import NotifierConfig, push  # noqa: E402  （ref/，复用 M6 发送层）
from models import PushMessage, PushResult  # noqa: E402

SELF_ID = "1666562110"
GROUP_ID = "827029417"
USER_ID = "123456789"

FIXTURES = Path(_ROOT) / "fixtures"
TMP_ROOT = Path(_ROOT) / ".tmp_test" / "s6"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

IWSF_TITLE = "THE IDOLM@STER 20th Anniversary MORE RE@LITY LIVE IDOL WORLD SUPER FESTIVAL 2026"
MILLION_TITLE = "THE IDOLM@STER MILLION LIVE! 13thLIVE"
DERE_TITLE = "CINDERELLA GIRLS MUSICAL DERE of the DEAD"


def _ws_tmp(prefix: str) -> str:
    """工作区内临时目录。

    不用 ``tempfile.mkdtemp``：其在 Windows 上创建受限 ACL 目录，
    沙箱进程写入被拒（S4 同坑）；``os.makedirs`` 继承工作区 ACL 可正常读写。
    """
    d = os.path.join(str(TMP_ROOT), prefix + uuid.uuid4().hex[:8])
    os.makedirs(d, exist_ok=True)
    return d


def _rm_ws_tmp(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _load_events() -> list[Event]:
    """从 fixture 列表页解析事件索引（真实 S1 解析，离线）。"""
    html = (FIXTURES / "imas_db_song_event.html").read_text(encoding="utf-8")
    return parse_events_html(html, base_url=PAGE_BASE_URL)


def _mock_transport_client() -> httpx.Client:
    """MockTransport：按 URL 末尾文件名回 fixture 详情页；未知 URL -> 404（离线）。"""
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


def _inc(at_bot: bool, text: str, role: str = "owner") -> Incoming:
    """构造 Incoming；role 默认 owner（S9 管理命令默认有权限；被拒用例显式传 member）。"""
    return Incoming(group_id=GROUP_ID, user_id=USER_ID, at_bot=at_bot, text=text, role=role)


def _make_bot(*, fail_send=False, raise_send=False, raise_render=False) -> tuple[SongBot, list]:
    """构造离线 SongBot：fixture 索引 + mock 抓取/渲染/发送。返回 (bot, sends)。"""
    sends: list[tuple] = []

    def sender(group_id: str, text: str, image_paths) -> bool:
        sends.append((str(group_id), text, [str(p) for p in (image_paths or [])]))
        if raise_send:
            raise RuntimeError("send boom")
        return not fail_send

    def renderer(sl, *, out_dir=None):
        if raise_render:
            raise RuntimeError("render boom")
        return [Path("fake_setlist.png")]

    bot = SongBot(
        events=_load_events(),
        setlist_client=_mock_transport_client(),
        renderer=renderer,
        sender=sender,
        bindings=BindingStore(path=os.path.join(_ws_tmp("bind_"), "b.json")),  # 隔离真实 data/ 绑定文件
    )
    return bot, sends


# ---------------------------------------------------------------------------
# S8 歌曲索引测试辅助：3 个 fixture 详情页构建迷你索引（离线，与 test_s8 同路径）
# ---------------------------------------------------------------------------
_SONG_PAGES = {
    "iwsf_day1.html": "imas_db_iwsf_day1.html",
    "million_13th_day1.html": "imas_db_million_13th_day1.html",
    "cg_musical_dd.html": "imas_db_cg_musical_dd.html",
}


def _song_events() -> list[Event]:
    """只保留详情 URL 能映射到 fixture 的事件（IWSF / 13thLIVE / DERE）。"""
    out = []
    for ev in _load_events():
        urls = [ev.url] if ev.url else [s.url for s in ev.sub_events]
        if any(any(u.rstrip("/").endswith(k) for k in _SONG_PAGES) for u in urls):
            out.append(ev)
    return out


def _local_setlist_fetch(url: str) -> Setlist:
    """离线详情抓取：URL 末尾文件名 -> fixture 页解析；无映射抛错（模拟抓取失败）。"""
    for key, name in _SONG_PAGES.items():
        if url.rstrip("/").endswith(key):
            html = (FIXTURES / name).read_text(encoding="utf-8")
            return parse_setlist_html(html, url=url)
    raise FileNotFoundError(f"no fixture for {url}")


def _mini_song_index() -> SongIndex:
    """用 3 个 fixture 详情页构建迷你歌曲索引（含 "Dance in the Light" 2 场 LIVE）。"""
    return build_song_index(_song_events(), _local_setlist_fetch)


def _song_bot(*, with_index: bool = True) -> tuple[SongBot, list]:
    """S8 测试 bot：注入迷你歌曲索引 + capture 发送。返回 (bot, sends)。

    显式传 ``config=BotConfig()``（空缓存路径）：不继承 config.yaml 的真实
    index_cache / song_index_cache / bindings_file，防止 update live 等流程
    把迷你索引写进真实 ``data/`` 缓存（S9 ``_update_bot`` 同款隔离）。
    """
    sends: list[tuple] = []

    def sender(group_id: str, text: str, image_paths) -> bool:
        sends.append((str(group_id), text, [str(p) for p in (image_paths or [])]))
        return True

    bot = SongBot(
        config=BotConfig(),
        events=_load_events(),
        setlist_client=_mock_transport_client(),
        renderer=lambda sl, **kw: [Path("fake_setlist.png")],
        sender=sender,
        bindings=BindingStore(path=os.path.join(_ws_tmp("bind_"), "b.json")),
        song_index=_mini_song_index() if with_index else None,
    )
    return bot, sends


def _patch_song_events():
    """patch songbot.bot.fetch_events -> 迷你索引同源事件（增量刷新首个已收录即停止，零抓取）。"""
    return mock.patch("songbot.bot.fetch_events", return_value=_song_events())


class TestFormat(unittest.TestCase):
    def test_format_event_list_truncates(self):
        events = [Event(title=f"LIVE {i}", year="2026", date=f"2026/0{i}/01") for i in range(1, 13)]
        out = format_event_list(events, limit=10)
        lines = out.splitlines()
        self.assertEqual(len(lines), 11)                # 10 条 + 截断提示
        self.assertTrue(lines[0].startswith("1. LIVE 1"))
        self.assertIn("还有 2 场", lines[-1])

    def test_format_event_list_multiday(self):
        ev = Event(title="IDOL WORLD SUPER FESTIVAL 2026", year="2026",
                   sub_events=[SubEvent("DAY1 全力援走", "…DAY1", "http://x/iwsf_day1.html", "2026/07/24(金)"),
                               SubEvent("DAY2 全力疾走", "…DAY2", "http://x/iwsf_day2.html", "2026/07/25(土)")])
        out = format_event_list([ev])
        self.assertIn("多日", out)
        self.assertIn("DAY1", out)
        self.assertIn("2026/07/24", out)

    def test_format_sub_list(self):
        ev = Event(title="IDOL WORLD SUPER FESTIVAL 2026", year="2026",
                   sub_events=[SubEvent("DAY1 全力援走", "…", "http://x/1.html", "2026/07/24(金)")])
        out = format_sub_list(ev)
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", out)
        self.assertIn("1. DAY1 全力援走", out)
        self.assertIn("2026/07/24", out)
        self.assertIn("回复序号", out)

    def test_setlist_text(self):
        sl = Setlist(title="IWSF 2026 DAY1", date_venue="2026/07/24(金) 開場 17:00",
                     performers=["天海春香", "如月千早"],
                     tracks=[Track(no=1, title="Dance in the Light", brand="ミリオンライブ！",
                                   performers=["舞浜歩"]),
                             Track(no=2, title="全員曲", performers=["全員"])])
        out = setlist_text(sl)
        self.assertIn("IWSF 2026 DAY1", out)
        self.assertIn("出演: 天海春香", out)
        self.assertIn("1. Dance in the Light [ミリオンライブ！] 舞浜歩", out)
        self.assertIn("2. 全員曲 全員", out)

    def test_setlist_text_empty(self):
        sl = Setlist(title="空", date_venue="", tracks=[])
        self.assertEqual(setlist_text(sl).strip(), "「空」")


class TestFirstStage(unittest.TestCase):
    def test_name_unique_multiday_sublist_and_session(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live IWSF2026"))
        self.assertEqual(len(sends), 1)
        text = sends[0][1]
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", text)
        self.assertIn("DAY1", text)
        ctx = bot.session.get(GROUP_ID, USER_ID)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["kind"], CTX_EVENT)
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", ctx["event"].title)

    def test_second_stage_day1_full_flow(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live 13thLIVE"))              # 13thLIVE 子公演名是 DAY1/DAY2
        sends.clear()
        bot.handle(_inc(False, "DAY1"))
        # 一次 sender 调用 = @归属文本 + 图片路径（标题/日期/出演/曲目都在 PNG 内，2026-08-27 live 反馈去文字）
        self.assertEqual(len(sends), 1)
        text, images = sends[0][1], sends[0][2]
        self.assertEqual(text, f"[CQ:at,qq={USER_ID}]")   # 回复按用户归属（@发起用户）
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].endswith("fake_setlist.png"))
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))   # 会话已清

    def test_name_unique_single_page_direct_render(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live DERE of the DEAD"))
        # 单页事件 -> 直接全流程：一次调用 = @归属文本 + 图片
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0][1], f"[CQ:at,qq={USER_ID}]")
        self.assertEqual(len(sends[0][2]), 1)
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))

    def test_moiw_alias_flow(self):
        """MOIW 别名：@bot live MOIW 2025 -> 唯一多日事件 -> 子列表 + 会话。"""
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live MOIW 2025"))
        self.assertEqual(len(sends), 1)
        self.assertIn("M@STERS OF IDOL WORLD 2025", sends[0][1])
        ctx = bot.session.get(GROUP_ID, USER_ID)
        self.assertEqual(ctx["kind"], CTX_EVENT)
        self.assertIn("M@STERS OF IDOL WORLD 2025", ctx["event"].title)

    def test_name_multi_candidates(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live シャニ"))
        self.assertEqual(len(sends), 1)
        self.assertIn("找到多个匹配", sends[0][1])
        ctx = bot.session.get(GROUP_ID, USER_ID)
        self.assertEqual(ctx["kind"], CTX_CANDIDATES)
        self.assertGreaterEqual(len(ctx["events"]), 2)

    def test_name_no_hit(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live 不存在的公演"))
        self.assertEqual(len(sends), 1)
        self.assertIn("没有找到", sends[0][1])
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))

    def test_time_query(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live 2026年7月"))
        self.assertEqual(len(sends), 1)
        text = sends[0][1]
        self.assertIn("2026年7月 的 LIVE", text)
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", text)
        self.assertIn("DERE of the DEAD", text)
        ctx = bot.session.get(GROUP_ID, USER_ID)
        self.assertEqual(ctx["kind"], CTX_CANDIDATES)

    def test_time_query_no_hit(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live 2026年8月"))               # fixture 中 2026-08 无 LIVE
        self.assertEqual(len(sends), 1)
        self.assertIn("未找到 2026年8月 的 LIVE", sends[0][1])


class TestSecondStage(unittest.TestCase):
    def test_candidate_number_pick(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live 2026年7月"))
        sends.clear()
        bot.handle(_inc(False, "1"))                       # 首候选 = IWSF（多日）
        self.assertEqual(len(sends), 1)
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", sends[0][1])
        ctx = bot.session.get(GROUP_ID, USER_ID)
        self.assertEqual(ctx["kind"], CTX_EVENT)

    def test_candidate_number_out_of_range(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live 2026年7月"))
        sends.clear()
        bot.handle(_inc(False, "99"))
        self.assertEqual(len(sends), 1)
        self.assertIn("序号超出范围", sends[0][1])

    def test_candidate_name_within(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live 2026年7月"))               # 候选 = IWSF + DERE
        sends.clear()
        bot.handle(_inc(False, "IDOL WORLD SUPER FESTIVAL 2026"))
        self.assertEqual(len(sends), 1)
        self.assertIn("回复序号", sends[0][1])              # 唯一命中 -> 子列表
        ctx = bot.session.get(GROUP_ID, USER_ID)
        self.assertEqual(ctx["kind"], CTX_EVENT)

    def test_event_confirmation_priority_when_at_bot(self):
        """有会话时 @bot DAY1 也先走二次确认（防止误判为全索引新查询）。"""
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live 13thLIVE"))
        sends.clear()
        bot.handle(_inc(True, "DAY1"))
        self.assertEqual(len(sends), 1)                    # 确认成功 -> 发图
        self.assertEqual(len(sends[0][2]), 1)
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))

    def test_at_bot_fresh_query_when_confirm_fails(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live IWSF2026"))
        sends.clear()
        bot.handle(_inc(True, "live 13thLIVE"))              # 旧会话内解析失败 -> 新查询
        self.assertEqual(len(sends), 1)
        self.assertIn("13thLIVE", sends[0][1])
        ctx = bot.session.get(GROUP_ID, USER_ID)
        self.assertIn("13thLIVE", ctx["event"].title)

    def test_no_session_no_at_ignored(self):
        bot, sends = _make_bot()
        bot.handle(_inc(False, "随便说点什么"))
        self.assertEqual(len(sends), 0)

    def test_unresolved_reply_keeps_session(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live IWSF2026"))
        sends.clear()
        bot.handle(_inc(False, "完全看不懂"))                # match_sub 失败
        self.assertEqual(len(sends), 1)
        self.assertIn("没看懂", sends[0][1])
        self.assertIsNotNone(bot.session.get(GROUP_ID, USER_ID))  # 会话保留


class TestFallback(unittest.TestCase):
    @mock.patch.object(SongBot, "_confirm_group_image", return_value=False)
    def test_image_send_failure_falls_back_to_text(self, mock_confirm):
        bot, sends = _make_bot(fail_send=True)
        bot.handle(_inc(True, "live 13thLIVE"))
        sends.clear()
        bot.handle(_inc(False, "DAY1"))
        # 第一发：图片发送返回 False 且确认未送达 -> 第二发回退纯文本歌单
        self.assertEqual(len(sends), 2)
        self.assertIn("图片发送失败", sends[1][1])
        self.assertIn("Dance in the Light", sends[1][1])   # fixture 歌单曲目
        mock_confirm.assert_called_once()

    @mock.patch.object(SongBot, "_confirm_group_image", return_value=True)
    def test_image_failure_but_delivered_skips_text(self, mock_confirm):
        """S7 bug 修复：NapCat 假失败（超时但实际送达）-> 确认已送达 -> 不发文字版兜底。"""
        bot, sends = _make_bot(fail_send=True)
        bot.handle(_inc(True, "live 13thLIVE"))
        sends.clear()
        bot.handle(_inc(False, "DAY1"))
        # 只发过图片那一条（发送返回 False 但确认已送达，跳过文字版）
        self.assertEqual(len(sends), 1)
        self.assertNotIn("失败", sends[0][1])
        self.assertTrue(sends[0][2])                    # 发送的是图片路径
        mock_confirm.assert_called_once()

    def test_render_failure_falls_back_to_text(self):
        bot, sends = _make_bot(raise_render=True)
        bot.handle(_inc(True, "live DERE of the DEAD"))
        self.assertEqual(len(sends), 1)
        self.assertIn("渲染失败", sends[0][1])
        self.assertIn("改发文字版", sends[0][1])

    def test_fetch_failure_replies_error(self):
        bot, sends = _make_bot()
        # 找一个详情 URL 不在 MockTransport 映射里的单页事件（离线 404）
        events = _load_events()
        ev = next(e for e in events if not e.sub_events
                  and not e.url.endswith(("iwsf_day1.html", "million_13th_day1.html",
                                          "cg_musical_dd.html")))
        bot.handle(_inc(True, "live " + ev.title))
        self.assertEqual(len(sends), 1)
        self.assertIn("抓取公演详情失败", sends[0][1])

    def test_send_exception_does_not_crash(self):
        bot, sends = _make_bot(raise_send=True)
        bot.handle(_inc(True, "live 13thLIVE"))
        sends.clear()
        bot.handle(_inc(False, "DAY1"))
        # 发送抛异常：记录日志、进程不崩；无会话残留
        self.assertGreaterEqual(len(sends), 1)
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))


class TestConfirmGroupImage(unittest.TestCase):
    """S7 bug 修复：图片送达确认（NapCat 假失败 -> 查最近消息判断是否已送达）。"""

    def _bot_with_mock_napcat(self, history_messages, *, raise_post=False):
        bot = SongBot(config=BotConfig())
        mock_client = mock.MagicMock()   # MagicMock：支持 `with httpx.Client(...)` 上下文管理器
        mock_client.__enter__.return_value = mock_client   # `with` 返回自身（否则落到新 mock 上）
        mock_client.get.return_value.json.return_value = {
            "data": {"user_id": SELF_ID, "nickname": "bot"}}
        if raise_post:
            mock_client.post.side_effect = RuntimeError("napcat down")
        else:
            mock_client.post.return_value.json.return_value = {
                "data": {"messages": history_messages}}
        return bot, mock_client

    def _recent_msg(self, user_id: str = SELF_ID, *, with_image: bool = True,
                    seconds_ago: float = 2.0) -> dict:
        segs = [{"type": "image", "data": {"file": "base64://x"}}] if with_image else \
               [{"type": "text", "data": {"text": "hi"}}]
        return {"user_id": user_id, "time": time.time() - seconds_ago, "message": segs}

    def _patch(self, mock_client):
        """统一 patch：httpx.Client 返回 mock + bot 自身 uin 固定为 SELF_ID（防 _SELF_CACHE 污染）。"""
        return (
            mock.patch("songbot.bot.httpx.Client", return_value=mock_client),
            mock.patch("songbot.bot._get_self_info", return_value=(SELF_ID, "bot")),
        )

    def test_finds_recent_bot_image(self):
        bot, mock_client = self._bot_with_mock_napcat(
            [self._recent_msg(), self._recent_msg(user_id="OTHER")])
        with self._patch(mock_client)[0], self._patch(mock_client)[1]:
            self.assertTrue(bot._confirm_group_image(GROUP_ID))

    def test_no_recent_image_returns_false(self):
        # 最近消息全是文本 / 别人的图 -> 未确认
        bot, mock_client = self._bot_with_mock_napcat(
            [self._recent_msg(with_image=False), self._recent_msg(user_id="OTHER")])
        with self._patch(mock_client)[0], self._patch(mock_client)[1]:
            self.assertFalse(bot._confirm_group_image(GROUP_ID))

    def test_old_message_outside_window_returns_false(self):
        bot, mock_client = self._bot_with_mock_napcat(
            [self._recent_msg(seconds_ago=999)])
        with self._patch(mock_client)[0], self._patch(mock_client)[1]:
            self.assertFalse(bot._confirm_group_image(GROUP_ID))

    def test_query_error_returns_false(self):
        bot, mock_client = self._bot_with_mock_napcat([], raise_post=True)
        with self._patch(mock_client)[0], self._patch(mock_client)[1]:
            self.assertFalse(bot._confirm_group_image(GROUP_ID))


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = BotConfig()
        self.assertEqual(cfg.port, 8090)
        self.assertEqual(cfg.ttl_sec, 300.0)
        self.assertEqual(cfg.reply_limit, 10)
        self.assertEqual(cfg.index_cache, "")
        self.assertEqual(cfg.bindings_file, DEFAULT_BINDINGS_FILE)

    def test_load_from_file(self):
        td = _ws_tmp("cfg_")
        try:
            p = os.path.join(td, "config.yaml")
            with open(p, "w", encoding="utf-8") as f:
                f.write("songbot:\n  port: 9999\n  ttl_sec: 60\n  reply_limit: 3\n"
                        "  event_list_url: http://example.com/event\n"
                        "  index_cache: data/x.json\n"
                        "  bindings_file: data/x_bind.json\n")
            cfg = load_bot_config(p)
            self.assertEqual(cfg.port, 9999)
            self.assertEqual(cfg.ttl_sec, 60.0)
            self.assertEqual(cfg.reply_limit, 3)
            self.assertEqual(cfg.event_list_url, "http://example.com/event")
            self.assertEqual(cfg.index_cache, "data/x.json")
            self.assertEqual(cfg.bindings_file, "data/x_bind.json")
        finally:
            _rm_ws_tmp(td)

    def test_missing_file_defaults(self):
        cfg = load_bot_config(os.path.join(_ws_tmp("none_"), "no_such_cfg.yaml"))
        self.assertEqual(cfg.port, 8090)


class TestIndexCache(unittest.TestCase):
    def test_roundtrip_serialize(self):
        events = _load_events()
        restored = bot_mod._events_from_dict(bot_mod._events_to_dict(events))
        self.assertEqual(len(restored), len(events))
        self.assertEqual(restored[0].title, events[0].title)
        ev = next(e for e in restored if e.sub_events)
        self.assertTrue(ev.sub_events[0].url)

    @mock.patch("songbot.bot.fetch_events")
    def test_cache_write_then_load(self, mock_fetch):
        events = _load_events()
        mock_fetch.return_value = events
        td = _ws_tmp("idx_")
        try:
            cache = os.path.join(td, "idx.json")
            cfg = BotConfig(index_cache=cache, index_cache_ttl_sec=86400)
            SongBot(config=cfg, bindings=BindingStore(path=os.path.join(td, "b.json"))).build_index()
            mock_fetch.assert_called_once()
            self.assertTrue(os.path.isfile(cache))
            # 二次启动：缓存命中，不再 fetch
            mock_fetch.reset_mock()
            bot2 = SongBot(config=cfg, bindings=BindingStore(path=os.path.join(td, "b.json")))
            bot2.build_index()
            mock_fetch.assert_not_called()
            self.assertEqual(len(bot2.events), len(events))
            self.assertEqual(bot2.latest_year, max(int(e.year) for e in events))
        finally:
            _rm_ws_tmp(td)

    @mock.patch("songbot.bot.fetch_events")
    def test_cache_expired_refetch(self, mock_fetch):
        events = _load_events()
        mock_fetch.return_value = events
        td = _ws_tmp("idx2_")
        try:
            cache = os.path.join(td, "idx.json")
            cfg = BotConfig(index_cache=cache, index_cache_ttl_sec=0.0)   # 立即过期
            SongBot(config=cfg, bindings=BindingStore(path=os.path.join(td, "b.json"))).build_index()
            mock_fetch.reset_mock()
            SongBot(config=cfg, bindings=BindingStore(path=os.path.join(td, "b.json"))).build_index()
            mock_fetch.assert_called_once()                                # 过期 -> 重抓
        finally:
            _rm_ws_tmp(td)


class TestS9BindingCommands(unittest.TestCase):
    """S9 bot 集成：强制前缀 + binding/unbind/bindings + live 先查绑定 + update live。"""

    def _update_bot(self, sends: list):
        """update 测试用 bot：index_cache/bindings 指向工作区临时路径（不碰真实 data/）。"""
        td = _ws_tmp("upd_")

        def sender(group_id: str, text: str, image_paths) -> bool:
            sends.append((str(group_id), text, [str(p) for p in (image_paths or [])]))
            return True

        cfg = BotConfig(index_cache=os.path.join(td, "idx.json"),
                        bindings_file=os.path.join(td, "b.json"))
        return SongBot(events=_load_events(), setlist_client=_mock_transport_client(),
                       renderer=lambda sl, **kw: [Path("fake.png")], sender=sender,
                       config=cfg)

    def test_bare_query_gets_usage_hint(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "IWSF2026"))               # 无前缀 -> 用法提示（强制前缀）
        self.assertEqual(len(sends), 1)
        self.assertIn("命令前缀", sends[0][1])
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))

    def test_binding_set_and_live_uses_binding(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "binding 13th 13thLIVE"))   # 13thLIVE 名称唯一命中
        self.assertEqual(len(sends), 1)
        self.assertIn("已绑定：13th → THE IDOLM@STER MILLION LIVE! 13thLIVE", sends[0][1])
        sends.clear()
        bot.handle(_inc(True, "live 13th"))              # 绑定命中 -> 多日子列表
        self.assertEqual(len(sends), 1)
        self.assertIn("13thLIVE", sends[0][1])
        self.assertIn("DAY1", sends[0][1])
        ctx = bot.session.get(GROUP_ID, USER_ID)
        self.assertEqual(ctx["kind"], CTX_EVENT)

    def test_binding_unique_required(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "binding x シャニ"))        # 多候选 -> 不绑
        self.assertIn("未能唯一确定", sends[0][1])
        self.assertEqual(len(bot.bindings), 0)
        bot.handle(_inc(True, "binding y 不存在的公演xyz"))  # 0 命中 -> 不绑
        self.assertIn("未能唯一确定", sends[1][1])
        self.assertEqual(len(bot.bindings), 0)

    def test_binding_missing_args(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "binding iwsf"))            # 缺事件名
        self.assertEqual(len(sends), 1)
        self.assertIn("binding <略缩> <事件名>", sends[0][1])
        self.assertEqual(len(bot.bindings), 0)

    def test_binding_single_page_direct(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "binding dere DERE of the DEAD"))
        self.assertIn("已绑定：dere", sends[0][1])
        sends.clear()
        bot.handle(_inc(True, "live dere"))               # 绑定单页 -> 直接发图
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0][1], f"[CQ:at,qq={USER_ID}]")   # @归属
        self.assertEqual(len(sends[0][2]), 1)

    def test_unbind(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "binding 13th 13thLIVE"))
        self.assertEqual(len(bot.bindings), 1)
        sends.clear()
        bot.handle(_inc(True, "unbind 13th"))
        self.assertIn("已删除绑定「13th」", sends[0][1])
        self.assertEqual(len(bot.bindings), 0)
        sends.clear()
        bot.handle(_inc(True, "unbind 13th"))             # 重复删 -> 未找到
        self.assertIn("未找到绑定「13th」", sends[0][1])

    def test_bindings_list(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "bindings"))                # 空
        self.assertIn("暂无绑定", sends[0][1])
        bot.handle(_inc(True, "binding aaa 13thLIVE"))
        bot.handle(_inc(True, "binding zzz CINDERELLA GIRLS MUSICAL DERE of the DEAD"))
        sends.clear()
        bot.handle(_inc(True, "bindings"))
        self.assertIn("全部绑定（2 条）", sends[0][1])
        self.assertIn("aaa →", sends[0][1])
        self.assertIn("zzz →", sends[0][1])

    def test_stale_binding_ignored(self):
        bot, sends = _make_bot()
        # 直接塞一个不在索引中的事件（模拟绑定后事件下架/改版）
        bot.bindings.set("ghost", Event(title="GHOST LIVE 1999", year="1999", url="http://x/ghost.html"))
        sends.clear()
        bot.handle(_inc(True, "live ghost"))
        self.assertEqual(len(sends), 1)
        self.assertIn("已不在索引中", sends[0][1])
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))

    def test_update_live(self):
        sends: list = []
        bot = self._update_bot(sends)
        events = _load_events()
        with mock.patch("songbot.bot.fetch_events", return_value=events) as mf:
            bot.handle(_inc(True, "update live"))
        mf.assert_called_once()
        self.assertEqual(len(sends), 2)                   # 进行中提示 + 完成回执
        self.assertIn("正在刷新", sends[0][1])
        self.assertIn("已刷新", sends[1][1])
        self.assertIn(f"{len(events)} 事件", sends[1][1])
        self.assertNotIn("歌曲", sends[1][1])             # 未接 S8 钩子
        self.assertEqual(len(bot.events), len(events))

    def test_update_live_with_song_refresher(self):
        sends: list = []
        bot = self._update_bot(sends)
        bot.song_refresher = lambda events: 42            # S8 钩子：返回歌曲数
        events = _load_events()
        with mock.patch("songbot.bot.fetch_events", return_value=events):
            bot.handle(_inc(True, "update live"))
        self.assertIn("42 歌曲", sends[1][1])

    def test_update_live_fetch_failure(self):
        from songbot.s1_fetch_events import FetchError
        sends: list = []
        bot = self._update_bot(sends)
        with mock.patch("songbot.bot.fetch_events", side_effect=FetchError("boom")):
            bot.handle(_inc(True, "update live"))
        self.assertIn("刷新失败", sends[1][1])
        self.assertGreater(len(bot.events), 0)            # 旧索引仍在

    def test_update_wrong_arg(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "update 2026"))
        self.assertEqual(len(sends), 1)
        self.assertIn("只支持 update live", sends[0][1])

    def test_unknown_command_hint(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "foobar xxx"))
        self.assertIn("命令前缀", sends[0][1])

    def test_manage_commands_denied_for_member(self):
        """普通成员（member）使用 binding/unbind/bindings/update 一律拒绝，且无副作用。"""
        bot, sends = _make_bot()
        for text in ("binding x 13thLIVE", "unbind x", "bindings", "update live"):
            sends.clear()
            bot.handle(_inc(True, text, role="member"))
            self.assertEqual(len(sends), 1, text)
            self.assertIn("仅群主/管理员", sends[0][1], text)
        self.assertEqual(len(bot.bindings), 0)                     # 没绑上
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))
        # update 被拒时不得触发抓取
        with mock.patch("songbot.bot.fetch_events") as mf:
            bot.handle(_inc(True, "update live", role="member"))
        mf.assert_not_called()

    def test_manage_commands_allowed_for_administrator(self):
        """管理员（administrator）可用 binding（owner 已由其余用例覆盖，_inc 默认 owner）。"""
        bot, sends = _make_bot()
        bot.handle(_inc(True, "binding 13th 13thLIVE", role="administrator"))
        self.assertIn("已绑定：13th", sends[0][1])
        self.assertEqual(len(bot.bindings), 1)

    def test_live_and_song_open_to_all(self):
        """live / song 全员可用（member 也能查询，不触发权限提示）。"""
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live IWSF2026", role="member"))
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", sends[0][1])
        self.assertNotIn("仅群主/管理员", sends[0][1])


class TestS8SongFlow(unittest.TestCase):
    """S8 bot 集成：@bot song <歌名> 两段交互（候选歌 -> LIVE -> 选 -> 发图）。"""

    def test_format_song_helpers(self):
        entry = SongEntry(title="Dance in the Light", appearances=[
            Appearance(event_title="IWSF 2026", event_year="2026", sub_title="DAY1",
                       date="2026/07/24(金)", url="http://x/iwsf_day1.html"),
            Appearance(event_title="13thLIVE", event_year="2026", sub_title="DAY1",
                       date="2026/05/05(火祝)", url="http://x/million_13th_day1.html"),
        ])
        out = format_song_lives(entry)
        self.assertIn("「Dance in the Light」出现在 2 场 LIVE", out)
        self.assertIn("1. IWSF 2026（DAY1） 2026/07/24(金)", out)
        self.assertIn("回复序号", out)
        cand = format_song_candidates([entry])
        self.assertIn("1. Dance in the Light（2 场 LIVE）", cand)
        self.assertIn("回复序号或歌名", cand)

    def test_song_unique_lists_lives(self):
        """@bot song 唯一命中 -> 列出该歌出现过的 LIVE（2 场）+ 会话 CTX_SONG_LIVES。"""
        bot, sends = _song_bot()
        with _patch_song_events():
            bot.handle(_inc(True, "song Dance in the Light"))
        self.assertEqual(len(sends), 1)
        text = sends[0][1]
        self.assertIn("「Dance in the Light」出现在 2 场 LIVE", text)
        self.assertIn("IDOL WORLD SUPER FESTIVAL 2026", text)     # IWSF day1
        self.assertIn("MILLION LIVE! 13thLIVE", text)             # 13th day1
        ctx = bot.session.get(GROUP_ID, USER_ID)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["kind"], CTX_SONG_LIVES)
        self.assertEqual(len(ctx["lives"]), 2)

    def test_song_pick_live_number_renders(self):
        """第二段：回复序号 -> 抓详情 + 渲染 + 发图 + 清会话。"""
        bot, sends = _song_bot()
        with _patch_song_events():
            bot.handle(_inc(True, "song Dance in the Light"))
        sends.clear()
        bot.handle(_inc(False, "1"))                            # 首个 LIVE = IWSF day1
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0][1], f"[CQ:at,qq={USER_ID}]")   # 只发图（带 @ 归属）
        self.assertEqual(len(sends[0][2]), 1)
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))   # 会话已清

    def test_song_lives_out_of_range(self):
        bot, sends = _song_bot()
        with _patch_song_events():
            bot.handle(_inc(True, "song Dance in the Light"))
        sends.clear()
        bot.handle(_inc(False, "99"))
        self.assertEqual(len(sends), 1)
        self.assertIn("序号超出范围（1–2）", sends[0][1])
        self.assertIsNotNone(bot.session.get(GROUP_ID, USER_ID))  # 会话保留

    def test_song_multi_candidate_then_pick(self):
        """多候选 -> 候选歌列表 + 会话；回复序号选歌 -> LIVE 列表。"""
        bot, sends = _song_bot()
        # 手工注入两个同词元候选 + 覆盖 source_urls（增量刷新零抓取）
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
        with _patch_song_events():
            bot.handle(_inc(True, "song brand new"))
        self.assertEqual(len(sends), 1)
        self.assertIn("找到多首候选歌曲", sends[0][1])
        self.assertIn("1. Brand New!!", sends[0][1])
        self.assertIn("2. Brand New Wave!", sends[0][1])
        self.assertEqual(sends[0][1].count("回复序号或歌名"), 1, "二次确认提示只应出现一次（回归：曾重复拼接）")
        ctx = bot.session.get(GROUP_ID, USER_ID)
        self.assertEqual(ctx["kind"], CTX_SONG_CANDIDATES)
        self.assertEqual(len(ctx["songs"]), 2)
        sends.clear()
        bot.handle(_inc(False, "1"))                            # 选 Brand New!!
        self.assertEqual(len(sends), 1)
        self.assertIn("「Brand New!!」出现在 1 场 LIVE", sends[0][1])
        ctx = bot.session.get(GROUP_ID, USER_ID)
        self.assertEqual(ctx["kind"], CTX_SONG_LIVES)

    def test_song_candidate_out_of_range(self):
        bot, sends = _song_bot()
        idx = SongIndex()
        idx.entries["brandnewwave"] = SongEntry(title="Brand New Wave!", appearances=[])
        idx.entries["brandnew"] = SongEntry(title="Brand New!!", appearances=[])
        idx.source_urls = {u for ev in _song_events()
                           for u in ([ev.url] if ev.url else [s.url for s in ev.sub_events]) if u}
        bot.song_index = idx
        with _patch_song_events():
            bot.handle(_inc(True, "song brand new"))
        sends.clear()
        bot.handle(_inc(False, "99"))
        self.assertIn("序号超出范围（1–2）", sends[0][1])

    def test_song_candidate_relist_single_hint(self):
        """候选内歌名再匹配仍多义 -> 重列候选，「回复序号或歌名」只出现一次（回归：曾重复拼接）。"""
        bot, sends = _song_bot()
        idx = SongIndex()
        idx.entries["brandnewwave"] = SongEntry(title="Brand New Wave!", appearances=[])
        idx.entries["brandnew"] = SongEntry(title="Brand New!!", appearances=[])
        idx.source_urls = {u for ev in _song_events()
                           for u in ([ev.url] if ev.url else [s.url for s in ev.sub_events]) if u}
        bot.song_index = idx
        with _patch_song_events():
            bot.handle(_inc(True, "song brand new"))
        sends.clear()
        bot.handle(_inc(False, "brand new"))              # 候选内仍多义 -> 重列候选
        self.assertEqual(len(sends), 1)
        self.assertIn("还是没唯一确定", sends[0][1])
        self.assertEqual(sends[0][1].count("回复序号或歌名"), 1)

    def test_song_no_hit(self):
        bot, sends = _song_bot()
        with _patch_song_events():
            bot.handle(_inc(True, "song 不存在的歌曲xyz"))
        self.assertEqual(len(sends), 1)
        self.assertIn("没有找到", sends[0][1])
        self.assertIn("用法", sends[0][1])
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))

    def test_song_empty_query(self):
        bot, sends = _song_bot()
        bot.handle(_inc(True, "song"))
        self.assertEqual(len(sends), 1)
        self.assertIn("song 后要跟歌名", sends[0][1])

    def test_song_index_not_ready(self):
        """索引未就绪（未注入 + 未加载缓存）-> 回「构建中」提示，不匹配不落会话。"""
        bot, sends = _song_bot(with_index=False)
        bot.handle(_inc(True, "song Dance in the Light"))
        self.assertEqual(len(sends), 1)
        self.assertIn("尚未构建完成", sends[0][1])
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))

    def test_song_refresh_failure_uses_existing_index(self):
        """查询前列表刷新失败（FetchError）-> 沿用现有索引继续匹配。"""
        bot, sends = _song_bot()
        from songbot.s1_fetch_events import FetchError
        with mock.patch("songbot.bot.fetch_events", side_effect=FetchError("boom")):
            bot.handle(_inc(True, "song Dance in the Light"))
        self.assertEqual(len(sends), 1)
        self.assertIn("2 场 LIVE", sends[0][1])                  # 仍用注入索引命中

    def test_update_live_rebuilds_song_index(self):
        """update live 钩子（_song_refresher）：用最新事件全量重建歌曲索引并回报歌曲数。"""
        bot, sends = _song_bot(with_index=False)
        bot.song_refresher = bot._song_refresher
        with mock.patch("songbot.bot.fetch_events", return_value=_song_events()):
            bot.handle(_inc(True, "update live"))
        self.assertEqual(len(sends), 2)
        self.assertIn("正在刷新", sends[0][1])
        self.assertIn("已刷新", sends[1][1])
        self.assertIn("歌曲", sends[1][1])
        self.assertIsNotNone(bot.song_index)
        self.assertGreater(len(bot.song_index.entries), 0)

    def test_start_song_index_loads_cache(self):
        """start_song_index：有落盘缓存 -> 直接加载（不启后台构建线程）。"""
        td = _ws_tmp("sidx_")
        try:
            cache = os.path.join(td, "song_index.json")
            save_song_index(_mini_song_index(), cache)
            bot = SongBot(config=BotConfig(song_index_cache=cache),
                          events=_load_events())
            bot.start_song_index()
            self.assertIsNotNone(bot.song_index)
            self.assertGreater(len(bot.song_index.entries), 0)
            self.assertIn(normalize("Dance in the Light"), bot.song_index.entries)
        finally:
            _rm_ws_tmp(td)


class TestQuit(unittest.TestCase):
    """quit：用户回复 quit 取消当前等待（清会话，不再等待二次确认）。"""

    def test_quit_clears_event_session(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live IWSF2026"))                # 多日事件 -> 会话
        self.assertIsNotNone(bot.session.get(GROUP_ID, USER_ID))
        sends.clear()
        bot.handle(_inc(False, "quit"))                        # 无 @ 取消
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0][1], f"[CQ:at,qq={USER_ID}] 已取消本次查询，可重新 @bot 发起（live / song）")
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))  # 会话已清

    def test_quit_clears_song_lives_session(self):
        bot, sends = _song_bot()
        with _patch_song_events():
            bot.handle(_inc(True, "song Dance in the Light"))  # -> CTX_SONG_LIVES
        self.assertEqual(bot.session.get(GROUP_ID, USER_ID)["kind"], CTX_SONG_LIVES)
        sends.clear()
        bot.handle(_inc(False, "QUIT"))                        # 大小写不敏感
        self.assertIn("已取消本次查询", sends[0][1])
        self.assertIsNone(bot.session.get(GROUP_ID, USER_ID))

    def test_quit_with_at_no_session(self):
        bot, sends = _make_bot()
        bot.handle(_inc(True, "quit"))                         # @bot quit 但无会话
        self.assertEqual(len(sends), 1)
        self.assertIn("当前没有进行中的查询", sends[0][1])
        self.assertIn(f"[CQ:at,qq={USER_ID}]", sends[0][1])

    def test_quit_ignored_without_session_and_at(self):
        bot, sends = _make_bot()
        bot.handle(_inc(False, "quit"))                        # 无会话且未 @ -> 忽略
        self.assertEqual(len(sends), 0)

    def test_replies_are_user_attributed(self):
        """所有文本回复带 @ 归属（按用户分类），不同用户会话互不串线。"""
        bot, sends = _make_bot()
        bot.handle(_inc(True, "live 2026年7月"))
        self.assertEqual(len(sends), 1)
        self.assertTrue(sends[0][1].startswith(f"[CQ:at,qq={USER_ID}]"), sends[0][1])
        # 第二个用户独立会话
        inc2 = Incoming(group_id=GROUP_ID, user_id="999999", at_bot=True, text="live 13thLIVE")
        bot.handle(inc2)
        self.assertEqual(len(sends), 2)
        self.assertTrue(sends[1][1].startswith("[CQ:at,qq=999999]"), sends[1][1])
        # 两个用户会话互不干扰
        bot.handle(Incoming(group_id=GROUP_ID, user_id=USER_ID, at_bot=False, text="1"))
        self.assertEqual(bot.session.get(GROUP_ID, USER_ID)["kind"], CTX_EVENT)  # 用户1 -> IWSF
        self.assertEqual(bot.session.get(GROUP_ID, "999999")["kind"], CTX_EVENT)  # 用户2 保留 13thLIVE


class TestReplyAttribution(unittest.TestCase):
    """回复 @ 归属：CQ 前缀 -> 独立 at 段（NapCat array 形态不解析 text 内 CQ 码，2026-08-27 修正）。"""

    def test_extract_at_qq(self):
        from songbot.bot import _extract_at_qq
        self.assertEqual(_extract_at_qq(""), (None, ""))
        self.assertEqual(_extract_at_qq("[CQ:at,qq=123456789] hi"), ("123456789", "hi"))
        self.assertEqual(_extract_at_qq("[CQ:at,qq=123456789]"), ("123456789", ""))
        self.assertEqual(_extract_at_qq("[CQ:at,qq=123456789]  a b"), ("123456789", "a b"))
        self.assertEqual(_extract_at_qq("plain"), (None, "plain"))
        self.assertEqual(_extract_at_qq("x [CQ:at,qq=1]"), (None, "x [CQ:at,qq=1]"))

    @mock.patch("songbot.bot.push")
    def test_default_sender_converts_cq_to_ats(self, mock_push):
        mock_push.return_value = [PushResult(group_id="111", ok=True, message_id="1")]
        bot = SongBot(config=BotConfig())
        self.assertTrue(bot._default_sender("111", "[CQ:at,qq=123456789] 回复内容", []))
        msg = mock_push.call_args[0][0]
        self.assertEqual(msg.ats, ["123456789"])
        self.assertEqual(msg.segments, ["回复内容"])
        self.assertEqual(msg.images, [])

    @mock.patch("songbot.bot.push")
    def test_default_sender_image_with_at(self, mock_push):
        mock_push.return_value = [PushResult(group_id="111", ok=True, message_id="1")]
        img = Path(_ws_tmp("img_")) / "fake.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        bot = SongBot(config=BotConfig())
        self.assertTrue(bot._default_sender("111", "[CQ:at,qq=123456789]", [img]))
        msg = mock_push.call_args[0][0]
        self.assertEqual(msg.ats, ["123456789"])
        self.assertEqual(msg.segments, [])                      # 纯 @ + 图片
        self.assertEqual(len(msg.images), 1)

    @mock.patch("songbot.bot.push")
    def test_default_sender_no_at(self, mock_push):
        mock_push.return_value = [PushResult(group_id="111", ok=True, message_id="1")]
        bot = SongBot(config=BotConfig())
        self.assertTrue(bot._default_sender("111", "普通回复", []))
        msg = mock_push.call_args[0][0]
        self.assertEqual(msg.ats, [])
        self.assertEqual(msg.segments, ["普通回复"])

    def test_push_wire_format_with_ats(self):
        """M6 发送层：ats 拼为独立 at 段，与文本同一条消息发出（wire 格式验证）。"""
        captured: dict = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"status": "ok", "data": {"message_id": "1"}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cfg = NotifierConfig(base_url="http://x", merge_forward=False)
        msg = PushMessage(group_ids=["111"], segments=["回复内容"], images=[], link="", ats=["123"])
        results = push(msg, config=cfg, client=client)
        self.assertTrue(results[0].ok)
        segs = captured["body"]["message"]
        self.assertEqual(segs[0], {"type": "at", "data": {"qq": "123"}})
        self.assertEqual(segs[1], {"type": "text", "data": {"text": "回复内容"}})

    def test_push_wire_format_at_with_image(self):
        captured: dict = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"status": "ok", "data": {"message_id": "1"}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cfg = NotifierConfig(base_url="http://x", merge_forward=False)
        msg = PushMessage(group_ids=["111"], segments=[], images=["base64://abc"], link="", ats=["123"])
        results = push(msg, config=cfg, client=client)
        self.assertTrue(results[0].ok)
        segs = captured["body"]["message"]
        self.assertEqual(segs[0], {"type": "at", "data": {"qq": "123"}})
        self.assertEqual(segs[1], {"type": "image", "data": {"file": "base64://abc"}})


class TestStartStopNotices(unittest.TestCase):
    """S7：启动/结束状态通知（需求 2）+ 优雅停止文件机制（全部离线）。"""

    def test_startup_text_format(self):
        cfg = BotConfig(ttl_sec=300.0)
        text = bot_mod._startup_text(cfg, port=8090, event_count=125, latest_year=2026,
                                     song_index_ready=False, dry_run=False)
        self.assertIn("songbot 已启动", text)
        self.assertIn("127.0.0.1:8090", text)
        self.assertIn("125 个", text)
        self.assertIn("2026 年", text)
        self.assertIn("后台构建中", text)
        self.assertIn("正式", text)
        self.assertNotIn("DRY-RUN", text)

    def test_startup_text_dry_run(self):
        cfg = BotConfig()
        text = bot_mod._startup_text(cfg, port=8090, event_count=1, latest_year=2026,
                                     song_index_ready=True, dry_run=True)
        self.assertIn("DRY-RUN", text)
        self.assertIn("已就绪", text)

    def test_shutdown_text_format(self):
        text = bot_mod._shutdown_text(BotConfig(), "2026-08-27 10:00:00", "2026-08-27 12:30:00")
        self.assertIn("songbot 已停止", text)
        self.assertIn("2026-08-27 10:00:00", text)
        self.assertIn("2026-08-27 12:30:00", text)

    def test_load_config_notify_groups_inline_list(self):
        td = _ws_tmp("cfg_notify_")
        try:
            p = os.path.join(td, "config.yaml")
            with open(p, "w", encoding="utf-8") as f:
                f.write("songbot:\n  notify_groups: [\"111\", \"222\"]\n"
                        "  stop_file: data/stop.txt\n")
            cfg = load_bot_config(p)
            self.assertEqual(cfg.notify_groups, ["111", "222"])
            self.assertEqual(cfg.stop_file, "data/stop.txt")
        finally:
            _rm_ws_tmp(td)

    def test_load_config_notify_groups_comma(self):
        td = _ws_tmp("cfg_notify2_")
        try:
            p = os.path.join(td, "config.yaml")
            with open(p, "w", encoding="utf-8") as f:
                f.write("songbot:\n  notify_groups: 111, 222\n")
            cfg = load_bot_config(p)
            self.assertEqual(cfg.notify_groups, ["111", "222"])
        finally:
            _rm_ws_tmp(td)

    def test_load_config_notify_groups_absent_defaults_empty(self):
        self.assertEqual(BotConfig().notify_groups, [])
        self.assertEqual(BotConfig().stop_file, "")

    def test_load_config_notify_templates(self):
        """config 自定义 notify_startup / notify_shutdown；\\n 字面还原为换行；缺失用默认。"""
        td = _ws_tmp("cfg_tpl_")
        try:
            p = os.path.join(td, "config.yaml")
            with open(p, "w", encoding="utf-8") as f:
                f.write("songbot:\n"
                        '  notify_startup: "sbot UP {events} ev / {mode}\\n{time}"\n'
                        '  notify_shutdown: "sbot DOWN {started} -> {stopped}"\n')
            cfg = load_bot_config(p)
            self.assertEqual(cfg.notify_startup, "sbot UP {events} ev / {mode}\n{time}")
            self.assertEqual(cfg.notify_shutdown, "sbot DOWN {started} -> {stopped}")
        finally:
            _rm_ws_tmp(td)

    def test_notify_template_missing_or_bad_uses_default(self):
        self.assertEqual(bot_mod._notify_template({}, "notify_startup", "DEF"), "DEF")
        self.assertEqual(bot_mod._notify_template({"notify_startup": "  "}, "notify_startup", "DEF"), "DEF")
        self.assertEqual(bot_mod._notify_template({"notify_startup": 123}, "notify_startup", "DEF"), "DEF")

    def test_render_template_unknown_placeholder_preserved(self):
        text = bot_mod._render_template("a {port} b {unknown} c", port=8090)
        self.assertEqual(text, "a 8090 b {unknown} c")

    def test_startup_text_custom_template(self):
        cfg = BotConfig(notify_startup="UP {port} / {events} / {year} / {song_index} / {ttl} / {mode} / {time}")
        text = bot_mod._startup_text(cfg, port=9000, event_count=7, latest_year=2025,
                                     song_index_ready=True, dry_run=False)
        self.assertIn("UP 9000 / 7 / 2025 / 已就绪 / 300 / 正式 / ", text)

    def test_shutdown_text_custom_template(self):
        cfg = BotConfig(notify_shutdown="DOWN {started} -> {stopped} @{time}")
        text = bot_mod._shutdown_text(cfg, "2026-08-27 10:00:00", "2026-08-27 12:30:00")
        self.assertEqual(text, "DOWN 2026-08-27 10:00:00 -> 2026-08-27 12:30:00 @2026-08-27 12:30:00")

    def test_notify_groups_sends_to_each(self):
        sent: list[tuple] = []

        def sender(group_id: str, text: str, image_paths) -> bool:
            sent.append((group_id, text, image_paths))
            return True

        n = bot_mod._notify_groups(sender, ["111", "222"], "hello")
        self.assertEqual(n, 2)
        self.assertEqual([s[0] for s in sent], ["111", "222"])
        self.assertTrue(all(s[1] == "hello" for s in sent))
        self.assertTrue(all(s[2] == [] for s in sent))

    def test_notify_groups_exception_tolerated(self):
        calls: list[str] = []

        def sender(group_id: str, text: str, image_paths) -> bool:
            calls.append(group_id)
            if group_id == "111":
                raise RuntimeError("napcat down")
            return True

        n = bot_mod._notify_groups(sender, ["111", "222", "333"], "hi")
        self.assertEqual(n, 2)          # 222/333 成功；111 异常仅告警
        self.assertEqual(calls, ["111", "222", "333"])

    def test_wait_for_stop_existing_file_returns(self):
        td = _ws_tmp("stop_")
        try:
            stop_file = os.path.join(td, "songbot.stop")
            with open(stop_file, "w", encoding="utf-8") as f:
                f.write("")
            bot_mod._wait_for_stop(stop_file, interval=0.05)   # 文件已存在：立即返回
            self.assertTrue(os.path.exists(stop_file))          # 本函数不删除
        finally:
            _rm_ws_tmp(td)

    def test_wait_for_stop_file_created_later(self):
        td = _ws_tmp("stop2_")
        try:
            stop_file = os.path.join(td, "songbot.stop")

            def create_later():
                time.sleep(0.15)
                with open(stop_file, "w", encoding="utf-8") as f:
                    f.write("")

            t = threading.Thread(target=create_later)
            t.start()
            bot_mod._wait_for_stop(stop_file, interval=0.05)   # 文件出现后返回
            t.join()
        finally:
            _rm_ws_tmp(td)

    def test_remove_stop_file(self):
        td = _ws_tmp("stop3_")
        try:
            stop_file = os.path.join(td, "songbot.stop")
            with open(stop_file, "w", encoding="utf-8") as f:
                f.write("")
            bot_mod._remove_stop_file(stop_file)
            self.assertFalse(os.path.exists(stop_file))
            bot_mod._remove_stop_file("")        # 空串：无操作不报错
            bot_mod._remove_stop_file(stop_file)  # 已删除：再删不报错
        finally:
            _rm_ws_tmp(td)


if __name__ == "__main__":
    unittest.main()
