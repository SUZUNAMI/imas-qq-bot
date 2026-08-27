"""S8 探针 — 歌曲反查 Live（songbot.s8_song_index）.

构建/加载歌曲反向索引，并打印某歌出现过的所有 LIVE（序号 + 日期）。

用法：
    # 离线验收：fixture 列表 + fixture 详情页（映射 URL 末尾文件名），构建迷你索引后查询
    python scripts/probe_song_index.py --local fixtures/imas_db_song_event.html --song "Dance in the Light"

    # 在线：真实抓取全部详情页建索引（首次约 331 请求，需数分钟）后查询
    python scripts/probe_song_index.py --song "Marionetteは眠らない"

    # 用落盘缓存（先 --save 或 bot 运行产生 data/songbot_song_index.json）
    python scripts/probe_song_index.py --cache data/songbot_song_index.json --song "Dance in the Light"

    # 只构建/保存索引（不查询）：--save <path>
    python scripts/probe_song_index.py --save data/songbot_song_index.json
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Windows 控制台默认 GBK，日文/「・」等字符可能超出可编码范围；强制 stdout/stderr 走 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from songbot.s1_fetch_events import EVENT_LIST_URL, FetchError, fetch_events, parse_events_html  # noqa: E402
from songbot.s1_fetch_events import PAGE_BASE_URL  # noqa: E402
from songbot.s2_fetch_setlist import fetch_setlist, parse_setlist_html  # noqa: E402
from songbot.s8_song_index import (  # noqa: E402
    build_song_index,
    load_song_index,
    match_songs,
    refresh_song_index,
    save_song_index,
)

# 离线映射：详情 URL 末尾文件名 -> fixture 详情页（与 tests/test_s8_song_index.py 同约定）
FIXTURE_PAGES = {
    "iwsf_day1.html": "imas_db_iwsf_day1.html",
    "million_13th_day1.html": "imas_db_million_13th_day1.html",
    "cg_musical_dd.html": "imas_db_cg_musical_dd.html",
}


def _local_fetch(url: str):
    """离线详情抓取：URL 末尾文件名 -> fixture 页解析；无映射抛 FetchError。"""
    for key, name in FIXTURE_PAGES.items():
        if url.rstrip("/").endswith(key):
            html = (Path(_ROOT) / "fixtures" / name).read_text(encoding="utf-8")
            return parse_setlist_html(html, url=url)
    raise FetchError(f"离线模式无对应 fixture: {url}")


def _live_fetch(url: str):
    return fetch_setlist(url)


def _load_events(local: str | None, url: str) -> list:
    if local:
        with open(local, encoding="utf-8") as f:
            return parse_events_html(f.read(), base_url=PAGE_BASE_URL)
    return fetch_events(url)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_song_index",
        description="S8 探针：构建/加载歌曲反向索引 + 打印某歌出现过的 LIVE",
    )
    parser.add_argument("--song", metavar="歌名", help="查询歌名（如 'Dance in the Light'）")
    parser.add_argument("--local", metavar="HTML", help="用本地列表页 HTML 离线（详情页走 fixture 映射）")
    parser.add_argument("--url", default=EVENT_LIST_URL, help=f"列表页 URL（默认 {EVENT_LIST_URL}）")
    parser.add_argument("--cache", metavar="JSON", help="歌曲索引缓存文件（存在则加载，缺省自动保存）")
    parser.add_argument("--save", metavar="JSON", help="构建后保存索引到该路径")
    parser.add_argument("--refresh", action="store_true",
                        help="加载 --cache 后先增量刷新（重抓列表页 diff 新增）再查询")
    args = parser.parse_args(argv)

    # ---- 索引来源：缓存加载 or 构建 ----
    idx = None
    if args.cache and os.path.isfile(args.cache):
        idx = load_song_index(args.cache)
        if idx is not None:
            print(f"[索引] 从缓存加载: {args.cache}（{len(idx.entries)} 首歌 / {len(idx.source_urls)} 个来源页）")
    if idx is None:
        print("[索引] 构建歌曲反向索引（抓全部详情页 setlist）…")
        t0 = time.time()
        try:
            events = _load_events(args.local, args.url)
        except (FetchError, OSError) as exc:
            print(f"[ERROR] 列表抓取失败: {exc}", file=sys.stderr)
            return 1
        fetch = _local_fetch if args.local else _live_fetch
        idx = build_song_index(events, fetch)
        print(f"[索引] 构建完成：{len(idx.entries)} 首歌 / {len(idx.source_urls)} 个来源页"
              f"（耗时 {time.time() - t0:.1f}s）")

    # ---- 增量刷新（可选） ----
    if args.refresh:
        try:
            events = _load_events(args.local, args.url)
        except (FetchError, OSError) as exc:
            print(f"[ERROR] 刷新列表抓取失败: {exc}", file=sys.stderr)
            return 1
        fetch = _local_fetch if args.local else _live_fetch
        before = len(idx.source_urls)
        refresh_song_index(idx, events, fetch)
        print(f"[索引] 增量刷新：新增 {len(idx.source_urls) - before} 个来源页"
              f"（共 {len(idx.entries)} 首歌）")

    # ---- 落盘 ----
    save_path = args.save or args.cache
    if save_path:
        try:
            save_song_index(idx, save_path)
            print(f"[索引] 已保存: {save_path}")
        except OSError as exc:
            print(f"[WARN] 索引保存失败: {exc}", file=sys.stderr)

    # ---- 查询 ----
    if not args.song:
        print(f"[完成] 索引就绪（{len(idx.entries)} 首歌）。用 --song <歌名> 查询。")
        return 0

    hits = match_songs(args.song, idx)
    if not hits:
        print(f"歌曲「{args.song}」：无命中")
        return 0
    for i, entry in enumerate(hits, 1):
        print(f"候选 {i}: 「{entry.title}」（{len(entry.appearances)} 场 LIVE）")
        for j, a in enumerate(entry.appearances, 1):
            sub = f"（{a.sub_title}）" if a.sub_title else ""
            print(f"  {j}. {a.event_title}{sub} | {a.date} | {a.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
