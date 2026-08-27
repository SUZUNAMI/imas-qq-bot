"""S3 查询判别 + 模糊匹配 + 时间筛选 — 歌曲列表 bot（songbot）.

把用户输入映射到查询类型（时间 / 名称）：
- 时间 → 按年/月筛选事件列表（``filter_by_time``）；
- 名称 → 唯一事件或候选列表（``match_events``），二次确认子公演（``match_sub``）。

纯函数、零网络，可直接离线单测。依赖 S1 的 ``Event`` / ``SubEvent`` 结构
（含 2026-08-27 补齐的 ``Event.date``，单页事件日期文本）。

匹配策略（施工图 docs/S1-S7-taskplan.md §S3）
------------------------------------------------
- ``normalize``（名称匹配）：NFKC + casefold + 去空白与分隔符（所有非
  字母/数字/日文字符），如 ``'I W S F'`` -> ``'iwsf'``、``'ＭＩＬＬＩＯＮ'`` -> ``'million'``；
- ``normalize_light``（时间判别）：NFKC + casefold + 去首尾空白（保留 ``/`` ``-``
  分隔符，否则 ``2026-07`` 会变成 ``202607`` 无法判别）；
- 打分：完全相等 100 > 子串包含 80 > 词元覆盖 60（query 的每个词元
  「相等 / 子串 / 子序列」命中候选某个词元，支持 ``IWSF`` -> ``IDOL WORLD
  SUPER FESTIVAL`` 这类缩写）> ``difflib`` ratio 兜底按比例；低于阈值（60）
  不算候选。
"""

from __future__ import annotations

import difflib
import re
import sys
import unicodedata
from typing import Optional

from songbot.models_song import Event, SubEvent

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SCORE_EXACT = 100        # 完全相等（normalize 后）
SCORE_CONTAIN = 80       # 候选包含 query 或 query 包含候选
SCORE_TOKEN_COVER = 60   # 词元/缩写覆盖（query 每个词元都命中候选；缩写如 IWSF ~ IDOL WORLD SUPER FESTIVAL）
SCORE_THRESHOLD = 60     # 低于此分不算候选（match_events / match_sub 均适用）
DEFAULT_TOP_N = 5        # 多候选时的返回上限
FALLBACK_MIN_RATIO = 0.8  # SequenceMatcher 兜底的最低 ratio（防 "13thLIVE" 误配 "11thLIVE" 等近似串）

# 名称匹配：normalize 后只保留 半角字母数字 / 平假名 / 片假名 / 汉字。
# 片假名范围拆两段以排除 U+30FB「・」（中点，属于分隔符）：\u3040-\u30fa 假名本体，\u30fc-\u30ff 长音符「ー」等
_REMOVE_RE = re.compile(r"[^0-9a-z\u3040-\u30fa\u30fc-\u30ff\u4e00-\u9fff]+")

# 词元切分：连续字母一段、连续数字一段（"iwsf2026" -> ["iwsf", "2026"]）
_TOKEN_RE = re.compile(r"[a-z]+|[0-9]+")

# 缩写候选：纯字母词（"IDOL WORLD SUPER FESTIVAL 2026" -> ["IDOL","WORLD","SUPER","FESTIVAL"]）
_ACRONYM_WORD_RE = re.compile(r"[A-Za-z]+")

# 时间判别（在 normalize_light 后的原文上进行）
_TIME_YEAR_MONTH_RE = re.compile(r"^(20\d{2})\s*年?\s*(\d{1,2})?\s*月?$")   # "2026年7月" / "2026年" / "2026"
_TIME_YEAR_SEP_MONTH_RE = re.compile(r"^(20\d{2})[/\-.](\d{1,2})$")          # "2026-07" / "2026/07" / "2026.07"
_TIME_MONTH_ONLY_RE = re.compile(r"^(\d{1,2})月$")                            # "7月"（年份用 latest_year 兜底）

# 日期文本：取首个 YYYY/MM 的月份（跨月以起始月为准；无匹配 None）
_MONTH_RE = re.compile(r"(\d{4})/(\d{1,2})")

# 缩写别名：query 前缀 -> 事件标题展开（2026-08-27 live 实测补漏）。
# MOIW = M@STERS OF IDOL WORLD（站点标题为 "THE IDOLM@STER M@STERS OF IDOL WORLD 2025" 等，
# 首字母缩写算法从 "THE IDOLM@STER M@STERS OF IDOL WORLD" 只能推出 "tismsoiw"，推不出 moiw）。
# 匹配时把 "MOIW<年份>" 展开为 "M@STERS OF IDOL WORLD <年份>" 再打分（年份后缀保证只中对应年份）。
ALIASES: dict[str, str] = {
    "moiw": "M@STERS OF IDOL WORLD",
}

