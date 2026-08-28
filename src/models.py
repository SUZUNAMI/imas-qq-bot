"""共享数据契约（冻结，module-specs.md §1）— 爱马仕官方新闻 QQ 转发机器人.

所有模块的数据类型**统一从这里 import**，禁止在各模块内重复定义同名 dataclass，
避免接口漂移（模块间 ``==`` / ``isinstance`` 失效、字段增删不同步）。

契约来源：docs/module-specs.md §1（冻结）。如需改动，必须回改该文档并同步 docs/index.md。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NewsItem:
    """列表条目（module-specs §1.1，M1 输出 / M3 输入）。字段名冻结勿改。"""

    id: str                 # 唯一键，URL 末段，如 "01_17821" —— 去重依据
    url: str                # 详情页完整 URL
    title: str              # 新闻标题（原文，日文）
    date: str               # "YYYY-MM-DD"
    thumbnail: Optional[str] = None  # 缩略图 URL，没有则 None


@dataclass
class NewsDetail:
    """详情（§1.2，M2 输出 / M4 输入）。"""

    id: str                 # 与 NewsItem.id 一致
    url: str
    title: str              # 标题（原文日文）
    date: str               # "YYYY-MM-DD"
    body_text: str          # 正文纯文本，段落用 "\n\n" 分隔
    images: list[str]       # 正文配图 URL 数组，可为空 []


@dataclass
class TranslationResult:
    """翻译结果（§1.3，M4 输出 / M5 输入）。"""

    title_zh: str           # 标题译文
    body_zh: str            # 正文译文（段落结构尽量与原文对齐，"\n\n" 分隔）


@dataclass
class PushMessage:
    """组装后的推送消息（§1.4，M5 输出 / M6 输入）。"""

    group_ids: list[str]    # 目标群号列表（字符串，群号可能超 32 位整数范围）
    segments: list[str]     # 已分好片的文本段，M6 按顺序逐条发送
    images: list[str]       # 配图 URL 数组，可为空 []
    link: str               # 原文链接
    ats: list[str] = field(default_factory=list)
    # 回复归属（2026-08-27 songbot 追加，契约同步自 ref/models.py）：需 @ 的 QQ 列表，
    # M6 拼为独立 at 段附在首段文本/图片前（空 = 不 @）。默认空列表，向后兼容 M5/M6/M7 既有用法。


@dataclass
class PushResult:
    """推送结果（§1.5，M6 输出 / M8 记录；M3 也产生同结构用于记录）。"""

    group_id: str
    ok: bool
    message_id: str         # 成功时填返回的消息 id，失败填 ""
    error: Optional[str] = None
