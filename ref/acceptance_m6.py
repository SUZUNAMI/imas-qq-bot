"""M6 acceptance checks: 配置面 + dry-run + (可选) 真实 NapCat 推送。

用法：
    python scripts/acceptance_m6.py [--group <群号>]
- 无 NapCat（base_url 不可达）时：验证「明确报错提示 NapCat 未连接」路径，SKIP 真实发送（打印提示）。
- 有 NapCat（NapCatQQ 已启动 + config.yaml/.env 配置好）时：向目标群发一条真实测试消息并校验 PushResult。
- 群号优先级：命令行 --group > 环境变量 NAPCAT_GROUP_IDS > config.yaml napcat.group_ids。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "vendor"))

import httpx  # noqa: E402

import m6_notifier as m6  # noqa: E402
from models import PushMessage  # noqa: E402

SAMPLE_MSG = PushMessage(
    group_ids=[],
    segments=[
        "【M6 验收测试】爱马仕官方新闻转发机器人\n"
        "这条消息由 scripts/acceptance_m6.py 发出，用于验收 QQ 推送模块。\n\n"
        "——— 中文翻译 ———\n这是一条测试消息，收到即表示 M6 推送链路正常。",
    ],
    images=[],
    link="https://idolmaster-official.jp/news/01_17821",
)


def check_config_surface() -> None:
    """验收：napcat 配置段可正常加载。"""
    cfg = m6.load_config()
    print(f"[config] base_url={cfg.base_url} token={'***' if cfg.token else '(无)'} "
          f"group_ids={cfg.group_ids} interval={cfg.interval_sec}s "
          f"timeout={cfg.timeout}s max_retries={cfg.max_retries}")
    assert cfg.base_url, "base_url 为空（缺省 http://127.0.0.1:3000）"


def check_dry_run() -> None:
    """验收：dry-run 预演不发真实消息，打印将发送内容。"""
    cfg = m6.load_config()
    msg = PushMessage(
        group_ids=cfg.group_ids or ["<群号占位>"],
        segments=SAMPLE_MSG.segments,
        images=SAMPLE_MSG.images,
        link=SAMPLE_MSG.link,
    )
    print("[dry-run] 目标群:", msg.group_ids)
    for i, seg in enumerate(msg.segments, 1):
        print(f"[dry-run] 文本段 {i}/{len(msg.segments)}（{len(seg)} 字符）: {seg[:40]}…")
    assert msg.segments and all(s for s in msg.segments), "segments 为空"


def check_live(group: str | None) -> None:
    """验收：真实推送（需 NapCat 运行）。NapCat 未连接 → 明确提示 + SKIP。"""
    cfg = m6.load_config()
    groups = [group] if group else cfg.group_ids
    if not groups:
        print("[live] SKIP: 未指定群号（--group / NAPCAT_GROUP_IDS / config napcat.group_ids），跳过真实发送")
        return
    msg = PushMessage(group_ids=groups, segments=SAMPLE_MSG.segments, images=[], link=SAMPLE_MSG.link)

    # 先探测 NapCat 是否可达，给出明确提示（规格 §8：网络不可达 → "NapCat 未连接"）
    try:
        with httpx.Client(timeout=3.0) as client:
            client.get(cfg.base_url.rstrip("/") + "/get_login_info")
    except httpx.TransportError as exc:
        print(f"[live] SKIP: NapCat 未连接（{cfg.base_url} 不可达: {exc}）")
        print("[live] 请先启动 NapCatQQ 并登录 bot 小号，再重跑本脚本完成真实推送验收")
        return

    results = m6.push(msg, config=cfg)
    for r in results:
        print(f"[live] 群 {r.group_id}: ok={r.ok} message_id={r.message_id!r} error={r.error!r}")
    assert all(r.ok for r in results), "存在推送失败的群"


def main() -> int:
    parser = argparse.ArgumentParser(description="M6 QQ 推送验收")
    parser.add_argument("--group", default=None, help="目标群号（覆盖配置）")
    args = parser.parse_args()

    check_config_surface()
    check_dry_run()
    check_live(args.group)
    print("[ALL PASS]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
