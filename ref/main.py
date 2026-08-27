"""M7 主控 / 调度（Orchestrator）— 爱马仕官方新闻 QQ 转发机器人.

规格：docs/module-specs.md §2 M7（本文件为其实现）。
职责：常驻单进程，按固定间隔串联 M1→M3→M2→M4→M5→M6；统一日志、异常不退出、
     失败条目下轮自动补救（M3 规格 §6 约定由 M7 调 ``get_unpushed()``）。

本地留档（2026-08-26 新增需求）
------------------------------
- 每次抓到新闻（详情解析成功后）即留档，**与推送解耦**：即使翻译/推送失败也留原文与图片。
- 目录：``data/archive/<新闻日期 YYYY-MM-DD>/<news_id>/``（按新闻发布日期分文件夹，
  跨企划同日期新闻自然归入同一日期目录；item.date 为空时回退抓取当天日期）。
- 内容：``原文.md``（标题/日期/链接/正文）+ ``译文.md``（有译文时）+ ``meta.json``
  （id/url/标题/日期/留档时间/是否已翻译/翻译失败原因）+ ``images/NN.<ext>``（正文配图）。
- 幂等：文本文件每次覆写；图片只下载缺失的（不重复抓）。留档失败仅告警，绝不阻断主流程。

翻译失败直发原文（2026-08-26 需求变更）
----------------------------------------
- 翻译失败时**不再等下轮补救**，而是：留档原文（meta 记 translation_error）→
  **直接推送原文**，并附「⚠️ AI 翻译失败：<原因>（本条为原文直发）」说明。
- 失败原因按 news_id 进程内记忆（重启清除），后续轮次跳过重复翻译、直接直发原文
  （避免每轮对同一失败条目重复调用翻译 API）；仍推送不成功（如 NapCat 断开）则下轮重试。

显式配置（2026-08-26 需求变更）
-------------------------------
- ``config.yaml`` 新增 ``orchestrator:`` 段，可显式调整轮询间隔与新闻 API 基址：
  - ``poll_interval_sec``：轮询间隔（秒），命令行 ``--interval`` 可覆盖；
  - ``api_base``：新闻列表 CMS API 基址（含尾部斜杠），传给 ``fetch_news_list(api_base=...)``。

启动时间截断（2026-08-26 需求变更）
------------------------------------
- 默认**只推送「启动时间之后更新/发布」的新闻**：每轮 ``fetch_news_list(..., min_updated=启动时刻)``
  客户端截断（CMS ``updated`` 字段，缺失回退 ``startdate``）——启动前就存在的旧新闻一律不推，
  避免首次启动把最新一批新闻全部补推刷屏。
- 启动时把状态库中**历史遗留的未推送条目标记为已处理**（跳过），防止补救循环补推旧新闻。
- ``--no-cutoff`` 可关闭截断（补推/特殊用途）。

启动交互（2026-08-26 决策，见 docs/modules/M7-orchestrator-plan.md）
--------------------------------------------------------------------
- 每次启动都会询问本次要监听哪些企划（brand）的新闻（官方 7 个 code，
  见 ``m1_fetcher.BRAND_CODES``）；编号多选，``0``/``all`` = 全部不过滤。
- 上次选择持久化到 ``data/m7_brands.json``，启动时显示为默认，直接回车沿用。
- 非交互路径：``--brands SHINYCOLORS,GAKUEN`` 跳过询问（供 M9 服务化 / 定时任务用）。

后台挂载
--------
- ``scripts/start_bot.cmd`` 新开独立 cmd 窗口运行本文件（窗口内交互 + 滚动日志，
  Ctrl+C / 关窗口即停止）；``scripts/stop_bot.cmd`` 按窗口标题结束。
- 窗口内已 ``chcp 65001`` + ``PYTHONUTF8=1``，本文件再强制 UTF-8 输出，
  保证日文企划名 / emoji 不因 GBK 控制台乱码或崩溃。

主循环（每轮）
--------------
1. ``fetch_news_list(limit, brands, api_base)`` 抓取（带企划白名单，M1 客户端筛选）；
2. ``get_new_items()`` 增量检测（无新增则本轮结束）；
3. 逐条 ``parse_detail → 留档(原文+图片) → translate → 留档(译文) → format_message → push``；
   翻译失败 → 留档原文 + 直发原文（附失败说明）；
4. 推送成功 ``mark_pushed``，逐群 ``record_push_result``（失败留在 push_log）；
5. ``get_unpushed()`` 补救之前推送失败的条目（含本轮刚失败的）。

推送成功口径：**至少一个群成功即 mark_pushed**（避免健康群重复推送；
失败群记录在 push_log 供人工补发——重复推送比漏群更扰民，见 worklog）。

``--dry-run`` 使用独立临时状态库 ``data/state_dryrun.db``，不污染正式 state.db。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

# 本文件位于 src/ 下：`python src\main.py` 时 sys.path[0]=src/，
# `from models import ...` 与 `from m1_fetcher import ...` 直接可用，无需改 path。
from m1_fetcher import BRAND_CODES, fetch_news_list
from m2_parser import parse_detail
from m3_store import (
    DEFAULT_DB_PATH,
    get_new_items,
    get_unpushed,
    init_db,
    mark_pushed,
    record_push_result,
)
from m4_translator import load_config as load_translator_config
from m4_translator import translate
from m5_formatter import MAX_IMAGES, TEMPLATE_FOOTER, TEMPLATE_HEADER, _split_segments
from m5_formatter import format_message
from m6_notifier import _coerce_group_ids, _read_config_file  # 复用 M6 的 YAML 子集解析器/群号归一化（避免第三份重复实现）
from m6_notifier import load_config as load_notifier_config
from m6_notifier import push
from models import PushMessage

# 环境兜底：本机依赖已 vendor 化（沙箱无法 pip 安装），正常环境走系统 site-packages
try:
    import httpx
except ImportError:  # pragma: no cover
    _vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
    if os.path.isdir(_vendor):
        sys.path.insert(0, _vendor)
        import httpx
    else:
        raise

logger = logging.getLogger("m7")

ROOT = Path(__file__).resolve().parent.parent
BRANDS_FILE = ROOT / "data" / "m7_brands.json"   # 上次企划选择（启动默认）
LOG_DIR = ROOT / "data" / "logs"
ARCHIVE_DIR = ROOT / "data" / "archive"          # 本地留档根目录（按新闻日期分子目录）
DRY_DB_PATH = ROOT / "data" / "state_dryrun.db"  # dry-run 独立状态库，防污染正式库

DEFAULT_INTERVAL = 300   # 规格 M7：默认 5 分钟
DEFAULT_LIMIT = 20       # 每轮抓取窗口（规格 M1：最新 10–20 条）
DEFAULT_MAX_LEN = 3500   # 单条 QQ 消息字符上限（M5 默认）
IMAGE_TIMEOUT = 20.0     # 单张配图下载超时（秒）

# 状态通知默认文案（config.yaml orchestrator.notify_startup / notify_shutdown 可覆盖）。
# 占位符：startup = {brands}/{interval}/{mode}/{groups}/{cutoff}/{time}；
#         shutdown = {rounds}/{ok}/{fail}/{time}。未知占位符原样保留不崩溃。
DEFAULT_STARTUP_TEXT = (
    "🤖 M7 爱马仕新闻转发机器人 已启动\n"
    "监听企划：{brands}\n"
    "轮询间隔：{interval} 秒\n"
    "模式：{mode}\n"
    "目标群：{groups}\n"
    "时间截断：{cutoff}\n"
    "启动时间：{time}"
)
DEFAULT_SHUTDOWN_TEXT = (
    "🤖 M7 已停止\n"
    "本次运行：{rounds} 轮 · 推送成功 {ok} 条 · 失败 {fail} 条\n"
    "停止时间：{time}"
)

# 翻译失败记忆：news_id -> 失败信息（进程内，重启清除——避免每轮对同一失败条目重复调翻译 API）
_TRANSLATE_FAILED: dict[str, str] = {}


def _suppress_preexisting_unpushed(db_path=None) -> int:
    """启动时间截断启用时：把状态库中历史遗留的未推送条目标记为已处理（跳过）。

    这些条目都是**本次启动之前**就已抓取到的旧新闻（已在 seen_items 中）——
    若不跳过，补救循环（get_unpushed）会在启动后把它们补推出去，
    违反「只推送启动时间之后更新的新闻」。返回跳过的条数。
    """
    count = 0
    for item in get_unpushed(db_path):
        mark_pushed(item.id, db_path)
        count += 1
    if count:
        logger.info("启动截断：跳过 %d 条启动前遗留的未推送旧新闻（不再补推）", count)
    return count


# ---------------------------------------------------------------------------
# 显式配置：config.yaml 的 orchestrator: 段（轮询间隔 / 新闻 API 基址）
# ---------------------------------------------------------------------------
def load_orchestrator_config(config_path: Optional[str] = None) -> dict:
    """读取 ``orchestrator:`` 段；缺失/损坏回退 {}（调用方用内置默认）。

    字段：``poll_interval_sec``（轮询间隔秒）/ ``api_base``（CMS API 基址，含尾斜杠）。
    """
    try:
        cfg = _read_config_file(str(config_path or ROOT / "config.yaml"))
    except Exception:  # noqa: BLE001 — 配置缺失/损坏时回退默认
        return {}
    o = cfg.get("orchestrator") if isinstance(cfg, dict) else None
    return o if isinstance(o, dict) else {}


# ---------------------------------------------------------------------------
# 开机/关机状态通知（orchestrator.notify_groups）
# ---------------------------------------------------------------------------
def _load_notify_groups(orch: dict) -> list[str]:
    """读取 orchestrator.notify_groups（开机/关机通知群）；空/缺失 -> []（关闭通知）。

    兼容 YAML 子集解析器形态：真 list / JSON 数组字符串 / 逗号分隔字符串（复用 M6 归一化）。
    """
    raw = orch.get("notify_groups")
    if not raw:
        return []
    return _coerce_group_ids(raw)


def _notify_template(orch: dict, key: str, default: str) -> str:
    """读 config 中的通知文案模板（key 如 notify_startup）；缺失/非字符串用默认文案。

    注意：YAML 子集解析器不解码转义，config 里的 ``\\n`` 在此统一还原为换行。
    """
    v = orch.get(key)
    if not isinstance(v, str) or not v.strip():
        return default
    return v.replace("\\n", "\n")


def _render_template(template: str, **kwargs) -> str:
    """渲染通知模板；未知占位符原样保留（如 {brands}），不因模板缺字段而崩溃。"""

    class _Lenient(dict):
        def __missing__(self, key):  # noqa: D105 — 未知占位符保留原文
            return "{" + key + "}"

    return template.format_map(_Lenient(**kwargs))


def _send_notification(notifier_cfg, groups: list[str], text: str) -> None:
    """给通知群发一条状态消息（开机/关机）；失败仅告警，绝不阻断主流程。

    用普通文本消息（临时关闭 merge_forward，避免短状态消息也包一层合并聊天记录）。
    """
    if not groups:
        return
    try:
        msg = PushMessage(group_ids=list(groups), segments=[text], images=[], link="")
        cfg = dataclasses.replace(notifier_cfg, merge_forward=False)
        results = push(msg, config=cfg)
        if not any(r.ok for r in results):
            logger.warning("状态通知发送失败: %s", [r.error for r in results])
    except Exception as exc:  # noqa: BLE001 — 通知失败不影响主流程
        logger.warning("状态通知发送异常: %s", exc)


# ---------------------------------------------------------------------------
# 企划选择：上次记忆 + 每次启动交互询问
# ---------------------------------------------------------------------------
def _load_last_brands() -> Optional[list[str]]:
    """读 data/m7_brands.json 的上次选择；缺失/损坏/全非法 -> None。"""
    try:
        data = json.loads(BRANDS_FILE.read_text(encoding="utf-8"))
        raw = data.get("brands")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(raw, list):
        return None
    known = [b for b in raw if isinstance(b, str) and b.strip().upper() in BRAND_CODES]
    return list(dict.fromkeys(b.upper() for b in known)) or None


def _save_last_brands(brands: Optional[list[str]]) -> None:
    """持久化本次选择（None=全部）。失败仅告警，不阻断启动。"""
    try:
        BRANDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        BRANDS_FILE.write_text(
            json.dumps({"brands": brands}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("保存上次企划选择失败（不影响本次运行）: %s", exc)


def parse_brand_input(raw: str, codes: list[str]) -> Optional[list[str]]:
    """把用户输入解析为企划 code 列表；None = 全部企划；非法输入返回空列表。

    :param raw: 用户原始输入（已 strip）：'' = 沿用默认（由调用方处理），
        '0'/'all'/'全部' = 全部，'1,5,6' = 编号多选（去重保序）
    :param codes: 可选项列表（顺序即编号 1..N）
    :return: list[code] / None（全部）；输入非法返回 []（调用方重新询问）
    """
    s = raw.strip().lower()
    if s in ("0", "all", "全部"):
        return None
    parts = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]
    try:
        idxs = [int(p) for p in parts]
    except ValueError:
        return []
    if not idxs or any(i < 1 or i > len(codes) for i in idxs):
        return []
    chosen = [codes[i - 1] for i in idxs]
    return list(dict.fromkeys(chosen))  # 去重保序


def select_brands() -> Optional[list[str]]:
    """交互询问本次监听的企划（每次启动必问）。None = 全部企划。

    回车 = 沿用上次选择（无记录则全部）；输入非法重新询问；stdin 非交互
    （EOFError，如被调度器拉起）回退上次/全部并告警。
    """
    codes = list(BRAND_CODES)
    last = _load_last_brands()
    print("\n=== M7 新闻监听启动 ===")
    if last:
        print(f"上次选择: {', '.join(last)}（直接回车沿用）")
    print("可选企划：")
    for i, code in enumerate(codes, 1):
        print(f"  {i}. {code:<16} {BRAND_CODES[code]}")
    print("  0. 全部企划（不过滤）")
    while True:
        try:
            raw = input("请输入要监听的企划编号（逗号分隔，如 1,5,6；回车=沿用上次/全部）: ").strip()
        except EOFError:
            logger.warning("stdin 非交互（无法询问企划），回退上次选择/全部")
            return last
        if not raw:
            return last  # 无记录时 last 为 None = 全部
        chosen = parse_brand_input(raw, codes)
        if chosen is None:
            return None
        if not chosen:
            print(f"  输入无效：请用逗号分隔的编号（1–{len(codes)}），0=全部")
            continue
        return chosen


# ---------------------------------------------------------------------------
# 本地留档：原文 + 图片 + 译文，按新闻日期分文件夹
# ---------------------------------------------------------------------------
def _guess_image_ext(url: str) -> str:
    """从 CMS 图片 URL 猜扩展名（Image/get?path=xxx.jpg）；失败兜底 .jpg。"""
    m = re.search(r"[?&]path=([^&]+)", url)
    path = unquote(urlsplit(m.group(1)).path) if m else unquote(urlsplit(url).path)
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp") else ".jpg"


def _download_image(url: str, dest: Path) -> bool:
    """下载单张配图到 dest；失败仅告警返回 False（不阻断留档/推送）。"""
    try:
        with httpx.Client(
            timeout=IMAGE_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
            },
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return True
    except Exception as exc:  # noqa: BLE001 — 单张图片失败不阻断
        logger.warning("图片下载失败（跳过）: %s: %s", url, exc)
        return False


def archive_news(
    item,
    detail,
    tr,
    *,
    base_dir: Path = ARCHIVE_DIR,
    translation_error: Optional[str] = None,
) -> Optional[Path]:
    """本地留档一条新闻：``<base_dir>/<新闻日期>/<news_id>/``。

    内容：原文.md（必有）+ 译文.md（有译文时）+ meta.json（含 translation_error，有则记）+
    images/NN.<ext>（配图）。幂等：文本每次覆写；图片只下载缺失的。
    任何失败仅告警（返回 None），绝不抛出、不阻断主流程。
    """
    try:
        date_str = item.date or detail.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target = base_dir / date_str / item.id
        target.mkdir(parents=True, exist_ok=True)
        img_dir = target / "images"
        img_dir.mkdir(exist_ok=True)

        # 原文（原始日文 + 元信息）
        original = (
            f"# {detail.title}\n\n"
            f"- 日期: {detail.date}\n"
            f"- 链接: {detail.url}\n"
            f"- 新闻ID: {item.id}\n\n"
            f"{detail.body_text}\n"
        )
        (target / "原文.md").write_text(original, encoding="utf-8")

        # 译文（有则写，无则留待下轮补）
        if tr is not None:
            translated = f"# {tr.title_zh}\n\n{tr.body_zh}\n"
            (target / "译文.md").write_text(translated, encoding="utf-8")

        # 元信息
        meta: dict = {
            "id": item.id,
            "url": item.url,
            "title": item.title,
            "date": item.date,
            "archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "translated": tr is not None,
        }
        if translation_error:
            meta["translation_error"] = translation_error
        (target / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 配图：只补缺
        images = detail.images or []
        saved = 0
        for idx, url in enumerate(images, 1):
            dest = img_dir / f"{idx:02d}{_guess_image_ext(url)}"
            if dest.exists() and dest.stat().st_size > 0:
                saved += 1
                continue
            if _download_image(url, dest):
                saved += 1
        logger.info(
            "已留档: %s（原文 + %s译文 + 图片 %d/%d 张）",
            target, "有" if tr is not None else "无", saved, len(images),
        )
        return target
    except Exception as exc:  # noqa: BLE001 — 留档失败不阻断
        logger.warning("留档失败（不影响推送）: %s: %s", getattr(item, "id", "?"), exc)
        return None


# ---------------------------------------------------------------------------
# 推送层：组装好的消息 -> M6（dry-run / 真实推送 / 回写）
# ---------------------------------------------------------------------------
def _push_message(msg: PushMessage, detail, *, notifier_cfg, dry_run: bool, db_path) -> bool:
    """推送组装好的消息；成功（任一群）mark_pushed，逐群 record_push_result。

    dry-run 不真实推送，仅日志预览并在独立 dry 库回写（防重复翻译）。
    """
    if dry_run:
        preview = (msg.segments[0] if msg.segments else "").replace("\n", " ⏎ ")
        logger.info(
            "[DRY-RUN] 将推送 %s -> 群 %s：%d 段 / %d 图 | 预览: %s",
            detail.id, msg.group_ids, len(msg.segments), len(msg.images), preview[:120],
        )
        mark_pushed(detail.id, db_path)
        return True
    results = push(msg, config=notifier_cfg)
    for r in results:
        record_push_result(r, detail.id, db_path)
    ok = any(r.ok for r in results)
    if ok:
        mark_pushed(detail.id, db_path)
    else:
        logger.warning("推送失败（所有群均未成功）: %s（%s）", detail.id, detail.title[:40])
    return ok


def _push_original_fallback(
    item,
    detail,
    error: str,
    *,
    notifier_cfg,
    max_len: int,
    dry_run: bool,
    db_path,
) -> bool:
    """翻译失败：直接推送原文，并附「翻译失败」说明（本条为原文直发）。

    复用 M5 的模板常量与分片逻辑（TEMPLATE_HEADER/FOOTER、_split_segments），
    保证与正常推送同风格、同分片规则。
    """
    text = (
        f"{TEMPLATE_HEADER.format(date=detail.date)}\n{detail.title}\n\n"
        f"{detail.body_text}\n\n"
        f"⚠️ AI 翻译失败：{error[:100]}（本条为原文直发）\n\n"
        f"{TEMPLATE_FOOTER.format(url=detail.url)}"
    )
    msg = PushMessage(
        group_ids=list(notifier_cfg.group_ids),
        segments=_split_segments(text, max_len),
        images=list(detail.images[:MAX_IMAGES]),
        link=detail.url,
    )
    logger.warning("翻译失败，改为直发原文: %s（%s）", detail.id, error[:100])
    return _push_message(msg, detail, notifier_cfg=notifier_cfg, dry_run=dry_run, db_path=db_path)


# ---------------------------------------------------------------------------
# 单条处理：M2 详情 → 留档 → M4 翻译 → 留档译文 → M5 → M6
# ---------------------------------------------------------------------------
def _process_one(
    item,
    *,
    translator_cfg,
    notifier_cfg,
    max_len: int,
    dry_run: bool,
    db_path=None,
    archive_dir: Optional[Path] = ARCHIVE_DIR,
) -> bool:
    """处理单条新闻；返回是否推送成功（至少一个群；dry-run 视为成功）。

    翻译失败：留档原文（meta 记 translation_error）→ **直发原文 + 失败说明**，
    并记忆失败原因（进程内），后续轮次跳过重复翻译直接直发（重启后重试翻译）。
    其余异常（详情解析/组装/推送）记录后返回 False，条目留 unpushed 下轮补救。
    """
    try:
        detail = parse_detail(item)
    except Exception as exc:  # noqa: BLE001 — 详情解析失败，本轮跳过
        logger.exception("详情解析失败（%s，下轮补救）: %s", getattr(item, "id", "?"), exc)
        return False

    # 翻译：失败则直发原文（记忆失败原因，避免每轮重复调用翻译 API）
    tr = None
    error: Optional[str] = None
    if item.id in _TRANSLATE_FAILED:
        error = _TRANSLATE_FAILED[item.id]
        logger.warning("翻译此前已失败（%s），跳过重试直接直发原文: %s", item.id, error[:80])
    else:
        try:
            tr = translate(detail, config=translator_cfg)
        except Exception as exc:  # noqa: BLE001 — 翻译失败走直发原文分支
            error = f"{type(exc).__name__}: {exc}"
            _TRANSLATE_FAILED[item.id] = error
            logger.exception("翻译失败（将直发原文）: %s: %s", detail.id, error)

    # 留档原文 + 图片（与翻译/推送解耦）
    if archive_dir is not None:
        archive_news(item, detail, tr, base_dir=archive_dir, translation_error=error)

    if tr is None:
        assert error is not None
        try:
            return _push_original_fallback(
                item, detail, error,
                notifier_cfg=notifier_cfg, max_len=max_len,
                dry_run=dry_run, db_path=db_path,
            )
        except Exception as exc:  # noqa: BLE001 — 直发原文也失败，下轮补救
            logger.exception("直发原文失败（%s，下轮补救）: %s", detail.id, exc)
            return False

    try:
        msg = format_message(detail, tr, notifier_cfg.group_ids, max_len=max_len)
        return _push_message(msg, detail, notifier_cfg=notifier_cfg, dry_run=dry_run, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 — 组装/推送失败，下轮补救
        logger.exception("组装或推送失败（%s，下轮补救）: %s", detail.id, exc)
        return False


# ---------------------------------------------------------------------------
# 单轮流水线：M1 → M3 → 逐条 M2/留档/M4/M5/M6 → 失败补救
# ---------------------------------------------------------------------------
def run_once(
    brands: Optional[list[str]],
    *,
    limit: int = DEFAULT_LIMIT,
    max_len: int = DEFAULT_MAX_LEN,
    translator_cfg=None,
    notifier_cfg=None,
    dry_run: bool = False,
    db_path=None,
    archive_dir: Optional[Path] = ARCHIVE_DIR,
    api_base: Optional[str] = None,
    min_updated: Optional[int] = None,
) -> dict:
    """跑一轮完整流水线，返回统计 dict（fetched/new/pushed_ok/pushed_fail/errors）。

    整轮异常（如 M1 抓取失败）原样上抛，由主循环捕获后等下一周期（规格 M7）。
    :param min_updated: 时间截断（Unix 秒）——只处理启动时间之后更新/发布的新闻。
    """
    stats = {"fetched": 0, "new": 0, "pushed_ok": 0, "pushed_fail": 0, "errors": 0}
    label = "全部企划" if brands is None else ",".join(brands)

    items = fetch_news_list(limit=limit, brands=brands, api_base=api_base, min_updated=min_updated)
    stats["fetched"] = len(items)
    logger.info("M1 抓取完成：%d 条（%s）", len(items), label)

    new_items = get_new_items(items, db_path)
    stats["new"] = len(new_items)
    if new_items:
        logger.info("M3 新增 %d 条：%s", len(new_items), ", ".join(i.id for i in new_items))

    for item in new_items:
        ok = _process_one(
            item, translator_cfg=translator_cfg, notifier_cfg=notifier_cfg,
            max_len=max_len, dry_run=dry_run, db_path=db_path, archive_dir=archive_dir,
        )
        stats["pushed_ok" if ok else "pushed_fail"] += 1
        stats["errors"] += 0 if ok else 1

    # 失败补救：新条目已处理，成功者已 mark_pushed；剩下的就是未成功的（含本轮失败）
    unpushed = get_unpushed(db_path)
    if unpushed:
        retried = 0
        for item in unpushed:
            logger.info("补救未推送条目: %s（%s）", item.id, (item.title or "")[:40])
            ok = _process_one(
                item, translator_cfg=translator_cfg, notifier_cfg=notifier_cfg,
                max_len=max_len, dry_run=dry_run, db_path=db_path, archive_dir=archive_dir,
            )
            stats["pushed_ok" if ok else "pushed_fail"] += 1
            stats["errors"] += 0 if ok else 1
            retried += 1
        logger.info("本轮补救 %d 条未推送条目", retried)
    return stats


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:  # 幂等：避免重复添加（热重载/测试场景）
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)
        fh = RotatingFileHandler(
            LOG_DIR / "m7.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    else:
        root.handlers[0].setFormatter(fmt)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="m7",
        description="爱马仕官方新闻转发主控（M7）：轮询监听指定企划新闻、本地留档并推送 QQ 群",
    )
    parser.add_argument("--brands", help="企划白名单，逗号分隔（跳过交互询问），如 SHINYCOLORS,GAKUEN")
    parser.add_argument("--interval", type=int, default=None,
                        help="轮询间隔秒数（缺省读 config.yaml 的 orchestrator.poll_interval_sec，再缺省 300）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"每轮抓取窗口条数（默认 {DEFAULT_LIMIT}）")
    parser.add_argument("--max-len", type=int, default=DEFAULT_MAX_LEN,
                        help=f"单条 QQ 消息字符上限（默认 {DEFAULT_MAX_LEN}）")
    parser.add_argument("--archive-dir", default=str(ARCHIVE_DIR),
                        help=f"本地留档根目录（默认 {ARCHIVE_DIR}）")
    parser.add_argument("--no-archive", action="store_true", help="关闭本地留档")
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出（验收用）")
    parser.add_argument("--no-cutoff", action="store_true",
                        help="关闭「启动时间截断」：补推启动前就存在的新闻（默认只推启动后更新的）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只抓取/翻译/组装/留档不推送（用独立 state_dryrun.db，不污染正式库）")
    return parser


def _resolve_brands(args) -> Optional[list[str]]:
    """--brands 显式指定（校验）> 交互询问。返回 None=全部。"""
    if args.brands:
        brands = [b.strip().upper() for b in args.brands.split(",") if b.strip()]
        unknown = sorted(set(brands) - set(BRAND_CODES))
        if unknown:
            raise SystemExit(f"未知企划 code: {unknown}；可选: {','.join(BRAND_CODES)}")
        return brands
    return select_brands()


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台 GBK 无法编码日文/emoji，统一 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")

    args = _build_parser().parse_args(argv)
    _setup_logging()

    brands = _resolve_brands(args)
    _save_last_brands(brands)

    # 显式配置（orchestrator 段）：轮询间隔 / API 基址 / 通知群；命令行 --interval 覆盖前者
    orch = load_orchestrator_config()
    interval = args.interval if args.interval is not None else int(orch.get("poll_interval_sec") or DEFAULT_INTERVAL)
    api_base = str(orch["api_base"]).strip() if orch.get("api_base") else None
    notify_groups = _load_notify_groups(orch)

    translator_cfg = load_translator_config()
    if not getattr(translator_cfg, "api_key", ""):
        logger.warning("未配置 DEEPSEEK_API_KEY（项目根 .env），翻译将失败并改为直发原文")
    notifier_cfg = load_notifier_config()

    db_path = DRY_DB_PATH if args.dry_run else None
    init_db(db_path)
    archive_dir = None if args.no_archive else Path(args.archive_dir)

    # 启动时间截断：默认只推「启动时间之后更新」的新闻；--no-cutoff 关闭
    cutoff = None if args.no_cutoff else int(time.time())
    if cutoff is not None:
        _suppress_preexisting_unpushed(db_path)

    label = "全部企划" if brands is None else ", ".join(brands)
    logger.info(
        "M7 启动：监听 %s | 间隔 %ds | API %s | 目标群 %s | 留档 %s | %s%s",
        label, interval, api_base or "默认", notifier_cfg.group_ids,
        "关闭" if archive_dir is None else archive_dir,
        "DRY-RUN（不推送，独立状态库）" if args.dry_run else "正式推送",
        " | 时间截断：只推启动时间(Unix %d)之后更新的新闻" % cutoff if cutoff is not None else "",
    )

    # 开机通知（notify_groups 全部群）：让用户知道 bot 已启动；文案由 config 模板可自定义
    if notify_groups:
        mode = "DRY-RUN（不推送）" if args.dry_run else "正式推送"
        startup_tpl = _notify_template(orch, "notify_startup", DEFAULT_STARTUP_TEXT)
        _send_notification(notifier_cfg, notify_groups, _render_template(
            startup_tpl,
            brands=label,
            interval=interval,
            mode=mode,
            groups=", ".join(notifier_cfg.group_ids),
            cutoff="只推启动后更新的新闻" if cutoff is not None else "关闭（--no-cutoff）",
            time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    run_stats = {"rounds": 0, "ok": 0, "fail": 0}
    try:
        while True:
            try:
                stats = run_once(
                    brands,
                    limit=args.limit,
                    max_len=args.max_len,
                    translator_cfg=translator_cfg,
                    notifier_cfg=notifier_cfg,
                    dry_run=args.dry_run,
                    db_path=db_path,
                    archive_dir=archive_dir,
                    api_base=api_base,
                    min_updated=cutoff,
                )
                run_stats["rounds"] += 1
                run_stats["ok"] += stats["pushed_ok"]
                run_stats["fail"] += stats["pushed_fail"]
                logger.info(
                    "本轮完成：抓取 %(fetched)d / 新增 %(new)d / 推送成功 %(pushed_ok)d / "
                    "失败 %(pushed_fail)d / 错误 %(errors)d", stats,
                )
            except Exception as exc:  # noqa: BLE001 — 整轮异常不退出，等下一周期（规格 M7）
                logger.exception("本轮执行异常（等待下周期）: %s", exc)
            if args.once:
                break
            logger.info("下一轮 %d 秒后开始（Ctrl+C 停止）…", interval)
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                break
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，M7 停止")
    finally:
        # 关机通知：无论正常退出还是 Ctrl+C 都发，让用户知道 bot 已停止；文案由 config 模板可自定义
        if notify_groups:
            shutdown_tpl = _notify_template(orch, "notify_shutdown", DEFAULT_SHUTDOWN_TEXT)
            _send_notification(notifier_cfg, notify_groups, _render_template(
                shutdown_tpl,
                rounds=run_stats["rounds"],
                ok=run_stats["ok"],
                fail=run_stats["fail"],
                time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))
        logger.info("M7 已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
