"""M5 消息组装（Formatter）— 爱马仕官方新闻 QQ 转发机器人.

把「原文详情 + 译文」拼成最终要发给 QQ 群的消息（``PushMessage``），
并处理好**分片**与**图片**（契约 docs/module-specs.md §1.4）。
契约类型统一来自 ``src/models.py``（单一事实源，防接口漂移）。

规格：docs/modules/M5-formatter.md（自包含交接文档）。

设计约定
------------------------------------------------------------------------
1. **纯函数**：无 IO、无网络、不碰数据库——最易测试与复用。
2. 契约类型复用 ``src/models.py``（NewsDetail / TranslationResult / PushMessage），
   本模块不重复定义；re-export 保持 ``from m5_formatter import PushMessage`` 可用（与 m4 同法）。
3. 入口 ``format_message(detail, tr, group_ids, *, max_len=3500)``：
   - ``max_len`` 为单段字符上限（规格 §5「可配置」，keyword-only 不破坏契约签名）；
   - 输入鸭子类型：models 类 / 任意含契约字段的 dataclass / dict 均可（兼容 M2 并行期自建类与 JSON 管道）。
4. 模板（规格 §4，冻结，原文与译文均拼接）::

       【NEWS】<date>
       <原标题 title>

       <原文正文 body_text>

       ——— 中文翻译 ———
       <标题译文 title_zh>
       <正文译文 body_zh>

       🔗 原文：<url>

   即消息同时包含完整原文（标题+正文）与完整译文（标题+正文），
   分隔线以上为原文、以下为译文，结尾固定带原文链接。
5. 分片（规格 §5）：贪心按段落（``\\n\\n``）边界累加，单段 ≤ max_len；
   原文与译文都进消息后文本更长，分片更常触发——某段落本身超限时
   才在该段内部硬切（先按行、再按字符），避免一句话被劈开。
6. 图片（规格 §6）：原样透传，防御性 ``images[:4]``（M2 已限制 4 张，这里不再二次截断之外的动作）。
"""

from __future__ import annotations

import dataclasses

from models import NewsDetail, PushMessage, TranslationResult  # 契约类型单一事实源（module-specs §1，见 src/models.py）

# ---------------------------------------------------------------------------
# 常量（规格 §5/§6；站点/运营策略调整时先改这里）
# ---------------------------------------------------------------------------
DEFAULT_MAX_LEN = 3500   # 单段文本上限（字符）
MAX_IMAGES = 4           # 配图上限（M2 已限制，此处防御性截断）

# 模板标记（规格 §4，冻结）
TEMPLATE_HEADER = "【NEWS】{date}"
TEMPLATE_DIVIDER = "——— 中文翻译 ———"
TEMPLATE_FOOTER = "🔗 原文：{url}"

# NewsDetail / TranslationResult / PushMessage 契约类型已移至 src/models.py（统一 import 防漂移），
# 此处经 `from models import ...` re-export，保持 `from m5_formatter import ...` 公共 API 不变。

# ---------------------------------------------------------------------------
# 输入归一化（鸭子类型：本类 / 任意契约 dataclass / dict）
# ---------------------------------------------------------------------------
_DETAIL_FIELDS = ("id", "url", "title", "date", "body_text", "images")
_TR_FIELDS = ("title_zh", "body_zh")


