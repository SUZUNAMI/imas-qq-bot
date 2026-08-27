"""歌曲列表 bot（songbot）— 数据契约 dataclass（单一事实源）.

契约正文：docs/S-songbot-plan.md §5.1 与 docs/S1-S7-taskplan.md 各阶段「契约」小节；
S8 扩展见 docs/S8-song-lookup-plan.md §4。
所有 songbot 模块统一 ``from songbot.models_song import ...``，
禁止重复定义同名 dataclass；契约改动必须回改上述计划文档并同步 docs/index.md。

S1 冻结：SubEvent / Event；S2 冻结：Track / Setlist（提前写入，供 S1/S2/S5 并行）；
S8 冻结：Appearance / SongEntry（歌曲反查 Live 反向索引）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SubEvent:
    """子公演（day1/day2…），多日事件的嵌套子项。"""

    title: str                # 显示名，如 "DAY1 全力援走" / "第一公演 -YAKUDOU-"
    full_title: str           # <a title> 完整标题（如 "… H.I.F選抜試験(セレクション) DAY1"）
    url: str                  # 详情页绝对 URL（http://）
    date: str                 # 日期文本，如 "2026/05/05(火祝)"


@dataclass
class Event:
    """顶层事件（列表页 /song/event 中的一个 <li data-brand-ids>）。"""

    title: str                                  # 事件名（去除徽章/日期后的纯文本）
    year: str                                   # "2026"（去 "年"）
    date: str = ""                              # 单页事件日期文本（去 "- " 前缀，如 "2026/07/04(土)・05(日)"）；多日事件为 ""（用子事件日期，S3 时间筛选用）
    brands: list[str] = field(default_factory=list)      # 品牌徽章名列表（badge 的 title，缺则取文本；可空）
    url: str = ""                               # 单页事件详情 URL；多日事件为 ""
    sub_events: list[SubEvent] = field(default_factory=list)  # 多日事件的子列表；单页为空 []


@dataclass
class Track:
    """歌曲行（公演详情页 table.tracklist 的一行，S2 使用）。"""

    no: int                   # 序号
    title: str                # 歌名（不含徽章）
    brand: Optional[str] = None     # 品牌徽章（无则 None）
    performers: list[str] = field(default_factory=list)   # 演者名列表
    performer_colors: list[Optional[str]] = field(default_factory=list)
    # 演者应援色（与 performers 平行；来自原网页 idol-name 的 data-brand-id/attr/group-id，
    # 优先级 group > attr > brand，hex 或 CSS 颜色名；无则 None）。S2 填写，S4 渲染用。
    link: Optional[str] = None      # /song/detail/N.html 绝对 URL（无则 None）


@dataclass
class Setlist:
    """公演详情（S2 使用）。"""

    title: str                # h1#page_title 文本
    date_venue: str           # 日期/场馆行（去 "詳細" 链接）
    performers: list[str] = field(default_factory=list)   # 出演者（idol-name 文本）
    performer_colors: list[Optional[str]] = field(default_factory=list)
    # 出演者应援色（与 performers 平行；语义同 Track.performer_colors）。
    tracks: list[Track] = field(default_factory=list)
    url: str = ""


@dataclass
class Appearance:
    """歌曲在某一公演的一次出演（S8 歌曲反查反向索引）.

    由 S8 构建索引时从「全部公演详情页 table.tracklist」生成；
    同一首歌在同一场 LIVE（同详情 URL）的多次演唱只记一次。
    """

    event_title: str            # 事件名
    event_year: str             # "2026"
    sub_title: str              # 子公演标题；单页事件为 ""
    date: str                   # 日期文本（如 "2026/07/24(金)"）
    url: str                    # 该公演详情页 URL（渲染用）


@dataclass
class SongEntry:
    """歌曲反向索引一条（一首歌，S8 使用）。

    键为 ``normalize(title)``（s3_match.normalize）；不同歌曲归一化后同键会合并
    （展示名取首见），appearances 按详情 URL 去重。
    """

    title: str                  # 歌名（原文本，展示用）
    appearances: list[Appearance] = field(default_factory=list)   # 出现过的 LIVE 列表