# 命令前缀（split_command 强制前缀分流）。song 由 S8 线程接入（2026-08-27 S9 约定：
# S9 先落地 binding/unbind/bindings/update + live，S8 落地 song）。
COMMANDS: frozenset = frozenset({"live", "song", "binding", "unbind", "bindings", "update", "refresh"})


# ---------------------------------------------------------------------------
# 文本规范化
# ---------------------------------------------------------------------------
def normalize(s: str) -> str:
    """名称匹配用：NFKC + casefold + 去空白与分隔符。

    ``'ＭＩＬＬＩＯＮ'`` / ``'million'`` -> ``'million'``；
    ``'I W S F'`` / ``'I・W・S・F'`` -> ``'iwsf'``；
    日文（平假名/片假名/汉字）保留，如 ``'学園アイドルマスター'`` 原样。
    """
    return _REMOVE_RE.sub("", unicodedata.normalize("NFKC", s).casefold())


def normalize_light(s: str) -> str:
    """时间判别用：NFKC + casefold + 去首尾空白（保留 ``/`` ``-`` 分隔符）。"""
    return unicodedata.normalize("NFKC", s).casefold().strip()


# ---------------------------------------------------------------------------
# 查询类型判别 + 时间解析
# ---------------------------------------------------------------------------
def classify_query(s: str) -> str:
    """查询类型："time"（时间格式）| "name"（名称）。

    ``13thLIVE``、``IWSF2026`` 等不含「年/月」且非 ``YYYY[/-.]MM`` 格式，不会误判为 time。
    """
    t = normalize_light(s)
    if _TIME_YEAR_MONTH_RE.match(t) or _TIME_YEAR_SEP_MONTH_RE.match(t) or _TIME_MONTH_ONLY_RE.match(t):
        return "time"
    return "name"


def parse_time_query(s: str, latest_year: int) -> Optional[tuple[int, Optional[int]]]:
    """解析时间查询 -> (year, month)；非时间格式返回 None。

    - ``"2026年7月"`` -> ``(2026, 7)``；``"2026-07"`` / ``"2026/07"`` / ``"2026.07"`` 同；
    - ``"7月"`` -> ``(latest_year, 7)``（无年份用索引最大年份兜底）；
    - ``"2026年"`` / ``"2026"`` -> ``(2026, None)``（全年）；
    - ``"13thLIVE"`` -> ``None``。
    """
    t = normalize_light(s)
    m = _TIME_YEAR_MONTH_RE.match(t)
    if m:
        year = int(m.group(1))
        month = int(m.group(2)) if m.group(2) else None
        return (year, month)
    m = _TIME_YEAR_SEP_MONTH_RE.match(t)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = _TIME_MONTH_ONLY_RE.match(t)
    if m:
        return (latest_year, int(m.group(1)))
    return None


def parse_month(date_text: str) -> Optional[int]:
    """取日期文本首个 ``YYYY/MM`` 的月份；无匹配返回 None（跨月以起始月为准）。

    ``"2026/07/04(土)・05(日)"`` -> 7；``"(DAY1夜・DAY2昼)"`` -> None。
    """
    m = _MONTH_RE.search(date_text)
    return int(m.group(2)) if m else None


def filter_by_time(events: list[Event], year: int, month: Optional[int] = None) -> list[Event]:
    """按年/月筛选事件（保持原顺序），返回**全部**命中。

    - ``month is None`` → 返回 ``Event.year == str(year)`` 的全部事件；
    - 否则 year 匹配 且（单页事件 ``parse_month(Event.date)``，多日事件任一
      ``SubEvent.date``）== month；
    - 日期文本无 ``YYYY/MM``（parse_month 返回 None）的事件**仅按年保留**
      （防御：不因日期形态异常而丢事件）；
    - 注：展示截断（回复上限 10 条 + 「还有 N 场…」提示）由调用方（S6 主控）
      负责，本函数保持筛选语义完整（施工图单测要点：``filter_by_time(events, 2026)``
      返回 14 个）。
    """
    year_s = str(year)
    out: list[Event] = []
    for ev in events:
        if ev.year != year_s:
            continue
        if month is None:
            out.append(ev)
            continue
        if ev.sub_events:
            months = [parse_month(s.date) for s in ev.sub_events]
            hit = any(m == month for m in months) or all(m is None for m in months)
        else:
            pm = parse_month(ev.date)
            hit = pm == month or pm is None
        if hit:
            out.append(ev)
    return out


# ---------------------------------------------------------------------------
# 名称匹配打分
# ---------------------------------------------------------------------------
def _tokens(s: str) -> list[str]:
    """normalize 后文本按 连续字母/连续数字 切词（日文整段为一个词元）。"""
    return _TOKEN_RE.findall(s)


