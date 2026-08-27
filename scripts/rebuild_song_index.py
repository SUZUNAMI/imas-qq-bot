"""重建歌曲反向索引（含全量 setlist 缓存），落盘 data/songbot_song_index.json。

用途：网络不稳时后台补全「漏抓的歌曲/公演」，无需重启 bot 或手动发 update live。
用法：python scripts/rebuild_song_index.py [--max-wait 秒] [--rounds 轮]

- 列表页抓取失败会重试（间隔 30s），直到成功或超过 --max-wait（默认 1800s=30min）。
- 成功后全量构建（含完整 setlist）；详情页抓取失败的「洞」再补抓 --rounds 轮（默认 5）。
- 最后落盘 data/songbot_song_index.json（与 songbot.song_index_cache 同路径）。
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "vendor"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from songbot.s1_fetch_events import FetchError, fetch_events  # noqa: E402
from songbot.s2_fetch_setlist import fetch_setlist  # noqa: E402
from songbot.s8_song_index import (  # noqa: E402
    _appearance_specs,
    _merge_setlist,
    build_song_index,
    save_song_index,
)

DEFAULT_OUT = os.path.join(ROOT, "data", "songbot_song_index.json")


def _parse_args(argv: list[str]) -> tuple[int, int]:
    max_wait, rounds = 1800, 5
    i = 0
    while i < len(argv):
        if argv[i] == "--max-wait" and i + 1 < len(argv):
            max_wait = int(argv[i + 1]); i += 2
        elif argv[i] == "--rounds" and i + 1 < len(argv):
            rounds = int(argv[i + 1]); i += 2
        else:
            i += 1
    return max_wait, rounds


def main(argv: list[str] | None = None) -> int:
    max_wait, rounds = _parse_args(argv or sys.argv[1:])
    deadline = time.time() + max_wait
    events = None
    while time.time() < deadline:
        try:
            events = fetch_events()
            break
        except FetchError as exc:
            print(f"[retry] 列表页抓取失败（剩余 {int(deadline - time.time())}s）: {exc}", flush=True)
            time.sleep(30)
    if events is None:
        print(f"列表页在 {max_wait}s 内仍失败，退出", flush=True)
        return 1

    specs = _appearance_specs(events)
    print(f"列表页成功：{len(events)} 事件 / {len(specs)} 详情页，开始全量构建（含 setlist）…", flush=True)
    idx = build_song_index(events, fetch_setlist)
    print(f"首轮构建：{len(idx.entries)} 首歌 / {len(idx.source_urls)}/{len(specs)} 页 / {len(idx.setlists)} 份 setlist", flush=True)

    for r in range(1, rounds + 1):
        missing = [s for s in specs if s["url"] not in idx.source_urls]
        if not missing:
            print("无缺失页，构建完成", flush=True)
            break
        print(f"补抓第 {r} 轮：{len(missing)} 个缺失页…", flush=True)
        for s in missing:
            _merge_setlist(idx, s, fetch_setlist)   # 内部 try/except，失败不中断
        time.sleep(5)

    missing = [s for s in specs if s["url"] not in idx.source_urls]
    save_song_index(idx, DEFAULT_OUT)
    print(f"已落盘 {DEFAULT_OUT}：{len(idx.entries)} 首歌 / {len(idx.source_urls)}/{len(specs)} 页 "
          f"/ {len(idx.setlists)} 份 setlist；仍缺 {len(missing)} 页", flush=True)
    return 0 if not missing else 2


if __name__ == "__main__":
    sys.exit(main())
