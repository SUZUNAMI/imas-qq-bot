"""S8 歌曲反向索引 — 歌曲列表 bot（songbot）.

从**全部公演详情页** ``table.tracklist`` 构建「歌名 -> 出现过的 LIVE」反向索引，
供 ``@bot song <歌名>`` 反查使用（计划：docs/S8-song-lookup-plan.md；
实施施工图：docs/modules/S8-song-lookup-plan.md）。

数据流
------
事件列表（S1 ``Event[]``）-> 全部详情 URL（``_appearance_specs``）->
``fetch_setlist``（S2）-> ``Track.title`` -> ``SongIndex``（``normalize(title)`` -> ``SongEntry``）。

模块组成
--------
- ``SongIndex``：``entries``（键 = ``s3_match.normalize(歌名)``）+ ``source_urls``
  （已抓过的详情页 URL 集合，增量刷新去重/停止边界）+ ``fetched_at``；
- ``build_song_index(events, fetch_setlist, ...)``：全量构建（抓全部详情 URL）；
- ``refresh_song_index(index, events, fetch_setlist, ...)``：增量刷新——
  **按事件列表顺序扫描详情 URL，遇到第一个已收录（``source_urls``）即停止**
  （2026-08-27 用户拍板：列表页年份降序、新 LIVE 永远在顶部，首个已收录即边界，
  其后必已收录），仅抓取停止点之前的新增 setlist 并入索引；
- ``save_song_index`` / ``load_song_index``：落盘 JSON 缓存
  （``data/songbot_song_index.json`` 默认，config 可改）；
- ``match_songs(query, index, ...)``：复用 ``s3_match.normalize`` + ``_score_text`` 打分，
  精确键命中优先，否则阈值 60 / top 5 候选；``index`` 可为 ``SongIndex`` 或
  ``list[SongEntry]``（候选内再匹配用）。

去重语义
--------
- 同一首歌在**同一场 LIVE（同详情 URL）的多次演唱只记一次** Appearance；
- 不同歌曲 ``normalize`` 后同键 -> 合并为一个 ``SongEntry``（展示名取首见）。

约定（docs/S1-S7-taskplan.md §0.4）
------------------------------------
- 单测全离线：解析类用 ``fixtures/*.html``，抓取注入 mock ``fetch_setlist``；
- 抓取/解析复用 S1/S2（``fetch_setlist`` 由调用方注入，默认 ``s2_fetch_setlist.fetch_setlist``）。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional, Union

from songbot.models_song import Appearance, Event, Setlist, SongEntry, Track
from songbot.s3_match import DEFAULT_TOP_N, SCORE_THRESHOLD, _score_text, normalize

logger = logging.getLogger("songbot.s8_song_index")

DEFAULT_SONG_INDEX_FILE = "data/songbot_song_index.json"   # 相对项目根（与 index_cache 同约定）


# ---------------------------------------------------------------------------
# 索引容器
# ---------------------------------------------------------------------------
class SongIndex:
    """歌曲反向索引：``entries``（normalize(歌名) -> SongEntry）+ ``source_urls``。"""

    def __init__(self) -> None:
        self.entries: dict[str, SongEntry] = {}   # 键 = normalize(歌名)
        self.source_urls: set[str] = set()        # 已抓过的详情页 URL（增量刷新边界/去重）
        self.setlists: dict[str, Setlist] = {}    # 详情 URL -> 完整 Setlist（全量缓存，song/live 出图免网络）
        self.fetched_at: float = 0.0              # 最近一次构建/刷新时间戳

    def __len__(self) -> int:
        return len(self.entries)


# ---------------------------------------------------------------------------
# 详情页清单（事件 -> 全部详情 URL + 事件元信息，保持列表页顺序）
# ---------------------------------------------------------------------------
def _appearance_specs(events: list[Event]) -> list[dict]:
    """事件列表 -> 详情页清单 ``[{url, event_title, event_year, sub_title, date}]``。

    - 单页事件：``url=Event.url``、``sub_title=""``、``date=Event.date``；
    - 多日事件：每个 SubEvent 一条（``url=SubEvent.url``、``sub_title=SubEvent.title``、
      ``date=SubEvent.date``）；
    - 顺序与列表页一致（年份降序，新 LIVE 在顶部——增量刷新「首个已收录即停止」依赖此序）。
    """
    specs: list[dict] = []
    for ev in events:
        if ev.sub_events:
            for s in ev.sub_events:
                if s.url:
                    specs.append({
                        "url": s.url, "event_title": ev.title, "event_year": ev.year,
                        "sub_title": s.title, "date": s.date,
                    })
        elif ev.url:
            specs.append({
                "url": ev.url, "event_title": ev.title, "event_year": ev.year,
                "sub_title": "", "date": ev.date,
            })
    return specs


def _merge_setlist(index: SongIndex, spec: dict, fetch_setlist: Callable) -> None:
    """抓取一个详情页并并入索引；单页失败记日志跳过，不中断整体构建/刷新。"""
    url = spec["url"]
    try:
        sl = fetch_setlist(url)
    except Exception as exc:  # noqa: BLE001 — 站点坏条目/网络抖动：跳过该页，其余继续
        logger.warning("歌曲索引跳过 %s: %s", url, exc)
        return
    index.setlists[url] = sl   # 全量缓存完整 setlist（2026-08-27：song/live 出图免网络）
    for tr in sl.tracks:
        if not tr.title:
            continue
        key = normalize(tr.title)
        entry = index.entries.get(key)
        if entry is None:
            entry = SongEntry(title=tr.title, appearances=[])
            index.entries[key] = entry
        if any(a.url == url for a in entry.appearances):
            continue                       # 同一场 LIVE 多次演唱只记一次
        entry.appearances.append(Appearance(
            event_title=spec["event_title"], event_year=spec["event_year"],
            sub_title=spec["sub_title"], date=spec["date"], url=url,
        ))
    index.source_urls.add(url)


# ---------------------------------------------------------------------------
# 构建 / 增量刷新
# ---------------------------------------------------------------------------
def build_song_index(
    events: list[Event],
    fetch_setlist: Callable,
    *,
    progress: Optional[Callable] = None,
) -> SongIndex:
    """全量构建歌曲反向索引：抓取事件列表提供的**全部**详情页 URL。

    :param events: 事件列表（S1 解析结果，提供全部详情 URL 与事件元信息）
    :param fetch_setlist: ``callable(url) -> Setlist``（S2 的 ``fetch_setlist`` 或 mock）
    :param progress: 可选 ``callable(done, total)`` 进度回调
    :return: 新 SongIndex（entries 覆盖全部成功抓取的歌曲）
    """
    index = SongIndex()
    specs = _appearance_specs(events)
    total = len(specs)
    for i, spec in enumerate(specs, 1):
        _merge_setlist(index, spec, fetch_setlist)
        if progress is not None:
            progress(i, total)
    index.fetched_at = time.time()
    return index


def refresh_song_index(
    index: SongIndex,
    events: list[Event],
    fetch_setlist: Callable,
    *,
    progress: Optional[Callable] = None,
) -> SongIndex:
    """增量刷新：**按列表顺序扫描详情 URL，遇到第一个已收录（``source_urls``）即停止**。

    列表页年份降序、新 LIVE 永远在顶部（2026-08-27 用户拍板），故首个已收录即边界，
    其后必已收录；停止点之前的 URL 均为新增，仅抓取这些 setlist 并入索引。

    :param index: 现有 SongIndex（**原地更新**）
    :param events: 最新事件列表（``fetch_events`` 重抓结果，顺序 = 列表页顺序）
    :param fetch_setlist: ``callable(url) -> Setlist``
    :param progress: 可选 ``callable(done, total)`` 进度回调（done = 本次新增抓取数）
    :return: 同一 ``index``
    """
    specs = _appearance_specs(events)
    total = len(specs)
    fetched = 0
    for i, spec in enumerate(specs, 1):
        url = spec["url"]
        if url in index.source_urls:
            logger.info("歌曲索引增量刷新在 %s 停止（第 %d/%d 个，其后均已收录）", url, i, total)
            break
        _merge_setlist(index, spec, fetch_setlist)
        fetched += 1
        if progress is not None:
            progress(fetched, total)
    index.fetched_at = time.time()
    return index


# ---------------------------------------------------------------------------
# 落盘缓存（JSON）
# ---------------------------------------------------------------------------
def _setlist_to_dict(sl: Setlist) -> dict:
    """Setlist -> JSON dict（全量缓存落盘用）。"""
    return {
        "title": sl.title,
        "date_venue": sl.date_venue,
        "performers": list(sl.performers),
        "performer_colors": list(sl.performer_colors),
        "tracks": [
            {
                "no": t.no,
                "title": t.title,
                "brand": t.brand,
                "performers": list(t.performers),
                "performer_colors": list(t.performer_colors),
                "link": t.link,
            }
            for t in sl.tracks
        ],
        "url": sl.url,
    }


def _setlist_from_dict(d) -> Optional[Setlist]:
    """JSON dict -> Setlist；非法输入返回 None。"""
    if not isinstance(d, dict):
        return None
    tracks: list[Track] = []
    for t in d.get("tracks") or []:
        if not isinstance(t, dict):
            continue
        tracks.append(Track(
            no=int(t.get("no") or 0),
            title=str(t.get("title") or ""),
            brand=t.get("brand"),
            performers=[str(x) for x in (t.get("performers") or [])],
            performer_colors=[x for x in (t.get("performer_colors") or [])],
            link=t.get("link"),
        ))
    return Setlist(
        title=str(d.get("title") or ""),
        date_venue=str(d.get("date_venue") or ""),
        performers=[str(x) for x in (d.get("performers") or [])],
        performer_colors=[x for x in (d.get("performer_colors") or [])],
        tracks=tracks,
        url=str(d.get("url") or ""),
    )


def _song_index_to_dict(index: SongIndex) -> dict:
    return {
        "fetched_at": index.fetched_at,
        "source_urls": sorted(index.source_urls),
        "songs": [
            {
                "title": e.title,
                "appearances": [
                    {"event_title": a.event_title, "event_year": a.event_year,
                     "sub_title": a.sub_title, "date": a.date, "url": a.url}
                    for a in e.appearances
                ],
            }
            for e in index.entries.values()
        ],
        "setlists": {url: _setlist_to_dict(sl) for url, sl in index.setlists.items()},
    }


def _song_index_from_dict(data) -> Optional[SongIndex]:
    if not isinstance(data, dict):
        return None
    index = SongIndex()
    try:
        index.fetched_at = float(data.get("fetched_at") or 0)
    except (TypeError, ValueError):
        index.fetched_at = 0.0
    index.source_urls = {str(u) for u in (data.get("source_urls") or [])}
    for s in data.get("songs") or []:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or "")
        if not title:
            continue
        entry = SongEntry(title=title, appearances=[])
        for a in s.get("appearances") or []:
            if not isinstance(a, dict):
                continue
            entry.appearances.append(Appearance(
                event_title=str(a.get("event_title") or ""),
                event_year=str(a.get("event_year") or ""),
                sub_title=str(a.get("sub_title") or ""),
                date=str(a.get("date") or ""),
                url=str(a.get("url") or ""),
            ))
        index.entries[normalize(title)] = entry
    index.setlists = {}
    for u, d in (data.get("setlists") or {}).items():
        sl = _setlist_from_dict(d)
        if sl is not None:
            index.setlists[str(u)] = sl
    return index


def save_song_index(index: SongIndex, path: str) -> None:
    """落盘 JSON 缓存（``ensure_ascii=False``；父目录自动创建；失败抛 OSError 由调用方处理）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_song_index_to_dict(index), ensure_ascii=False, indent=1),
                 encoding="utf-8")