def _acronym(raw: str) -> str:
    """候选原始文本的首字母缩写：纯字母词的首字母连接。

    ``"IDOL WORLD SUPER FESTIVAL 2026"`` -> ``"iwsf"``（只取字母词，数字词跳过）。
    """
    words = _ACRONYM_WORD_RE.findall(raw)
    return "".join(w[0] for w in words).casefold()


def _token_hit(qt: str, ct: str) -> bool:
    """单个词元是否命中：相等，或字母词元为候选词元子串。

    数字词元只允许相等——``DAY3`` 的 ``3`` 不得因子串匹配中 ``13thLIVE`` 的 ``13``。
    """
    if qt == ct:
        return True
    if qt.isdigit() or ct.isdigit():
        return False
    return qt in ct


def _token_cover(qtokens: list[str], cand_norm: str) -> bool:
    """query 每个词元都命中候选的某个词元（``_token_hit``）。

    方向单向：只认「query 词元 ⊆ 候选词元」。反向（候选 token ⊂ query token）
    会误配——如 query ``13`` 与候选 ``DAY1`` 的 ``1`` 子串匹配。不用子序列
    （太宽松：'iwsf' 会误匹配任意长英文标题里的 i-w-s-f 子序列）。
    """
    cand_tokens = _tokens(cand_norm)
    if not cand_tokens:
        return False
    for qt in qtokens:
        if not any(_token_hit(qt, ct) for ct in cand_tokens):
            return False
    return True


def _acronym_cover(qtokens: list[str], cand_raw: str) -> bool:
    """缩写匹配：query 的纯字母词元全部命中候选首字母缩写（相等，或 query ⊆ 候选缩写）。

    ``IWSF2026`` 的字母词元 ``iwsf`` ~ 候选 ``IDOL WORLD SUPER FESTIVAL 2026``
    的首字母 ``iwsf``；数字词元（如 ``2026``）交给 ``_token_cover`` 处理。
    反向（候选缩写 ⊂ query 词元）会误配——如候选只含一个字母词 ``in`` 时
    缩写为 ``i``，``i in 'iwsf'`` 导致误命中。
    """
    acr = _acronym(cand_raw)
    if not acr:
        return False
    letters = [t for t in qtokens if t.isalpha()]
    if not letters:
        return False
    return all(t == acr or t in acr for t in letters)


def _score_text(query_norm: str, cand_raw: str, cand_norm: str) -> int:
    """单个候选文本与 query 的得分（0–100）。"""
    if not query_norm or not cand_norm:
        return 0
    if query_norm == cand_norm:
        return SCORE_EXACT
    if query_norm in cand_norm or cand_norm in query_norm:
        return SCORE_CONTAIN
    qt = _tokens(query_norm)
    if qt and (_token_cover(qt, cand_norm) or _acronym_cover(qt, cand_raw)):
        return SCORE_TOKEN_COVER
    ratio = difflib.SequenceMatcher(None, query_norm, cand_norm).ratio()
    # 兜底只认高相似（防 "13thLIVE" 与 "11thLIVE"/"12thLIVE" 这类近似串误配）
    return int(ratio * 100) if ratio >= FALLBACK_MIN_RATIO else 0


def _event_candidates(ev: Event) -> list[tuple[str, str]]:
    """事件所有可匹配文本：(原始文本, normalize 后文本) 列表。

    候选 = title + 各子公演 title/full_title（缩写匹配需要原始文本，故成对返回）。
    """
    raw = [ev.title]
    raw += [s.title for s in ev.sub_events]
    raw += [s.full_title for s in ev.sub_events]
    return [(x, normalize(x)) for x in raw if x]


def _score_event(query_norm: str, ev: Event) -> int:
    """事件得分 = 所有候选文本的最高分。"""
    return max(_score_text(query_norm, r, n) for r, n in _event_candidates(ev)) if ev else 0


def _query_forms(query: str) -> list[str]:
    """query 的匹配形态列表：normalize 原文 + 别名展开（ALIASES 前缀命中时）。

    ``"MOIW 2025"`` -> ``["moiw2025", "mstersofidolworld2025"]``；
    ``"IWSF2026"`` -> ``["iwsf2026"]``（无别名，只保留原文形态）。
    """
    q = normalize(query)
    forms = [q] if q else []
    for key, raw in ALIASES.items():
        if q == key or q.startswith(key):
            suffix = q[len(key):].strip()
            forms.append(normalize(raw + (" " + suffix if suffix else "")))
    return forms


