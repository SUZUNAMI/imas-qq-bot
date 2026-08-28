"""S6 主控串联 — 歌曲列表 bot（songbot）.

施工图：docs/S1-S7-taskplan.md §S6；实施计划：docs/modules/S6-bot-plan.md。

职责：常驻进程，串联 S1（事件索引）/ S2（详情抓取）/ S3（匹配+时间筛选）/
S4（图片渲染）/ S5（事件接收+会话），完成群内 ``@bot`` **两段交互**：

1. 第一段：``@bot live <LIVE 名/时间>``（**强制命令前缀**，S9 落地）
   - 先查绑定（``binding`` 设置的略缩，精确 normalize 匹配）；
   - 名称唯一多日事件 → 回复「事件名 + 子列表（DAY1/DAY2…+日期）」，会话记该事件；
   - 名称唯一单页事件 → 直接抓详情 → 渲染 PNG → 发图；
   - 多候选 → 序号候选列表，会话记候选；无命中 → 未找到 + 用法；
   - 时间（``2026年7月`` / ``7月`` / ``2026-07``）→ 按年/月筛选回复 LIVE 列表，会话记候选；
   - ``song <歌名>``（S8）：歌曲反向索引反查该歌出现过的 LIVE（序号+日期）→ 选序号 → 出图；
   - ``binding <略缩> <事件名>`` / ``unbind <略缩>`` / ``bindings``：绑定别名管理（S9）；
   - ``update live``：手动全量刷新（S9，``refresh_all``：重抓列表 → 重建事件索引 → 落盘缓存 → S8 歌曲索引钩子）；
2. 第二段：无 ``@`` 回复 ``DAY1`` / 序号 / 公演名 → 定位子公演/候选 → 抓详情 → 渲染 → 发图 → 清会话。

关键实现点
----------
- 发送层**复用 ``ref/m6_notifier.py``**（``push`` / ``load_config`` / ``_read_config_file``），
  图片用 ``[CQ:image,file=base64://<png_base64>]``（``PushMessage.images`` 传 ``base64://`` 前缀）；
  回复统一 ``merge_forward=False``（避免短回复也包成合并聊天记录）；
- 图片发送失败 → **回退纯文本歌单**（``setlist_text``）并告警（风险表约定）；
- 事件索引：进程内缓存 + 可选落盘 JSON（``songbot.index_cache``，TTL 默认 24h，``--no-cache`` 强制重抓）；
- 绑定存储：``BindingStore``（``songbot/s9_binding.py``），JSON 落盘（``songbot.bindings_file``，
  默认 ``data/songbot_bindings.json``），启动加载、变更即存；
- 歌曲反向索引（S8，``songbot/s8_song_index.py``）：``songbot.song_index_cache`` 落盘 JSON
  （默认 ``data/songbot_song_index.json``）；启动优先加载缓存、无缓存则**后台线程全量构建**
  （构建中 ``song`` 查询回「歌曲索引构建中…」）；每次 ``song`` 查询前**增量刷新**
  （重抓列表页 → 按列表顺序扫描详情 URL，**遇到第一个已收录即停止**，仅抓新增并入索引）；
  ``update live`` 钩子（``song_refresher``）用最新事件全量重建歌曲索引；
- S10（2026-08-27 计划拍板）：**@only 门控**——未 ``at_bot`` 的消息一律忽略
  （含会话二次确认，**每轮回复都需 @bot**）；**列表类回复图片化**——候选/子列表/
  时间筛选/歌曲出现/bindings 六类列表改走 ``render_list``（S4 泛化，``_send_list``）
  发图（图内 footer 统一「回复序号」，序号即会话确认序号）；``format_*`` 纯文本函数
  保留（dry-run / 图片失败回退文本用）；
- 依赖全部可注入（events / setlist_client / renderer / list_renderer / sender / session /
  bindings / song_index / song_refresher / clock），离线单测零网络；
- 日志同主项目 M7 习惯：UTF-8 + ``data/logs/songbot.log`` RotatingFileHandler，异常不退出；
- S7（2026-08-27）：启动成功/优雅停止时向 ``songbot.notify_groups``（主群+测试群）发状态通知
  （``_startup_text``/``_shutdown_text``/``_notify_groups``）；主循环监听停止文件
  （``songbot.stop_file``，``_wait_for_stop``），``stop_songbot.cmd`` 写入即优雅退出并发停止通知；
  强制 kill 无法发停止通知（文档注明）。
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional, Union

# ---------------------------------------------------------------------------
# sys.path：ref/（复用 M6 发送层）+ vendor/（依赖兜底）
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "ref"), os.path.join(_ROOT, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import httpx  # noqa: E402  （送达确认查询用；vendor 已在上面注入 sys.path）

from m6_notifier import _coerce_group_ids  # noqa: E402  （群号列表解析，兼容 YAML 子集内联列表形态）
from m6_notifier import _get_self_info  # noqa: E402  （送达确认用：bot 自身 uin）
from m6_notifier import _read_config_file  # noqa: E402  （YAML 子集解析，复用单一事实源）
from m6_notifier import load_config as load_notifier_config  # noqa: E402
from m6_notifier import push  # noqa: E402
from models import PushMessage  # noqa: E402  （ref/models.py，契约单一事实源）

from songbot.models_song import Appearance, Event, Setlist, SongEntry  # noqa: E402
from songbot.s1_fetch_events import DEFAULT_HEADERS, EVENT_LIST_URL, FetchError, fetch_events  # noqa: E402
from songbot.s2_fetch_setlist import fetch_setlist  # noqa: E402
from songbot.s3_match import (  # noqa: E402
    classify_query,
    filter_by_time,
    match_events,
    match_sub,
    normalize,
    parse_time_query,
    split_command,
)
from songbot.s4_render import render_list, render_setlist, warmup_browser  # noqa: E402
from songbot.s5_receiver import DEFAULT_PORT, EventReceiver, Incoming, SessionStore  # noqa: E402
from songbot.s8_song_index import (  # noqa: E402
    SongIndex,
    _appearance_specs,
    build_song_index,
    load_song_index,
    match_songs,
    refresh_song_index,
    save_song_index,
)
from songbot.s9_binding import (  # noqa: E402
    DEFAULT_BINDINGS_FILE,
    BindingStore,
    event_from_dict,
    event_to_dict,
)

logger = logging.getLogger("songbot.bot")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_TTL_SEC = 300.0                # 会话 TTL（秒），与 S5 默认一致
DEFAULT_REPLY_LIMIT = 10               # 时间筛选/候选列表单次回复上限（超出提示「还有 N 场」）
DEFAULT_CACHE_TTL_SEC = 86400          # 事件索引缓存有效期（秒），默认 24h
SONG_REFRESH_FETCH_TIMEOUT = 5.0       # 增量刷新列表页抓取短超时（秒），断网快速失败不阻塞
LOG_DIR = os.path.join(_ROOT, "data", "logs")
LOG_FILE = os.path.join(LOG_DIR, "songbot.log")

# 启动/停止状态通知默认文案（config.yaml songbot.notify_startup / notify_shutdown 可覆盖，仿 M7）。
# 占位符：startup = {port}/{events}/{year}/{song_index}/{ttl}/{mode}/{time}；
#         shutdown = {started}/{stopped}/{time}。未知占位符原样保留不崩溃。
DEFAULT_NOTIFY_STARTUP = (
    "songbot 已启动（歌曲列表查询）\n"
    "监听 127.0.0.1:{port}/event · 事件索引 {events} 个（最新 {year} 年）\n"
    "歌曲反向索引：{song_index}\n"
    "会话 TTL {ttl}s · 模式：{mode}"
)
DEFAULT_NOTIFY_SHUTDOWN = (
    "songbot 已停止\n"
    "启动于 {started} · 停止于 {stopped}"
)

# 管理命令权限（2026-08-27 S9 追加拍板）：binding / unbind / bindings / update live
# 仅群主（owner）与管理员（administrator）可用（sender.role，OneBot 11）；member 拒绝。
ADMIN_ROLES: frozenset = frozenset({"owner", "administrator"})
MANAGE_COMMANDS: frozenset = frozenset({"binding", "unbind", "bindings", "update"})
ADMIN_DENY_TEXT = "该命令仅群主/管理员可用（binding / unbind / bindings / update live）"

USAGE = (
    "用法（命令前缀）：\n"
    "@bot live <LIVE 名或年月>（如 live IWSF2026 / live 2026年7月）\n"
    "@bot song <歌名>（如 song Marionetteは眠らない / song Dance in the Light）\n"
    "@bot binding <略缩> <事件名> · @bot unbind <略缩> · @bot bindings\n"
    "@bot update live（手动全量刷新）"
)

# 会话 context 的 kind 常量
CTX_EVENT = "event"                    # 待确认的多日事件（{kind, event: Event}）
CTX_CANDIDATES = "candidates"          # 待选择的候选列表（{kind, events: list[Event]}）
CTX_SONG_CANDIDATES = "song_candidates"  # S8：待选择的候选歌曲（{kind, songs: list[SongEntry]}）
CTX_SONG_LIVES = "song_lives"          # S8：某歌出现过的 LIVE 列表（{kind, song, lives: list[Appearance]}）

# 回复归属：text 开头的 [CQ:at,qq=N]（_send_text 拼）-> _default_sender 转独立 at 段
# （NapCat array 消息形态下 CQ 码嵌 text 会字面显示，2026-08-27 修正）
_AT_PREFIX_RE = re.compile(r"^\[CQ:at,qq=([0-9]+)\]\s*")


# ---------------------------------------------------------------------------
# 配置：config.yaml 的 songbot: 段
# ---------------------------------------------------------------------------
@dataclass
class BotConfig:
    """songbot 主控配置（load_bot_config() 产出；命令行参数可覆盖）。"""

    port: int = DEFAULT_PORT                    # 接收器监听端口（NapCat postUrls 指向）
    ttl_sec: float = DEFAULT_TTL_SEC            # 两段交互会话 TTL（秒）
    event_list_url: str = EVENT_LIST_URL        # 事件列表页 URL（纯 HTTP）
    index_cache: str = ""                       # 事件索引落盘缓存路径；空串关闭
    index_cache_ttl_sec: float = DEFAULT_CACHE_TTL_SEC  # 缓存有效期（秒）
    render_dir: Optional[str] = None            # 图片输出根目录；None = render_setlist 默认
    reply_limit: int = DEFAULT_REPLY_LIMIT      # 单次回复列表上限
    bindings_file: str = DEFAULT_BINDINGS_FILE  # 绑定别名落盘路径（S9）；空串关闭持久化
    song_index_cache: str = ""                  # 歌曲反向索引落盘缓存路径（S8）；空串关闭持久化
    notify_groups: list[str] = field(default_factory=list)  # 启动/结束状态通知群（S7）；空列表关闭
    stop_file: str = ""                         # 优雅停止请求文件（S7）；空串关闭（Ctrl+C 停止）
    notify_startup: str = DEFAULT_NOTIFY_STARTUP    # 启动通知文案模板（config 可覆盖；占位符见常量注释）
    notify_shutdown: str = DEFAULT_NOTIFY_SHUTDOWN  # 停止通知文案模板（同上）


def _cfg_int(section: dict, key: str, default: int) -> int:
    try:
        return int(section[key])
    except (KeyError, TypeError, ValueError):
        return default


def _cfg_float(section: dict, key: str, default: float) -> float:
    try:
        return float(section[key])
    except (KeyError, TypeError, ValueError):
        return default


def _cfg_str(section: dict, key: str, default: str) -> str:
    v = section.get(key)
    return str(v).strip() if v is not None and str(v).strip() else default


def _notify_template(section: dict, key: str, default: str) -> str:
    """读 songbot 段通知文案模板（key 如 notify_startup）；缺失/非字符串用默认文案。

    注意：YAML 子集解析器不解码转义，config 里的 ``\\n`` 在此统一还原为换行（仿 ref/main.py）。
    """
    v = section.get(key)
    if not isinstance(v, str) or not v.strip():
        return default
    return v.replace("\\n", "\n")


def _render_template(template: str, **kwargs) -> str:
    """渲染通知模板；未知占位符原样保留（如 {port}），不因模板缺字段而崩溃（仿 ref/main.py）。"""

    class _Lenient(dict):
        def __missing__(self, key):  # noqa: D105 — 未知占位符保留原文
            return "{" + key + "}"

    return template.format_map(_Lenient(**kwargs))


def load_bot_config(config_path: Optional[str] = None) -> BotConfig:
    """读 config.yaml 的 ``songbot:`` 段；文件缺失/损坏/缺字段回退内置默认。

    :param config_path: 配置文件路径（缺省项目根 config.yaml）
    """
    path = config_path or os.path.join(_ROOT, "config.yaml")
    cfg: dict = {}
    if os.path.isfile(path):
        try:
            cfg = _read_config_file(path)
        except Exception:  # noqa: BLE001 — 配置缺失/损坏回退默认
            logger.warning("config.yaml 读取失败（回退默认 songbot 配置）")
            cfg = {}
    s = cfg.get("songbot") if isinstance(cfg, dict) else None
    s = s if isinstance(s, dict) else {}
    return BotConfig(
        port=_cfg_int(s, "port", DEFAULT_PORT),
        ttl_sec=_cfg_float(s, "ttl_sec", DEFAULT_TTL_SEC),
        event_list_url=_cfg_str(s, "event_list_url", EVENT_LIST_URL),
        index_cache=_cfg_str(s, "index_cache", ""),
        index_cache_ttl_sec=_cfg_float(s, "index_cache_ttl_sec", DEFAULT_CACHE_TTL_SEC),
        render_dir=_cfg_str(s, "render_dir", "") or None,
        reply_limit=_cfg_int(s, "reply_limit", DEFAULT_REPLY_LIMIT),
        bindings_file=_cfg_str(s, "bindings_file", DEFAULT_BINDINGS_FILE),
        song_index_cache=_cfg_str(s, "song_index_cache", ""),
        notify_groups=_coerce_group_ids(s.get("notify_groups")),
        stop_file=_cfg_str(s, "stop_file", ""),
        notify_startup=_notify_template(s, "notify_startup", DEFAULT_NOTIFY_STARTUP),
        notify_shutdown=_notify_template(s, "notify_shutdown", DEFAULT_NOTIFY_SHUTDOWN),
    )


# ---------------------------------------------------------------------------
# 纯函数：回复排版（离线可单测）
# ---------------------------------------------------------------------------
def format_event_list(events: list[Event], limit: int = DEFAULT_REPLY_LIMIT) -> str:
    """事件列表排版（序号 + 日期 + 多日子项）；超出 limit 提示「还有 N 场」。"""
    lines: list[str] = []
    for i, ev in enumerate(events[:limit], 1):
        if ev.sub_events:
            subs = "、".join(f"{s.title}({s.date})" for s in ev.sub_events if s.date)
            lines.append(f"{i}. {ev.title}（多日：{subs}）")
        else:
            lines.append(f"{i}. {ev.title}（{ev.date}）" if ev.date else f"{i}. {ev.title}")
    if len(events) > limit:
        lines.append(f"…还有 {len(events) - limit} 场")
    return "\n".join(lines)


def _event_list_rows(events: list[Event]) -> list[tuple[str, str]]:
    """事件列表行（S10 render_list 用）：[(主文本=事件名, 副文本=日期/多日子项)]。

    序号由 render_list 自动 1 起；**全部事件都进图**（图片可分页，不再按
    ``reply_limit`` 截断——会话确认序号以全部事件为准，与图片序号一致）。
    """
    rows: list[tuple[str, str]] = []
    for ev in events:
        if ev.sub_events:
            subs = "、".join(f"{s.title}({s.date})" for s in ev.sub_events if s.date)
            rows.append((ev.title, f"多日：{subs}" if subs else "多日"))
        else:
            rows.append((ev.title, ev.date or ""))
    return rows


def format_sub_list(event: Event) -> str:
    """多日事件的子公演列表（1..N. 子公演名（日期）），末尾带二次确认提示。"""
    lines = [f"「{event.title}」"]
    for i, s in enumerate(event.sub_events, 1):
        lines.append(f"{i}. {s.title}（{s.date}）" if s.date else f"{i}. {s.title}")
    lines.append("回复序号、DAY1 或公演名")
    return "\n".join(lines)


def setlist_text(setlist: Setlist) -> str:
    """Setlist 纯文本歌单（图片发送失败时的兜底回复）。"""
    lines = [f"「{setlist.title}」"]
    if setlist.date_venue:
        lines.append(setlist.date_venue)
    if setlist.performers:
        lines.append("出演: " + "、".join(setlist.performers))
    for t in setlist.tracks:
        brand = f"[{t.brand}]" if t.brand else ""
        perf = "／".join(t.performers) if t.performers else ""
        tail = " ".join(x for x in (brand, perf) if x)
        lines.append(f"{t.no}. {t.title}" + (f" {tail}" if tail else ""))
    return "\n".join(lines)


def format_song_candidates(songs: list[SongEntry]) -> str:
    """候选歌曲列表排版（序号 + 歌名 + 出现 LIVE 数），末尾带二次确认提示（S8）。"""
    lines = [f"{i}. {s.title}（{len(s.appearances)} 场 LIVE）" for i, s in enumerate(songs, 1)]
    lines.append("回复序号或歌名")
    return "\n".join(lines)


def format_song_lives(entry: SongEntry) -> str:
    """某歌出现过的 LIVE 列表（序号 + 事件名 + 子公演 + 日期），末尾带二次确认提示（S8）。"""
    lines = [f"「{entry.title}」出现在 {len(entry.appearances)} 场 LIVE："]
    for i, a in enumerate(entry.appearances, 1):
        label = a.event_title
        if a.sub_title:
            label += f"（{a.sub_title}）"
        if a.date:
            label += f" {a.date}"
        lines.append(f"{i}. {label}")
    lines.append("回复序号查看该 LIVE 的歌曲列表")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 事件索引序列化（落盘缓存）
# ---------------------------------------------------------------------------
# 实现统一定义在 songbot/s9_binding.py（event_to_dict / event_from_dict），
# bot.py 复用同一格式（事件索引缓存与绑定存储共用）；以下为兼容别名。
_event_to_dict = event_to_dict
_event_from_dict = event_from_dict


def _events_to_dict(events: list[Event]) -> list[dict]:
    return [_event_to_dict(e) for e in events]


def _events_from_dict(data) -> list[Event]:
    return [_event_from_dict(d) for d in data if isinstance(d, dict)]


# ---------------------------------------------------------------------------
# 主控
# ---------------------------------------------------------------------------
def _extract_at_qq(text: str) -> tuple[Optional[str], str]:
    """提取文本开头的 ``[CQ:at,qq=N]``（回复归属）-> ``(qq, 剩余文本)``；无则 ``(None, 原文)``。

    纯函数（离线可单测）：``_send_text`` 拼出的归属前缀在此拆出，
    由 ``_default_sender`` 转成独立 at 段（CQ 码嵌 text 在 NapCat array 形态下会字面显示）。
    """
    if not text:
        return None, text
    m = _AT_PREFIX_RE.match(text)
    if m:
        return m.group(1), text[m.end():]
    return None, text


class SongBot:
    """两段交互处理链（事件回调入口 ``handle(Incoming)``）。

    依赖全部可注入（离线单测用 mock）：
    - ``events``：事件索引（None 时由 ``build_index()`` 构建）
    - ``setlist_client``：httpx.Client（注入 ``fetch_setlist``；None 自建）
    - ``renderer``：``callable(setlist, *, out_dir=None) -> list[Path]``（默认 ``render_setlist``）
    - ``list_renderer``：``callable(title, rows, *, out_dir=None, hint=...) -> list[Path]``
      （S10 默认 ``render_list``；列表类回复发图用）
    - ``sender``：``callable(group_id: str, text: str, image_paths: list[Path]) -> bool``
      （默认 ``_default_sender``：M6 push + base64:// 图片）
    - ``session``：SessionStore（默认按 cfg.ttl_sec 新建）
    - ``bindings``：BindingStore（默认按 cfg.bindings_file 新建；注入用内存/临时路径实例）
    - ``song_index``：歌曲反向索引 SongIndex（S8；None 时由 ``start_song_index()``
      加载缓存或后台全量构建；注入（测试）则直接使用）
    - ``song_refresher``：``callable(events: list[Event]) -> Optional[int]``
      （``update live`` 时重建歌曲反向索引并返回歌曲数；None = 跳过；main 中默认接 S8 实现）
    """

    def __init__(
        self,
        *,
        config: Optional[BotConfig] = None,
        events: Optional[list[Event]] = None,
        setlist_client=None,
        renderer: Optional[Callable] = None,
        list_renderer: Optional[Callable] = None,
        sender: Optional[Callable] = None,
        session: Optional[SessionStore] = None,
        bindings: Optional[BindingStore] = None,
        song_index: Optional[SongIndex] = None,
        song_refresher: Optional[Callable] = None,
    ):
        self.cfg = config or load_bot_config()
        self.events = list(events) if events else []
        self.setlist_client = setlist_client
        self.renderer = renderer or render_setlist
        self.list_renderer = list_renderer or render_list   # S10：列表类回复发图
        self.sender = sender or self._default_sender
        self.session = session or SessionStore(ttl=self.cfg.ttl_sec)
        self.bindings = bindings if bindings is not None else BindingStore(path=self.cfg.bindings_file)
        self.song_index = song_index            # S8：歌曲反向索引（None = 未就绪/后台构建中）
        self.song_index_lock = threading.Lock()  # 构建/刷新串行化
        self.song_refresher = song_refresher      # S9 钩子：update live 重建歌曲反向索引
        self.latest_year = max((int(e.year) for e in self.events if str(e.year).isdigit()),
                               default=datetime.now().year)
        self._notifier = None  # 惰性加载（首次发送时）

    # ------------------------------------------------------------------
    # 索引构建（进程内 + 可选落盘 JSON）
    # ------------------------------------------------------------------
    def build_index(self) -> list[Event]:
        """构建事件索引：优先读有效缓存，否则 ``fetch_events`` 并落盘。"""
        cfg = self.cfg
        cache_path = cfg.index_cache
        if cache_path:
            p = Path(cache_path)
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    fetched_at = float(data.get("fetched_at") or 0)
                    age = time.time() - fetched_at
                    if age <= cfg.index_cache_ttl_sec:
                        events = _events_from_dict(data.get("events") or [])
                        if events:
                            logger.info("事件索引缓存命中（%.0fs 前抓取，%d 个）: %s",
                                        age, len(events), p)
                            self.events = events
                            self._refresh_latest_year()
                            return events
                    logger.info("事件索引缓存过期（%.0fs > TTL %.0fs），重抓",
                                age, cfg.index_cache_ttl_sec)
                except Exception as exc:  # noqa: BLE001 — 缓存损坏则重抓
                    logger.warning("事件索引缓存读取失败（将重抓）: %s: %s", p, exc)
        try:
            events = fetch_events(cfg.event_list_url)
        except FetchError:
            # 重抓失败但有过期缓存：回退缓存并告警（比裸奔好）
            if cache_path and p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    events = _events_from_dict(data.get("events") or [])
                    if events:
                        logger.warning("事件重抓失败，回退过期缓存（%d 个事件）", len(events))
                        self.events = events
                        self._refresh_latest_year()
                        return events
                except Exception:  # noqa: BLE001
                    pass
            raise
        self.events = events
        self._refresh_latest_year()
        self._save_index_cache(events)
        return events

    def _save_index_cache(self, events: list[Event]) -> None:
        """事件索引落盘（build_index 抓新 / refresh_all 手动刷新共用）；失败仅告警。"""
        cache_path = self.cfg.index_cache
        if not cache_path:
            return
        try:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "fetched_at": time.time(), "source_url": self.cfg.event_list_url,
                "events": _events_to_dict(events),
            }
            Path(cache_path).write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            logger.info("事件索引已缓存: %s（%d 个事件）", cache_path, len(events))
        except OSError as exc:
            logger.warning("事件索引缓存写入失败（不影响运行）: %s", exc)

    def refresh_all(self) -> tuple[int, Optional[int]]:
        """``update live``：手动全量刷新（S9）。

        强制重抓事件列表 → 重建进程内事件索引 → 落盘缓存 → 歌曲反向索引钩子
        （``song_refresher``，S8 接入；重建期间旧索引继续服务，完成后原子替换）。

        :return: ``(事件数, 歌曲数或 None)``（歌曲数 None = 未接入歌曲索引）
        """
        events = fetch_events(self.cfg.event_list_url)
        self.events = events
        self._refresh_latest_year()
        self._save_index_cache(events)
        song_count: Optional[int] = None
        if self.song_refresher is not None:
            try:
                song_count = int(self.song_refresher(events) or 0)
            except Exception as exc:  # noqa: BLE001 — 歌曲索引失败不阻断事件刷新
                logger.exception("歌曲反向索引刷新失败（不影响事件索引）: %s", exc)
                song_count = None
        logger.info("update live 完成：%d 事件%s", len(events),
                    f" / {song_count} 歌曲" if song_count is not None else "")
        return len(events), song_count

    def _refresh_latest_year(self) -> None:
        self.latest_year = max((int(e.year) for e in self.events if str(e.year).isdigit()),
                               default=datetime.now().year)

    # ------------------------------------------------------------------
    # 歌曲反向索引（S8）：启动加载/后台构建 + 查询前增量刷新 + update live 钩子
    # ------------------------------------------------------------------
    def start_song_index(self) -> None:
        """启动歌曲反向索引：优先加载落盘缓存；无缓存则**后台线程**全量构建（不阻塞接收）。

        索引未就绪期间 ``song`` 查询回「歌曲索引构建中…」；构建完成自动落盘缓存。
        """
        if self.song_index is not None:
            return                                    # 已注入（测试）或已就绪
        path = self.cfg.song_index_cache
        if path:
            idx = load_song_index(path)
            if idx is not None:
                self.song_index = idx
                logger.info("歌曲索引缓存加载：%s（%d 首歌 / %d 个来源页）",
                            path, len(idx.entries), len(idx.source_urls))
                return
        threading.Thread(target=self._build_song_index_bg, name="song-index-build",
                         daemon=True).start()

    def _build_song_index_bg(self) -> None:
        """后台全量构建歌曲索引（首次约 331 个详情页，需数分钟）；完成后落盘。"""
        try:
            with self.song_index_lock:
                if self.song_index is not None:
                    return
                total = len(_appearance_specs(self.events))
                logger.info("歌曲索引后台全量构建开始（%d 个详情页，首次约需数分钟）…", total)
                idx = build_song_index(self.events, self._fetch_setlist_for_index)
                self.song_index = idx
                logger.info("歌曲索引后台构建完成：%d 首歌 / %d 个来源页",
                            len(idx.entries), len(idx.source_urls))
                self._song_index_save()
        except Exception:  # noqa: BLE001 — 构建失败不崩进程，song 查询继续提示未就绪
            logger.exception("歌曲索引后台构建失败（song 查询将提示索引未就绪）")

    def _fetch_setlist_for_index(self, url: str):
        """歌曲索引抓取用：复用注入的 setlist_client（离线单测 MockTransport 同路径）。"""
        return fetch_setlist(url, client=self.setlist_client)

    def _song_index_save(self) -> None:
        """歌曲索引落盘缓存；失败仅告警（不影响运行）。"""
        path = self.cfg.song_index_cache
        if not path or self.song_index is None:
            return
        try:
            save_song_index(self.song_index, path)
        except OSError as exc:
            logger.warning("歌曲索引缓存写入失败（不影响运行）: %s", exc)

    def _refresh_song_index(self) -> Optional[int]:
        """增量刷新歌曲反向索引（**手动触发**，``refresh`` 命令对所有人开放）：重抓列表页 ->
        diff 新增详情 URL -> 并入索引。

        列表页重抓用短超时（SONG_REFRESH_FETCH_TIMEOUT）；失败沿用现有索引返回 None；
        仅新增来源页时落盘。返回新增来源页数（0 = 无新增）。
        """
        if self.song_index is None:
            return None
        with self.song_index_lock:
            try:
                fresh = fetch_events(
                    self.cfg.event_list_url,
                    client=httpx.Client(timeout=SONG_REFRESH_FETCH_TIMEOUT,
                                        headers=DEFAULT_HEADERS, follow_redirects=True),
                )
            except FetchError:
                logger.info("增量刷新列表页抓取失败（沿用现有歌曲索引）")
                return None
            before = len(self.song_index.source_urls)
            refresh_song_index(self.song_index, fresh, self._fetch_setlist_for_index)
            added = len(self.song_index.source_urls) - before
            if added:
                logger.info("歌曲索引增量刷新：新增 %d 个来源页", added)
                self._song_index_save()
            return added

    def _song_refresher(self, events: list[Event]) -> int:
        """S9 ``update live`` 钩子：用最新事件列表**全量重建**歌曲反向索引，返回歌曲数。"""
        with self.song_index_lock:
            idx = build_song_index(events, self._fetch_setlist_for_index)
            self.song_index = idx
            self._song_index_save()
            logger.info("update live 重建歌曲索引：%d 首歌 / %d 个来源页",
                        len(idx.entries), len(idx.source_urls))
            return len(idx.entries)

    # ------------------------------------------------------------------
    # 处理链入口（EventReceiver 回调）
    # ------------------------------------------------------------------
    def handle(self, inc: Incoming) -> None:
        """一条群消息事件 -> 两段交互处理（异常只记日志，不退出）。"""
        try:
            self._handle(inc)
        except Exception:  # noqa: BLE001 — 单条消息处理异常不崩进程
            logger.exception("处理消息异常: %r", inc)

    def _handle(self, inc: Incoming) -> None:
        text = (inc.text or "").strip()
        # S10 @only 门控：未 @bot 的消息一律忽略（含会话二次确认——每轮回复都需 @bot；
        # 之前「有会话但无 @ → 二次确认/提示」路径按 2026-08-27 拍板决策删除）
        if not text or not inc.at_bot:
            return
        group, user = inc.group_id, inc.user_id
        # quit：用户取消当前等待（任何会话 context 通用；此处已保证 at_bot）
        if text.casefold() == "quit":
            had = self.session.get(group, user) is not None
            self.session.clear(group, user)
            if had:
                self._send_text(group, "已取消本次查询，可重新 @bot 发起（live / song）", user)
            else:
                self._send_text(group, "当前没有进行中的查询（@bot live / song 发起）", user)
            return
        ctx = self.session.get(group, user)
        if ctx is not None:
            if self._try_confirm(inc, ctx):
                return
            # @ 了 bot 且会话内解析失败 -> 回落第一段（视为新查询）
        self._first_stage(inc, text)

    # ------------------------------------------------------------------
    # 第二段：会话内二次确认
    # ------------------------------------------------------------------
    def _try_confirm(self, inc: Incoming, ctx: dict) -> bool:
        """尝试把本次消息当作会话的二次确认；处理成功返回 True，无法解析返回 False。"""
        text = (inc.text or "").strip()
        group, user = inc.group_id, inc.user_id
        kind = ctx.get("kind")

        if kind == CTX_EVENT:
            event = ctx.get("event")
            sub = match_sub(text, event) if isinstance(event, Event) else None
            if sub is None:
                return False
            self.session.clear(group, user)          # 先清会话（全流程耗时，防并发重入）
            self._full_flow(group, user, sub.url, sub.full_title or sub.title)
            return True

        if kind == CTX_CANDIDATES:
            cands = ctx.get("events") or []
            if not cands:
                return False
            if text.isdigit():
                idx = int(text) - 1
                if 0 <= idx < len(cands):
                    self._resolve_event(inc, cands[idx])
                    return True
                self._send_text(group, f"序号超出范围（1–{len(cands)}），请重新回复", user)
                return True
            hits = match_events(text, cands)
            if not hits:
                return False
            if len(hits) == 1:
                self._resolve_event(inc, hits[0])
                return True
            # 候选内仍多义：更新候选重新列出（S10：走 render_list 发图）
            self.session.set(group, user, {"kind": CTX_CANDIDATES, "events": hits})
            title = "还是没唯一确定，这些候选"
            fallback = (title + "：\n" + format_event_list(hits, self.cfg.reply_limit)
                        + "\n回复序号或 LIVE 名")
            self._send_list(group, user, title, _event_list_rows(hits), fallback)
            return True

        if kind == CTX_SONG_CANDIDATES:
            # S8：候选歌曲 -> 序号选歌（列出 LIVE）/ 歌名再匹配（候选内）
            songs = ctx.get("songs") or []
            if not songs:
                return False
            if text.isdigit():
                idx = int(text) - 1
                if 0 <= idx < len(songs):
                    self._list_song_lives(group, user, songs[idx])
                    return True
                self._send_text(group, f"序号超出范围（1–{len(songs)}），请重新回复", user)
                return True
            hits = match_songs(text, songs)
            if not hits:
                return False
            if len(hits) == 1:
                self._list_song_lives(group, user, hits[0])
                return True
            self.session.set(group, user, {"kind": CTX_SONG_CANDIDATES, "songs": hits})
            title = "还是没唯一确定，这些候选"
            rows = [(s.title, f"{len(s.appearances)} 场 LIVE") for s in hits]
            fallback = title + "：\n" + format_song_candidates(hits)
            self._send_list(group, user, title, rows, fallback)
            return True

        if kind == CTX_SONG_LIVES:
            # S8：某歌的 LIVE 列表 -> 序号选 LIVE -> 全流程发图 -> 清会话
            lives = ctx.get("lives") or []
            if not lives:
                return False
            if text.isdigit():
                idx = int(text) - 1
                if 0 <= idx < len(lives):
                    self.session.clear(group, user)     # 先清会话（全流程耗时，防并发重入）
                    app = lives[idx]
                    label = app.event_title + (f"（{app.sub_title}）" if app.sub_title else "")
                    self._full_flow(group, user, app.url, label)
                    return True
                self._send_text(group, f"序号超出范围（1–{len(lives)}），请重新回复", user)
                return True
            return False                                # 非序号 -> 通用「没看懂」提示，保留会话
        return False

    # ------------------------------------------------------------------
    # 第一段：@bot + 命令（强制前缀：live / song（S8）/ binding / unbind / bindings / update（S9））
    # ------------------------------------------------------------------
    def _first_stage(self, inc: Incoming, text: str) -> None:
        group, user = inc.group_id, inc.user_id
        parsed = split_command(text)
        if parsed is None:
            # 无前缀 / 未知命令 -> 用法提示（强制前缀）
            self._send_text(group, "请用命令前缀（live / song / refresh / binding / unbind / bindings / update）。\n" + USAGE, user)
            return
        cmd, rest = parsed
        # 管理命令权限控制（S9）：binding/unbind/bindings/update 仅群主/管理员可用
        if cmd in MANAGE_COMMANDS and inc.role not in ADMIN_ROLES:
            self._send_text(group, ADMIN_DENY_TEXT, user)
            return
        if cmd == "live":
            self._handle_live(inc, rest)
        elif cmd == "song":
            self._handle_song(inc, rest)
        elif cmd == "refresh":
            self._handle_refresh(group, user)
        elif cmd == "binding":
            self._handle_binding(group, user, rest)
        elif cmd == "unbind":
            self._handle_unbind(group, user, rest)
        elif cmd == "bindings":
            self._handle_bindings(group, user)
        elif cmd == "update":
            self._handle_update(group, user, rest)
        else:
            # 识别但尚未接入的命令
            self._send_text(group, f"命令「{cmd}」尚未开放，可用：live / song / refresh / binding / unbind / bindings / update live", user)

    # ---------------- song：歌曲反查 LIVE（S8） ----------------
    def _handle_song(self, inc: Incoming, rest: str) -> None:
        """``@bot song <歌名>``：反查该歌出现过的 LIVE -> 唯一列 LIVE / 多候选列候选歌。"""
        group, user = inc.group_id, inc.user_id
        text = (rest or "").strip()
        if not text:
            self._send_text(group, "song 后要跟歌名，如：song Marionetteは眠らない / song Dance in the Light", user)
            return
        if self.song_index is None:
            self._send_text(group, "歌曲索引尚未构建完成（首次构建约需数分钟），请稍后再试", user)
            return
        hits = match_songs(text, self.song_index)
        if not hits:
            self._send_text(group, f"没有找到与「{text}」匹配的歌曲。\n" + USAGE, user)
            return
        if len(hits) == 1:
            self._list_song_lives(group, user, hits[0])
            return
        self.session.set(group, user, {"kind": CTX_SONG_CANDIDATES, "songs": hits})
        title = "找到多首候选歌曲，请选择"
        rows = [(s.title, f"{len(s.appearances)} 场 LIVE") for s in hits]
        fallback = title + "：\n" + format_song_candidates(hits)
        self._send_list(group, user, title, rows, fallback)

    def _list_song_lives(self, group: str, user: str, entry: SongEntry) -> None:
        """歌曲唯一落定：列出该歌出现过的 LIVE（序号+事件+子公演+日期），会话记 CTX_SONG_LIVES。"""
        if not entry.appearances:
            self._send_text(group, f"「{entry.title}」暂无 LIVE 出演记录", user)
            return
        self.session.set(group, user, {"kind": CTX_SONG_LIVES, "song": entry,
                                       "lives": entry.appearances})
        rows: list[tuple[str, str]] = []
        for a in entry.appearances:
            label = a.event_title + (f"（{a.sub_title}）" if a.sub_title else "")
            rows.append((label, a.date or ""))
        title = f"「{entry.title}」出现在 {len(entry.appearances)} 场 LIVE"
        self._send_list(group, user, title, rows, format_song_lives(entry))

    # ---------------- live：绑定 -> 时间 -> 名称 ----------------
    def _handle_live(self, inc: Incoming, rest: str) -> None:
        group, user = inc.group_id, inc.user_id
        text = (rest or "").strip()
        if not text:
            self._send_text(group, "live 后要跟 LIVE 名或年月，如：live IWSF2026 / live 2026年7月", user)
            return
        # 1) 先查绑定（精确 normalize 匹配；绑定只影响 live 分支，S9 计划 §0）
        bound = self.bindings.resolve(text)
        if bound is not None:
            ev = self._find_index_event(bound)
            if ev is None:
                # 绑定事件已不在索引（下架/改版）：忽略绑定并提示（S9 风险表）
                self._send_text(group,
                                f"绑定「{text}」指向的事件已不在索引中（可能已下架/改版），"
                                "可用 @bot bindings 查看、@bot unbind 删除后重新绑定", user)
                return
            self._resolve_event(inc, ev)
            return
        # 2) 时间查询
        qtype = classify_query(text)
        if qtype == "time":
            parsed = parse_time_query(text, self.latest_year)
            if parsed is None:
                self._send_text(group, f"无法解析「{text}」。" + USAGE, user)
                return
            year, month = parsed
            hits = filter_by_time(self.events, year, month)
            label = f"{year}年" + (f"{month}月" if month else "")
            if not hits:
                self._send_text(group, f"未找到 {label} 的 LIVE。试试 live + LIVE 名，如 live IWSF2026 / live 13thLIVE", user)
                return
            self.session.set(group, user, {"kind": CTX_CANDIDATES, "events": hits})
            title = f"{label} 的 LIVE（共 {len(hits)} 场）"
            fallback = (f"{label} 的 LIVE（共 {len(hits)} 场）：\n"
                        + format_event_list(hits, self.cfg.reply_limit)
                        + "\n回复序号或 LIVE 名")
            self._send_list(group, user, title, _event_list_rows(hits), fallback)
            return
        # 3) 名称匹配
        matches = match_events(text, self.events)
        if not matches:
            self._send_text(group, f"没有找到与「{text}」匹配的 LIVE。\n" + USAGE, user)
            return
        if len(matches) == 1:
            self._resolve_event(inc, matches[0])
            return
        self.session.set(group, user, {"kind": CTX_CANDIDATES, "events": matches})
        title = "找到多个匹配，请选择"
        fallback = (title + "：\n" + format_event_list(matches, self.cfg.reply_limit)
                    + "\n回复序号或 LIVE 名")
        self._send_list(group, user, title, _event_list_rows(matches), fallback)

    def _find_index_event(self, bound: Event) -> Optional[Event]:
        """把绑定存储的事件映射回**当前索引**中的事件（新鲜数据）。

        按 normalize(title) 相等匹配，回退 URL（单页）与子事件 URL（多日）匹配；
        绑定事件已不在索引（下架/改版）返回 None。
        """
        bn = normalize(bound.title)
        for ev in self.events:
            if normalize(ev.title) == bn:
                return ev
        if bound.url:
            for ev in self.events:
                if ev.url == bound.url:
                    return ev
        if bound.sub_events:
            urls = {s.url for s in bound.sub_events}
            for ev in self.events:
                if urls & {s.url for s in ev.sub_events}:
                    return ev
        return None

    # ---------------- binding / unbind / bindings ----------------
    def _handle_binding(self, group: str, user: str, rest: str) -> None:
        text = (rest or "").strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            self._send_text(group, "用法：binding <略缩> <事件名>，如 binding iwsf IDOL WORLD SUPER FESTIVAL 2026", user)
            return
        alias, event_name = parts[0], parts[1].strip()
        hits = match_events(event_name, self.events)
        if len(hits) != 1:
            # 0/多命中：提示更精确，不列候选（S9 计划 §0）
            self._send_text(group,
                            f"「{event_name}」未能唯一确定（命中 {len(hits)} 个），请用更精确的名字再试", user)
            return
        try:
            self.bindings.set(alias, hits[0])
        except ValueError as exc:
            self._send_text(group, f"绑定失败：{exc}", user)
            return
        self._send_text(group, f"已绑定：{alias} → {hits[0].title}（live {alias} 可直接查询）", user)

    def _handle_unbind(self, group: str, user: str, rest: str) -> None:
        alias = (rest or "").strip()
        if not alias:
            self._send_text(group, "用法：unbind <略缩>，如 unbind iwsf", user)
            return
        ok = self.bindings.remove(alias)
        self._send_text(group, f"已删除绑定「{alias}」" if ok else f"未找到绑定「{alias}」", user)

    def _handle_bindings(self, group: str, user: str) -> None:
        items = self.bindings.list()
        if not items:
            self._send_text(group, "暂无绑定。用 binding <略缩> <事件名> 添加，如 binding iwsf IDOL WORLD SUPER FESTIVAL 2026", user)
            return
        title = f"全部绑定（{len(items)} 条）"
        rows = [(alias, ev.title) for alias, ev in items]
        fallback = title + "：\n" + "\n".join(f"{alias} → {ev.title}" for alias, ev in items)
        self._send_list(group, user, title, rows, fallback)

    # ---------------- refresh：手动增量更新歌曲索引（对所有人开放） ----------------
    def _handle_refresh(self, group: str, user: str) -> None:
        """``@bot refresh``：手动增量更新歌曲反向索引（重抓列表页 -> 只抓新增公演）。"""
        if self.song_index is None:
            self._send_text(group, "歌曲索引尚未构建完成，请稍后再试", user)
            return
        self._send_text(group, "正在增量更新歌曲索引，请稍候…", user)
        added = self._refresh_song_index()
        if added is None:
            self._send_text(group, "增量更新失败（列表页抓取失败），沿用现有索引", user)
        elif added:
            self._send_text(group, f"增量更新完成：新增 {added} 个公演", user)
        else:
            self._send_text(group, "增量更新完成：没有新增公演", user)

    # ---------------- update live ----------------
    def _handle_update(self, group: str, user: str, rest: str) -> None:
        if (rest or "").strip().casefold() != "live":
            self._send_text(group, "目前只支持 update live（手动全量刷新事件 + 歌曲索引）", user)
            return
        self._send_text(group, "正在刷新全部 LIVE 索引，请稍候…", user)
        try:
            n_events, n_songs = self.refresh_all()
        except FetchError as exc:
            self._send_text(group, f"刷新失败（{exc}），仍使用旧索引", user)
            return
        reply = f"已刷新：{n_events} 事件"
        if n_songs is not None:
            reply += f" / {n_songs} 歌曲"
        self._send_text(group, reply, user)

    def _resolve_event(self, inc: Incoming, ev: Event) -> None:
        """唯一事件落定：多日 -> 子列表 + 会话记事件；单页 -> 全流程发图。"""
        group, user = inc.group_id, inc.user_id
        if ev.sub_events:
            self.session.set(group, user, {"kind": CTX_EVENT, "event": ev})
            rows = [(s.title, s.date or "") for s in ev.sub_events]
            self._send_list(group, user, ev.title, rows, format_sub_list(ev))
        else:
            self.session.clear(group, user)
            self._full_flow(group, user, ev.url, ev.title)

    # ------------------------------------------------------------------
    # 全流程：抓详情 -> 渲染 -> 发图（失败回退纯文本歌单）
    # ------------------------------------------------------------------
    def _render_out(self) -> Optional[Path]:
        base = self.cfg.render_dir
        if not base:
            return None
        return Path(base) / datetime.now().strftime("%Y%m%d_%H%M%S")

    def _cached_setlist(self, url: str) -> Optional[Setlist]:
        """全量 setlist 缓存查询（S8 构建索引时顺带缓存；未命中返回 None）。

        M9 防御：缓存若缺演者颜色（构建时颜色表缺失等历史原因），
        视为未命中，由调用方重新实时抓取（保证渲染有色）。
        """
        if self.song_index is not None:
            sl = self.song_index.setlists.get(url)
            if sl is not None and not self._setlist_has_colors(sl):
                return None
            return sl
        return None

    @staticmethod
    def _setlist_has_colors(sl: Setlist) -> bool:
        """setlist 是否带有效演者颜色（任一 track 有非 None 的 performer_colors 即视为有效）。"""
        if sl is None:
            return False
        for t in (sl.tracks or []):
            if t.performer_colors and any(c is not None for c in t.performer_colors):
                return True
        return any(c is not None for c in (sl.performer_colors or []))

    def _cache_setlist(self, url: str, sl: Setlist) -> None:
        """把网络抓到的 setlist 顺手写回全量缓存（网络不稳时逐步补齐；失败仅告警）。"""
        if self.song_index is None:
            return
        with self.song_index_lock:
            self.song_index.setlists[url] = sl
            self._song_index_save()

    def _full_flow(self, group_id: str, user_id: str, url: str, label: str) -> None:
        """抓详情 + 渲染 PNG + 发送；任一环节失败给用户明确回复，不抛异常。

        :param user_id: 发起用户 QQ（回复带 @ 归属；图片消息也带 @）
        """
        sl = self._cached_setlist(url)   # 命中全量缓存免网络（2026-08-27）
        if sl is None:
            try:
                sl = fetch_setlist(url, client=self.setlist_client)
            except Exception as exc:  # noqa: BLE001 — 抓取失败给提示
                logger.warning("详情抓取失败: %s: %s", url, exc)
                self._send_text(group_id, f"抓取公演详情失败（{type(exc).__name__}），请稍后再试或换个 LIVE", user_id)
                return
            self._cache_setlist(url, sl)   # 按需补写：网络不稳时逐步补齐全量缓存（2026-08-27）
        if not sl.tracks:
            self._send_text(group_id, f"「{sl.title or label}」没有可显示的曲目", user_id)
            return
        try:
            paths = list(self.renderer(sl, out_dir=self._render_out()))
        except Exception as exc:  # noqa: BLE001 — 渲染失败回退文字
            logger.exception("图片渲染失败（%s），回退纯文本歌单", url)
            self._send_text(group_id, "歌曲列表渲染失败，改发文字版：\n" + setlist_text(sl), user_id)
            return
        if not paths:
            self._send_text(group_id, "歌曲列表渲染结果为空，改发文字版：\n" + setlist_text(sl), user_id)
            return
        # 只发图片（带 @ 归属）：标题/日期场馆/出演/曲目都已渲染进 PNG
        # （2026-08-27 live 反馈：去掉冗余文字消息；2026-08-27 用户要求：回复按用户归属）
        at_text = f"[CQ:at,qq={user_id}]" if user_id else ""
        try:
            ok = bool(self.sender(group_id, at_text, [Path(p) for p in paths]))
        except Exception as exc:  # noqa: BLE001 — 发送异常回退文字
            logger.exception("图片发送异常（回退纯文本歌单）: %s", exc)
            self._send_text(group_id, "图片发送失败，改发文字版：\n" + setlist_text(sl), user_id)
            return
        if not ok:
            # S7 bug 修复（2026-08-27 实测）：NapCat sendMsg 偶发「回执超时」返回 status=failed，
            # 但消息实际已送达——先查群历史确认是否已送达：已送达则降级为 INFO（假阴性），
            # 不刷「发送失败」告警、不重复发文字版兜底（2026-08-27 告警刷屏修复）。
            if self._confirm_group_image(group_id):
                logger.info("图片已送达（NapCat 回执超时假阴性），跳过文字版兜底")
                return
            logger.warning("图片发送失败（群 %s），改发文字版", group_id)
            self._send_text(group_id, "图片发送失败，改发文字版：\n" + setlist_text(sl), user_id)

    # ------------------------------------------------------------------
    # 发送层（默认：复用 M6 push，图片 base64://）
    # ------------------------------------------------------------------
    def _send_text(self, group_id: str, text: str, user_id: Optional[str] = None) -> None:
        """发送纯文本回复；带 ``user_id`` 时回复开头 @ 该用户（按用户归属，群内多人并发不混淆）。

        失败仅告警（不抛，避免影响后续消息）。
        """
        if user_id:
            text = f"[CQ:at,qq={user_id}] {text}"
        try:
            ok = self.sender(str(group_id), text, [])
            if not ok:
                logger.warning("发送文本失败（群 %s）", group_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("发送文本失败（群 %s）: %s", group_id, exc)

    def _send_list(self, group_id: str, user_id: str, title: str,
                   rows: list[tuple[str, str]], text_fallback: str,
                   *, hint: str = "回复序号") -> None:
        """列表类回复发图（S10.3）：``render_list`` → 发图（带 @ 归属）；失败回退纯文本。

        候选/子列表/时间筛选/歌曲出现/bindings 等「序号 + 名称 + 日期」列表统一走此方法：
        图片内 footer 统一「回复序号」（S10 拍板），序号与会话确认序号一致；
        渲染失败 / 图片为空 / 发送失败（含 NapCat 假失败送达确认）→ 回退 ``text_fallback``。

        :param title: 图内标题（如「2026年7月 的 LIVE（共 2 场）」）
        :param rows: [(主文本, 副文本)]，序号自动 1 起
        :param text_fallback: 失败时回退发送的纯文本（``format_*`` 产物，含确认提示）
        :param hint: 图内 footer 提示（默认「回复序号」）
        """
        try:
            paths = list(self.list_renderer(title, rows, out_dir=self._render_out(), hint=hint))
        except Exception as exc:  # noqa: BLE001 — 列表渲染失败回退文本
            logger.exception("列表图片渲染失败（回退纯文本）: %s", exc)
            self._send_text(group_id, text_fallback, user_id)
            return
        if not paths:
            logger.warning("列表图片渲染结果为空（回退纯文本）: %s", title)
            self._send_text(group_id, text_fallback, user_id)
            return
        at_text = f"[CQ:at,qq={user_id}]" if user_id else ""
        try:
            ok = bool(self.sender(group_id, at_text, [Path(p) for p in paths]))
        except Exception as exc:  # noqa: BLE001 — 列表图片发送异常回退文本
            logger.exception("列表图片发送异常（回退纯文本）: %s", exc)
            self._send_text(group_id, text_fallback, user_id)
            return
        if not ok:
            # 同 _full_flow：NapCat 偶发「回执超时」假失败——先确认群内是否已有 bot 图片；
            # 已送达则降级为 INFO（假阴性），不刷「发送失败」告警（2026-08-27 告警刷屏修复）。
            if self._confirm_group_image(group_id):
                logger.info("列表图片已送达（NapCat 回执超时假阴性），跳过文本兜底")
                return
            logger.warning("列表图片发送失败（群 %s），改发文本", group_id)
            self._send_text(group_id, text_fallback, user_id)

    def _confirm_group_image(self, group_id: str, within_seconds: float = 20.0) -> bool:
        """确认该群最近是否已有 **bot 本人** 发出的图片（NapCat 假失败的送达确认，S7 bug 修复）。

        图片发送返回失败（如 NapCat ``sendMsg`` 回执超时返回 ``status=failed``，但消息实际
        已送达）时调用：查 ``get_group_msg_history`` 最近 N 条，近 ``within_seconds`` 秒内若
        已有 bot 自己发的图片消息，视为「实际已送达」——调用方跳过文字版兜底，
        避免「先失败文字版 + 后图片」的重复/误报。

        :return: True = 已确认送达；False = 未确认（查询失败按未送达处理，保守兜底）
        """
        try:
            if self._notifier is None:
                self._notifier = replace(load_notifier_config(), merge_forward=False)
            base = self._notifier.base_url
            with httpx.Client(timeout=8) as client:
                self_id = str(_get_self_info(client, self._notifier)[0])
                resp = client.post(
                    f"{base}/get_group_msg_history",
                    json={"group_id": str(group_id), "message_seq": 0, "count": 6},
                )
                data = resp.json()
            now = time.time()
            msgs = (data.get("data") or {}).get("messages") or []
            for m in msgs:
                if str(m.get("user_id")) != self_id:
                    continue
                if now - float(m.get("time") or 0) > within_seconds:
                    continue
                segs = m.get("message") or []
                if any(isinstance(s, dict) and s.get("type") == "image" for s in segs):
                    return True
            return False
        except Exception as exc:  # noqa: BLE001 — 确认查询失败按未送达处理（保守）
            logger.info("图片送达确认查询失败（按未送达兜底，可能是断网）: %s", exc)
            return False

    def _default_sender(self, group_id: str, text: str, image_paths: list[Path]) -> bool:
        """默认发送：OneBot send_group_msg（文本一条 + 图片合并一条多图消息），返回是否成功。

        复用 ``ref/m6_notifier.push``：文本段逐条发、图片合并一条（``base64://`` 前缀），
        重试/容错由 M6 承担；``merge_forward=False``（回复不包合并聊天记录）。
        回复归属：text 开头的 ``[CQ:at,qq=N]`` 在此拆出并转成**独立 at 段**
        （NapCat array 消息形态下 CQ 码嵌 text 会字面显示，2026-08-27 修正），
        随文本/图片同一条发出。
        """
        if self._notifier is None:
            self._notifier = replace(load_notifier_config(), merge_forward=False)
        ats: list[str] = []
        if text:
            qq, text = _extract_at_qq(text)
            if qq:
                ats = [qq]
        images: list[str] = []
        for p in image_paths or []:
            data = Path(p).read_bytes()
            images.append("base64://" + base64.b64encode(data).decode("ascii"))
        msg = PushMessage(
            group_ids=[str(group_id)],
            segments=[text] if text else [],
            images=images,
            link="",
            ats=ats,
        )
        results = push(msg, config=self._notifier)
        # 不再在此告警：NapCat sendMsg 偶发「回执超时」假失败（消息实际已送达），
        # 是否真失败由调用方 _full_flow/_send_list 查群历史后判定（2026-08-27 修复告警刷屏）。
        return any(r.ok for r in results)


# ---------------------------------------------------------------------------
# 日志（同主项目 M7 习惯）
# ---------------------------------------------------------------------------
def _setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:  # 幂等：避免重复添加（测试场景）
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    else:
        root.handlers[0].setFormatter(fmt)


def _dry_run_sender(group_id: str, text: str, image_paths: list[Path]) -> bool:
    """dry-run 发送：只打印（不碰 NapCat）。"""
    preview = text.replace("\n", " ⏎ ")[:160] if text else ""
    print(f"[DRY-RUN] -> 群 {group_id}: {preview} | 图片 {len(image_paths)} 张"
          + (f"（{', '.join(str(p) for p in image_paths)}）" if image_paths else ""))
    return True


# ---------------------------------------------------------------------------
# 启动/停止状态通知（S7 需求 2：bot 启动与结束时在配置群（主群+测试群）发状态消息）
# ---------------------------------------------------------------------------
def _startup_text(cfg: BotConfig, *, port: int, event_count: int, latest_year: int,
                  song_index_ready: bool, dry_run: bool) -> str:
    """启动通知文案：按 ``cfg.notify_startup`` 模板渲染（config 可自定义，仿 M7）。

    占位符：{port}/{events}/{year}/{song_index}/{ttl}/{mode}/{time}；未知占位符原样保留。
    """
    return _render_template(
        cfg.notify_startup,
        port=port,
        events=event_count,
        year=latest_year,
        song_index="已就绪" if song_index_ready else "后台构建中",
        ttl=f"{cfg.ttl_sec:.0f}",
        mode="DRY-RUN（不真实推送）" if dry_run else "正式",
        time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def _shutdown_text(cfg: BotConfig, started_at: str, stopped_at: str) -> str:
    """停止通知文案：按 ``cfg.notify_shutdown`` 模板渲染（config 可自定义，仿 M7）。

    占位符：{started}/{stopped}/{time}；未知占位符原样保留。
    """
    return _render_template(
        cfg.notify_shutdown,
        started=started_at,
        stopped=stopped_at,
        time=stopped_at,
    )


def _notify_groups(sender: Callable, groups: list[str], text: str) -> int:
    """向每个群发送状态通知；返回成功数。失败仅告警不抛（不阻塞启动/停止流程）。

    dry-run 时 sender 为 ``_dry_run_sender``，只打印不真发。
    """
    ok = 0
    for g in groups:
        try:
            if sender(str(g), text, []):
                ok += 1
        except Exception:  # noqa: BLE001 — 单群通知失败不阻断其余群与主流程
            logger.exception("状态通知发送失败（群 %s）", g)
    return ok


def _wait_for_stop(stop_file: str, interval: float = 5.0) -> None:
    """主循环等待：监听停止文件，出现即返回（优雅停止，S7）。

    - ``stop_file`` 非空：每 ``interval`` 秒检查一次，文件出现返回（不删除，由
      ``_remove_stop_file`` 在退出时清理）；
    - ``stop_file`` 为空串：不监听，常驻等待（等价旧版 ``sleep`` 语义，Ctrl+C 退出）。
    """
    if not stop_file:
        while True:
            time.sleep(interval)
    while not os.path.exists(stop_file):
        time.sleep(interval)


def _remove_stop_file(stop_file: str) -> None:
    """优雅退出后清理停止文件（避免下次启动立即再次触发停止）。"""
    if not stop_file:
        return
    try:
        if os.path.exists(stop_file):
            os.remove(stop_file)
    except OSError:  # noqa: BLE001
        logger.warning("停止文件清理失败: %s", stop_file)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="songbot.bot",
        description="歌曲列表 bot 主控（S6）：@bot 查询 LIVE → 两段交互 → 歌曲列表图片",
    )
    parser.add_argument("--config", default=None, help="配置文件路径（缺省项目根 config.yaml）")
    parser.add_argument("--port", type=int, default=None,
                        help="接收器监听端口（缺省读 config.yaml songbot.port，再缺省 8090）")
    parser.add_argument("--index-file", default=None,
                        help="事件索引缓存文件路径（覆盖 config；空串禁用缓存）")
    parser.add_argument("--no-cache", action="store_true", help="禁用索引缓存（强制重新抓取）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将发送的内容，不真实推送到 QQ（排障/预演用）")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台 GBK 无法编码日文，统一 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")

    args = _build_parser().parse_args(argv)
    _setup_logging()

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cfg = load_bot_config(args.config)
    if args.port is not None:
        cfg.port = args.port
    if args.index_file is not None:
        cfg.index_cache = args.index_file
    if args.no_cache:
        cfg.index_cache = ""

    bot = SongBot(config=cfg)
    if args.dry_run:
        bot.sender = _dry_run_sender

    try:
        bot.build_index()
    except FetchError as exc:
        logger.error("事件索引构建失败，退出: %s", exc)
        print(f"[ERROR] 事件索引构建失败: {exc}")
        return 1

    bot.start_song_index()                        # S8：加载缓存或后台全量构建歌曲反向索引
    bot.song_refresher = bot._song_refresher      # S9 update live 钩子：重建歌曲索引

    # M9 2GB 内存优化：不预热浏览器（warmup 常驻 ~140MB Edge）；
    # worker 惰性启动——首次查询时冷启动（约 15s），之后浏览器常驻复用（2-3s）。

    receiver = EventReceiver(bot.handle, port=cfg.port)
    receiver.start()
    logger.info(
        "songbot 已启动：监听 %s:%d%s | 事件 %d 个（最新 %d 年）| 会话 TTL %.0fs | %s%s",
        "127.0.0.1", receiver.bound_port, "/event", len(bot.events), bot.latest_year,
        cfg.ttl_sec, "DRY-RUN（不真实推送）" if args.dry_run else "正式",
        f" | 歌曲索引 {'已加载缓存' if bot.song_index is not None else '后台构建中'}",
    )
    print(f"songbot 已启动：http://127.0.0.1:{receiver.bound_port}/event（Ctrl+C 停止）")
    # S7 需求 2：启动成功后在配置群（主群+测试群）发状态通知；dry-run 只打印
    _notify_groups(bot.sender, cfg.notify_groups,
                   _startup_text(cfg, port=receiver.bound_port, event_count=len(bot.events),
                                 latest_year=bot.latest_year,
                                 song_index_ready=bot.song_index is not None,
                                 dry_run=args.dry_run))
    try:
        _wait_for_stop(cfg.stop_file, interval=5.0)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，songbot 停止")
    finally:
        receiver.stop()
        _remove_stop_file(cfg.stop_file)
        # S7 需求 2：优雅退出（Ctrl+C / 停止文件）后发停止通知；强制 kill 无法执行
        _notify_groups(bot.sender, cfg.notify_groups,
                       _shutdown_text(cfg, started_at, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    logger.info("songbot 已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