def load_song_index(path: str) -> Optional[SongIndex]:
    """读 JSON 缓存 -> SongIndex；文件缺失/损坏返回 None（调用方决定重建）。"""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return _song_index_from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("歌曲索引缓存读取失败（将重建）: %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# 歌名匹配（复用 s3_match.normalize + _score_text 打分策略）
# ---------------------------------------------------------------------------
def _as_entries(index: Union[SongIndex, list[SongEntry]]) -> list[SongEntry]:
    """把 SongIndex 或 SongEntry 列表统一成条目列表（候选内再匹配传 list）。"""
    if isinstance(index, SongIndex):
        return list(index.entries.values())
    return list(index)


def match_songs(query: str, index: Union[SongIndex, list[SongEntry]],
                top_n: int = DEFAULT_TOP_N) -> list[SongEntry]:
    """歌名匹配：按得分降序返回 ``SongEntry`` 列表（与 ``match_events`` 同语义）。

    - 对每条 ``_score_text`` 打分（完全相等 100 / 子串包含 80 / 词元缩写覆盖 60 /
      difflib 兜底），低于阈值 60 不算候选；
    - 唯一命中 -> ``[该条]``；多个 -> top N（默认 5）作候选（同分保持原顺序，稳定排序）——
      **不静默猜**：精确命中只保证排最前，其他 >= 阈值的候选仍列出让用户选；
    - 无 / 空 query -> ``[]``。
    """
    q = normalize(query)
    if not q:
        return []
    entries = _as_entries(index)
    scored: list[tuple[int, SongEntry]] = []
    for e in entries:
        key = normalize(e.title)
        if not key:
            continue
        if len(key) <= 2 and key != q and key in q:
            # 噪声防护：过短歌名（如 765PRO 的 "i"）作为 query 的子串出现时，
            # 「cand in query」的包含分（80）会把不相关歌曲拉进候选；
            # 精确命中（key == q）仍保留（用户确实在查这首歌）。
            continue
        score = _score_text(q, e.title, key)
        if score >= SCORE_THRESHOLD:
            scored.append((score, e))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    if len(scored) == 1:
        return [scored[0][1]]
    return [e for _, e in scored[:top_n]]