def match_events(query: str, events: list[Event], top_n: int = DEFAULT_TOP_N) -> list[Event]:
    """名称匹配：按得分降序返回事件列表。

    - 唯一且分 >= 阈值 → ``[该 Event]``；
    - 多个 → 取 top N（默认 5）作候选列表（同分保持原顺序，稳定排序）；
    - 无 / 空 query → ``[]``。
    """
    forms = _query_forms(query)
    if not forms:
        return []
    scored = [(max(_score_event(f, ev) for f in forms), ev) for ev in events]
    scored = [(s, ev) for s, ev in scored if s >= SCORE_THRESHOLD]
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    if len(scored) == 1:
        return [scored[0][1]]
    return [ev for _, ev in scored[:top_n]]


def match_sub(query: str, event: Event) -> Optional[SubEvent]:
    """二次确认：定位多日事件的子公演（"DAY1" / "DAY2" / 子标题 / 序号）；无则 None。

    - 纯数字（1 基）→ 第 N 个子公演（越界 None）；
    - 否则按子公演 title/full_title 打分，最高分 >= 阈值者命中（同分取第一个）。
    """
    if not query or not event or not event.sub_events:
        return None
    q = normalize(query)
    if not q:
        return None
    if q.isdigit():
        idx = int(q) - 1
        if 0 <= idx < len(event.sub_events):
            return event.sub_events[idx]
        return None
    best: Optional[SubEvent] = None
    best_score = 0
    for sub in event.sub_events:
        cands = [(x, normalize(x)) for x in (sub.title, sub.full_title) if x]
        score = max(_score_text(q, r, n) for r, n in cands) if cands else 0
        if score > best_score:
            best_score = score
            best = sub
    return best if best_score >= SCORE_THRESHOLD else None


def split_command(s: str) -> Optional[tuple[str, str]]:
    """命令前缀分流：按首个空白切分，首词元（casefold 后）∈ COMMANDS 时返回 (cmd, 剩余)。

    - ``"live IWSF2026"`` -> ``("live", "IWSF2026")``；
    - ``"binding iwsf IDOL WORLD SUPER FESTIVAL 2026"`` -> ``("binding", "iwsf IDOL WORLD SUPER FESTIVAL 2026")``；
    - ``"bindings"`` -> ``("bindings", "")``（无剩余）；``"update live"`` -> ``("update", "live")``；
    - 无前缀 / 未知命令 -> ``None``（强制前缀，供 bot.py 分流回用法提示）。
    """
    t = (s or "").strip()
    if not t:
        return None
    parts = t.split(maxsplit=1)
    cmd = parts[0].casefold()
    if cmd not in COMMANDS:
        return None
    return (cmd, parts[1].strip() if len(parts) > 1 else "")


# ---------------------------------------------------------------------------
# 命令行自测：python -m songbot.s3_match
# ---------------------------------------------------------------------------
def _main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json
    import os
    import sys

    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(prog="s3_match", description="S3 查询判别/匹配自测（纯函数，本地 fixture 或 JSON 事件文件）")
    parser.add_argument("query", help="查询串，如 IWSF2026 / 13thLIVE / 2026年7月")
    parser.add_argument("--local", default=os.path.join(_root, "fixtures", "imas_db_song_event.html"),
                        help="列表页 HTML（默认 fixture，离线）")
    args = parser.parse_args(argv)

    sys.path.insert(0, _root)
    from songbot.s1_fetch_events import parse_events_html, PAGE_BASE_URL

    with open(args.local, encoding="utf-8") as f:
        events = parse_events_html(f.read(), base_url=PAGE_BASE_URL)

    qtype = classify_query(args.query)
    print(f"query={args.query!r} 类型={qtype}")
    if qtype == "time":
        latest = max(int(e.year) for e in events)
        parsed = parse_time_query(args.query, latest)
        if parsed is None:
            print("  无法解析")
            return 1
        year, month = parsed
        hits = filter_by_time(events, year, month)
        label = f"{year}年" + (f"{month}月" if month else "")
        print(f"  时间筛选 {label}: {len(hits)} 个")
        for ev in hits:
            if ev.sub_events:
                subs = ", ".join(f"{s.title} ({s.date})" for s in ev.sub_events)
                print(f"    [多日] {ev.title} | {subs}")
            else:
                print(f"    [单页] {ev.title} | date={ev.date}")
        return 0

    hits = match_events(args.query, events)
    if not hits:
        print("  无命中")
        return 0
    for ev in hits:
        print(f"    {ev.year} {ev.title} | url={'有' if ev.url else ''} subs={len(ev.sub_events)}")
    if len(hits) == 1 and hits[0].sub_events:
        sub = match_sub("DAY1", hits[0])
        print(f"  二次确认示例 match_sub('DAY1') -> {sub.title if sub else None}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
