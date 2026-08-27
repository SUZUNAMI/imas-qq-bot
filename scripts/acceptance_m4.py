"""M4 acceptance checks: no-key error path + (可选) 真实 DeepSeek 翻译。

用法：
    python scripts/acceptance_m4.py
- 无 DEEPSEEK_API_KEY 时：验证「明确报错、不静默乱码」路径，跳过真实调用（打印 SKIP）。
- 有 Key（.env / 环境变量）时：对样例 NewsDetail 跑一次真实翻译并打印结果。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "vendor"))

import m4_translator as m4  # noqa: E402
from models import NewsDetail  # noqa: E402

# 样例（含术语表词，验收「按表翻译」）
SAMPLE = NewsDetail(
    id="01_17821",
    url="https://idolmaster-official.jp/news/01_17821",
    title="【イベント】アイドルマスター 新情報発表会 開催決定！",
    date="2026-08-26",
    body_text=(
        "『アイドルマスター シャイニーカラーズ』より、新情報発表会の開催が決定いたしました。\n\n"
        "2026年9月12日（土）に実施予定です。詳細は後日お知らせいたします。"
    ),
    images=[],
)


def check_no_key_error() -> None:
    """验收 8.4：无 Key 必须明确报错，不静默产出乱码。"""
    try:
        m4.translate(SAMPLE, config=m4.TranslatorConfig(api_key=""))
        raise AssertionError("expected TranslationError, got none")
    except m4.TranslationError as exc:
        msg = str(exc)
        assert "DEEPSEEK_API_KEY" in msg, msg
        print(f"[no-key] OK: {msg[:90]}")


def check_config_surface() -> None:
    """验收：配置加载默认值与术语表合并可用。"""
    cfg = m4.load_config()
    print(f"[config] base_url={cfg.base_url} model={cfg.model} temperature={cfg.temperature} "
          f"max_retries={cfg.max_retries} terms={len(cfg.terms)}")
    assert cfg.base_url and cfg.model
    assert "アイドルマスター" in cfg.terms


def check_live() -> None:
    """验收 8.1–8.3：有 Key 时跑真实翻译（需网络）。"""
    cfg = m4.load_config()
    if not cfg.api_key:
        print("[live] SKIP: 未配置 DEEPSEEK_API_KEY（.env 或环境变量），跳过真实调用")
        return
    result = m4.translate(SAMPLE, config=cfg)
    assert isinstance(result.title_zh, str) and result.title_zh.strip(), "title_zh 为空"
    assert isinstance(result.body_zh, str) and result.body_zh.strip(), "body_zh 为空"
    print("[live] title_zh:", result.title_zh)
    print("[live] body_zh:")
    print(result.body_zh)
    print("[live] 术语表校验：", "偶像大师" in (result.title_zh + result.body_zh) or "爱马仕" in (result.title_zh + result.body_zh))


def main() -> int:
    check_no_key_error()
    check_config_surface()
    check_live()
    print("[ALL PASS]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
