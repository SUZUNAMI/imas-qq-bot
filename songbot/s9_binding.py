"""S9 绑定别名存储 + 手动刷新支撑 — 歌曲列表 bot（songbot）.

计划正文：docs/S9-bindings-update-plan.md；施工图：docs/S1-S7-taskplan.md §S3/S6。

职责（S9.1）
------------
- ``BindingStore``：绑定别名（略缩）→ 序列化 ``Event`` 的线程安全存储，
  JSON 持久化（``data/songbot_bindings.json`` 默认）；``set`` / ``get`` /
  ``remove`` / ``list`` / ``resolve``；
- 别名 key = ``s3_match.normalize(略缩)``（精确 normalize 匹配）；
- ``event_to_dict`` / ``event_from_dict``：``Event`` 序列化（格式与
  bot.py 事件索引缓存一致，bot.py 复用本模块实现，避免重复定义）。

契约（S9 计划 §3）
------------------
- 绑定值 = 序列化 ``Event``（``{normalize(略缩): {"alias": 原样略缩, "event": event_dict}}``）；
- 启动加载、变更即存；持久化失败记日志不崩、内存内仍生效（风险表约定）；
- 并发安全：读写均加锁（RLock）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Optional

from songbot.models_song import Event, SubEvent
from songbot.s3_match import normalize

logger = logging.getLogger("songbot.binding")

DEFAULT_BINDINGS_FILE = "data/songbot_bindings.json"   # 相对项目根（与 index_cache 同约定）


# ---------------------------------------------------------------------------
# Event 序列化（格式与 bot.py 事件索引缓存一致；bot.py 复用本模块实现）
# ---------------------------------------------------------------------------
def sub_to_dict(s: SubEvent) -> dict:
    return {"title": s.title, "full_title": s.full_title, "url": s.url, "date": s.date}


def event_to_dict(e: Event) -> dict:
    return {
        "title": e.title, "year": e.year, "date": e.date,
        "brands": list(e.brands), "url": e.url,
        "sub_events": [sub_to_dict(x) for x in e.sub_events],
    }


def event_from_dict(d: dict) -> Event:
    subs = []
    for x in d.get("sub_events") or []:
        if isinstance(x, dict):
            subs.append(SubEvent(
                title=str(x.get("title") or ""), full_title=str(x.get("full_title") or ""),
                url=str(x.get("url") or ""), date=str(x.get("date") or ""),
            ))
    return Event(
        title=str(d.get("title") or ""), year=str(d.get("year") or ""),
        date=str(d.get("date") or ""),
        brands=[str(b) for b in (d.get("brands") or [])],
        url=str(d.get("url") or ""), sub_events=subs,
    )


# ---------------------------------------------------------------------------
# BindingStore
# ---------------------------------------------------------------------------
class BindingStore:
    """绑定别名存储：略缩（normalize 后）-> 序列化 Event，线程安全 + JSON 持久化。

    JSON 结构（形如计划 §3「{略缩(normalize): event_dict}」，另存原样略缩供展示）::

        {"iwsf": {"alias": "IWSF", "event": {title/year/date/brands/url/sub_events}}}

    - 启动加载（缺失/损坏回退空 + 记日志）；每次变更立即落盘（失败记日志不崩）；
    - key 一律 ``normalize(略缩)``，``resolve(query)`` 用 ``normalize(query)`` 精确匹配。
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or DEFAULT_BINDINGS_FILE
        self._data: dict[str, dict] = {}          # normalize(alias) -> {"alias": str, "event": dict}
        self._lock = threading.RLock()
        self._load()

    # ---------------- 加载 / 落盘 ----------------
    def _load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._data = {str(k): v for k, v in data.items() if isinstance(v, dict)}
                logger.info("绑定加载：%s（%d 条）", self.path, len(self._data))
        except FileNotFoundError:
            self._data = {}
        except (OSError, ValueError) as exc:      # 损坏 JSON / 读失败 -> 空表 + 告警
            logger.warning("绑定文件读取失败（回退空表）: %s: %s", self.path, exc)
            self._data = {}

    def _save(self) -> None:
        if not self.path:
            return
        try:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=1)
        except OSError as exc:
            logger.warning("绑定持久化失败（内存内仍生效）: %s: %s", self.path, exc)

    # ---------------- 增删查 ----------------
    def set(self, alias: str, event: Event) -> None:
        """设置/覆盖绑定：``normalize(alias)`` -> 序列化 event；立即落盘。"""
        key = normalize(alias or "")
        if not key or not isinstance(event, Event):
            raise ValueError("绑定略缩不能为空，且须绑定一个 Event")
        with self._lock:
            self._data[key] = {"alias": str(alias).strip(), "event": event_to_dict(event)}
            self._save()

    def get(self, alias: str) -> Optional[Event]:
        """按 normalize(alias) 精确取绑定事件；无则 None。"""
        key = normalize(alias or "")
        with self._lock:
            entry = self._data.get(key)
        return event_from_dict(entry["event"]) if entry else None

    def resolve(self, query: str) -> Optional[Event]:
        """``resolve_binding``：精确 normalize 匹配（``live <略缩>`` 分支先查绑定用）。"""
        return self.get(query)

    def remove(self, alias: str) -> bool:
        """删除绑定；返回是否原本存在。"""
        key = normalize(alias or "")
        with self._lock:
            if key not in self._data:
                return False
            del self._data[key]
            self._save()
            return True

    def list(self) -> list[tuple[str, Event]]:
        """全部绑定 ``(原样略缩, Event)``，按 key 排序（稳定展示）。"""
        with self._lock:
            items = [(entry.get("alias") or key, event_from_dict(entry["event"]))
                     for key, entry in self._data.items()]
        return sorted(items, key=lambda x: x[0].casefold())

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
