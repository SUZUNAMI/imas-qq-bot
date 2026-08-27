"""S1/S2 探针 — 歌曲列表 bot（songbot）.

S1：抓取 /song/event 并打印全部顶层事件
（年份分组、单页/多日、子公演 DAY 名称/日期/URL、品牌徽章），
作为 S1 验收「125 事件、多日事件 day 子项/日期/URL 与网页一致」的执行依据。
S2：--setlist <公演详情页 URL> 抓取并打印结构化 Setlist（标题/日期场馆/出演者/曲目），
作为 S2 验收「3 个真实 URL 输出正确 Setlist」的执行依据。

用法：
    python scripts/probe_song_event.py                 # 官方站点（HTTP）
    python scripts/probe_song_event.py --limit 5       # 只打印前 5 个
    python scripts/probe_song_event.py --local fixtures/imas_db_song_event.html  # S1 离线验收
    python scripts/probe_song_event.py --setlist http://imas-db.jp/song/event/million_13th_day1.html
    python scripts/probe_song_event.py --setlist-local fixtures/imas_db_iwsf_day1.html  # S2 离线验收
    python scripts/probe_song_event.py --json          # 输出 JSON（便于比对）
"""

import argparse
import json
import os
import sys

# Windows 控制台默认 GBK，日文/「・」等字符可能超出可编码范围；强制 stdout/stderr 走 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from songbot.s1_fetch_events import EVENT_LIST_URL, PAGE_BASE_URL, FetchError, fetch_events, parse_events_html
from songbot.s2_fetch_setlist import fetch_setlist, parse_setlist_html


def _setlist_dict(sl) -> dict:
    return {
        "title": sl.title,
        "date_venue": sl.date_venue,
        "performers": sl.performers,
        "url": sl.url,
        "tracks": [
            {"no": t.no, "title": t.title, "brand": t.brand,
             "performers": t.performers, "link": t.link}
            for t in sl.tracks
        ],
    }


def _event_dict(ev) -> dict:
    return {
        "title": ev.title,
        "year": ev.year,
        "brands": ev.brands,
        "url": ev.url,
        "sub_events": [
            {"title": s.title, "full_title": s.full_title, "url": s.url, "date": s.date}
            for s in ev.sub_events
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_song_event",
        description="S1 探针：抓取并打印 imas-db 歌曲事件列表（默认官方站点，可用 --local 离线）",
    )
    parser.add_argument("--url", default=EVENT_LIST_URL, help=f"列表页 URL（默认 {EVENT_LIST_URL}）")
    parser.add_argument("--local", metavar="HTML", help="用本地 HTML 文件离线解析（替代网络抓取）")
    parser.add_argument("--limit", type=int, default=0, help="只打印前 N 个事件（0=全部）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是人类可读文本")
    # S2：--setlist <url>（live）/ --setlist-local <html>（离线验收）
    parser.add_argument("--setlist", metavar="URL", help="S2：抓取公演详情页并打印结构化 Setlist")
    parser.add_argument("--setlist-local", metavar="HTML", help="S2：用本地 HTML 文件离线解析 Setlist")
    args = parser.parse_args(argv)

    # --- S2 分支：Setlist ---
    if args.setlist or args.setlist_local:
        try:
            if args.setlist_local:
                with open(args.setlist_local, encoding="utf-8") as f:
                    sl = parse_setlist_html(f.read(), url="")
            else:
                sl = fetch_setlist(args.setlist)
        except (FetchError, OSError) as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(_setlist_dict(sl), ensure_ascii=False, indent=1))
            return 0

        print(f"标题: {sl.title}")
        print(f"日期/场馆: {sl.date_venue}")
        print(f"出演者({len(sl.performers)}): {'、'.join(sl.performers)}")
        print(f"曲目({len(sl.tracks)}):")
        for t in sl.tracks:
            brand = f" [{t.brand}]" if t.brand else ""
            link = f" link={t.link}" if t.link else ""
            print(f"  {t.no:>3}. {t.title}{brand} | {','.join(t.performers)}{link}")
        return 0

    try:
        if args.local:
            with open(args.local, encoding="utf-8") as f:
                html = f.read()
            events = parse_events_html(html, base_url=PAGE_BASE_URL)
        else:
            events = fetch_events(args.url)
    except (FetchError, OSError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = [_event_dict(e) for e in events]
        if args.limit > 0:
            data = data[: args.limit]
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0

    multi = sum(1 for e in events if e.sub_events)
    print(f"共 {len(events)} 个顶层事件（多日 {multi} / 单页 {len(events) - multi}），年份降序：")
    shown = events if args.limit <= 0 else events[: args.limit]
    for ev in shown:
        if ev.sub_events:
            print(f"  [多日] {ev.year} {ev.title}")
            print(f"          brands={ev.brands}")
            for s in ev.sub_events:
                print(f"          - {s.title} | date={s.date} | url={s.url} | full={s.full_title}")
        else:
            print(f"  [单页] {ev.year} {ev.title} | url={ev.url} | brands={ev.brands}")
    if args.limit > 0 and len(events) > args.limit:
        print(f"  …（共 {len(events)} 个，去掉 --limit 查看全部）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