def _coerce(obj, fields: tuple[str, ...], cls, what: str):
    """把输入归一化为契约类型；支持本类 / 任意含契约字段的 dataclass / dict。"""
    if isinstance(obj, cls):
        return obj
    if isinstance(obj, dict):
        src: dict = obj
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        src = {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
    else:
        raise ValueError(
            f"不支持的 {what} 输入类型: {type(obj).__name__}"
            "（需 dict 或含契约字段的 dataclass）"
        )
    missing = [k for k in fields if k not in src]
    if missing:
        raise ValueError(f"{what} 缺少字段: {missing}")
    return cls(**{k: src[k] for k in fields})


def _coerce_detail(detail) -> NewsDetail:
    return _coerce(detail, _DETAIL_FIELDS, NewsDetail, "NewsDetail")


def _coerce_tr(tr) -> TranslationResult:
    return _coerce(tr, _TR_FIELDS, TranslationResult, "TranslationResult")


# ---------------------------------------------------------------------------
# 分片（规格 §5：段落边界优先，单段超限才内部硬切）
# ---------------------------------------------------------------------------
def _hard_split(text: str, max_len: int) -> list[str]:
    """单段内部硬切：优先按 ``\\n`` 行边界贪心凑段，行仍超限才按字符切。"""
    out: list[str] = []
    buf = ""
    for line in text.split("\n"):
        candidate = line if not buf else f"{buf}\n{line}"
        if len(candidate) <= max_len:
            buf = candidate
            continue
        if buf:
            out.append(buf)
            buf = ""
        if len(line) > max_len:  # 单行超限：按字符硬切
            for i in range(0, len(line), max_len):
                out.append(line[i : i + max_len])
        else:
            buf = line
    if buf:
        out.append(buf)
    return out


def _split_segments(text: str, max_len: int) -> list[str]:
    """把完整消息切成 ≤ max_len 的段；尽量在段落（``\\n\\n``）边界切。"""
    if len(text) <= max_len:
        return [text]
    segments: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        candidate = para if not buf else f"{buf}\n\n{para}"
        if len(candidate) <= max_len:
            buf = candidate
            continue
        if buf:
            segments.append(buf)
            buf = ""
        if len(para) > max_len:  # 单段仍超限：该段内部硬切
            segments.extend(_hard_split(para, max_len))
        else:
            buf = para
    if buf:
        segments.append(buf)
    return segments


# ---------------------------------------------------------------------------
# 模板组装
# ---------------------------------------------------------------------------
def _build_full_text(detail: NewsDetail, tr: TranslationResult) -> str:
    """按规格 §4 模板拼出完整消息文本（不分片）。

    原文（标题 + body_text 正文）与译文（title_zh + body_zh 正文）均拼接，
    分隔线以上为原文、以下为译文；非空内容块之间空行分隔，空字段自动跳过。
    """
    header = TEMPLATE_HEADER.format(date=detail.date)
    if detail.title:
        header = f"{header}\n{detail.title}"

    translated = "\n".join(part for part in (tr.title_zh, tr.body_zh) if part)

    parts: list[str] = [header]
    if detail.body_text:
        parts.append(detail.body_text)
    parts.append(TEMPLATE_DIVIDER)
    if translated:
        parts.append(translated)
    parts.append(TEMPLATE_FOOTER.format(url=detail.url))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 入口（契约签名冻结；max_len 为 keyword-only 可配置项）
# ---------------------------------------------------------------------------
def format_message(
    detail,
    tr,
    group_ids: list[str],
    *,
    max_len: int = DEFAULT_MAX_LEN,
) -> PushMessage:
    """把「原文详情 + 译文」组装为 ``PushMessage``（纯函数，无 IO）。

    :param detail: NewsDetail（本类 / 任意契约 dataclass / dict）
    :param tr: TranslationResult（本类 / 任意契约 dataclass / dict）
    :param group_ids: 目标群号列表（字符串；拷贝进结果，不持有外部引用）
    :param max_len: 单段字符上限（默认 3500，规格 §5 可配置）
    :raises ValueError: 输入类型/字段缺失非法，或 max_len 非正数
    """
    if not isinstance(max_len, int) or isinstance(max_len, bool) or max_len <= 0:
        raise ValueError(f"max_len 必须为正整数，收到: {max_len!r}")
    detail = _coerce_detail(detail)
    tr = _coerce_tr(tr)
    text = _build_full_text(detail, tr)
    return PushMessage(
        group_ids=list(group_ids),
        segments=_split_segments(text, max_len),
        images=list(detail.images[:MAX_IMAGES]),  # 原样透传，防御性截断（规格 §6）
        link=detail.url,
    )


# ---------------------------------------------------------------------------
# 命令行自测：python src/m5_formatter.py [--max-len N]
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    import sys

    if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台 GBK 无法编码 emoji（🔗），统一 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    argv = list(sys.argv[1:] if argv is None else argv)
    max_len = DEFAULT_MAX_LEN
    if argv and argv[0] == "--max-len" and len(argv) > 1:
        try:
            max_len = int(argv[1])
        except ValueError:
            print(f"[ERROR] --max-len 需要整数，收到: {argv[1]}")
            return 1
    detail = NewsDetail(
        id="01_17821",
        url="https://idolmaster-official.jp/news/01_17821",
        title="【イベント】アイドルマスター 新情報発表会 開催決定！",
        date="2026-08-26",
        body_text=(
            "『アイドルマスター シャイニーカラーズ』より、新情報発表会の開催が決定いたしました。\n\n"
            "2026年9月12日（土）に実施予定です。詳細は後日お知らせいたします。"
        ),
        images=["https://example.com/a.jpg"],
    )
    tr = TranslationResult(
        title_zh="【活动】偶像大师 新情报发布会 举办决定！",
        body_zh="来自『偶像大师 闪耀色彩』的新情报发布会举办决定。\n\n预定于 2026 年 9 月 12 日（周六）举行。详情日后公布。",
    )
    msg = format_message(detail, tr, ["123456789"], max_len=max_len)
    for i, seg in enumerate(msg.segments, 1):
        print(f"--- segment {i}/{len(msg.segments)} (len={len(seg)}) ---")
        print(seg)
    print(f"--- images={msg.images} link={msg.link} ---")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main())
