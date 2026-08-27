"""管道集成测试（M1 → M3 → M2 → M4）— 爱马仕官方新闻 QQ 转发机器人.

本脚本把 M1–M4 四个已完成模块按真实数据流串联跑一遍，验证「管道可用性」：

    M1 fetch_news_list(limit=5)  真实抓取列表（CMS API）      -> NewsItem[]
    M3 init_db + get_new_items   全新库喂入 + 重喂幂等        -> 新增 NewsItem[]
    M2 parse_detail(items[0])    真实抓取详情（__NEXT_DATA__） -> NewsDetail
    M4 translate(detail)         缺 Key 明确报错 / fake client 管道打通 / 有 Key 时真实翻译

退出码：0 = 全部通过；1 = 任一步失败或断言失败。

运行：python scripts/pipeline_test_m1m4.py
"""
import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "vendor"))

import httpx  # noqa: E402

import m1_fetcher  # noqa: E402
import m2_parser  # noqa: E402
import m3_store  # noqa: E402
import m4_translator as m4  # noqa: E402
from models import NewsDetail, TranslationResult  # noqa: E402

# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
_PASSED: list[str] = []
_FAILED: list[str] = []


def _check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _PASSED.append(name)
        print(f"  [PASS] {name}" + (f"  {detail}" if detail else ""))
    else:
        _FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


def _step(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# M4 fake client（与 tests/test_m4_translator.py 同构，零网络）
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=None):
        self.status_code = status_code
        self._json = json_data
        self.text = text if text is not None else (
            json.dumps(json_data, ensure_ascii=False) if json_data is not None else ""
        )

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeClient:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse(200, {"choices": [{"message": {"content": '{"title_zh":"标题","body_zh":"正文"}'}}]})


def _ok(content: str) -> FakeResponse:
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


# ---------------------------------------------------------------------------
# M1：列表抓取
# ---------------------------------------------------------------------------
def step_m1() -> list:
    _step("M1 列表抓取（真实网络）")
    items = m1_fetcher.fetch_news_list(limit=5)
    _check("M1 返回 5 条", len(items) == 5, f"实际 {len(items)}")
    _check("M1 最新在前（date 不升序且首条非空）", bool(items) and items[0].date, f"首条 {items[0].id} {items[0].date}")
    _check("M1 id/url 契约（id 为 URL 末段）",
           all(it.id and it.url.endswith("/" + it.id) for it in items))
    _check("M1 title 非空", all(bool(it.title) for it in items))
    for it in items[:3]:
        print(f"    {it.id}  {it.date}  {it.title[:44]}")
    return items


# ---------------------------------------------------------------------------
# M3：增量检测（全新库 → 5 新增；重喂 → 0 新增；mark_pushed 后 unpushed 剔除）
# ---------------------------------------------------------------------------
def step_m3(items: list) -> str:
    _step("M3 增量检测（.tmp/pipeline_test/ 临时库）")
    ts = time.strftime("%Y%m%d_%H%M%S")
    db_dir = os.path.join(ROOT, ".tmp", "pipeline_test", f"run_{ts}")
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, "state.db")

    m3_store.init_db(db_path)
    new1 = m3_store.get_new_items(items, db_path)
    _check("M3 首喂 5 条 → 5 新增", len(new1) == 5, f"实际 {len(new1)}")
    _check("M3 新增顺序与输入一致", [n.id for n in new1] == [n.id for n in items])

    new2 = m3_store.get_new_items(items, db_path)
    _check("M3 重喂同一批 → 0 新增（幂等）", len(new2) == 0, f"实际 {len(new2)}")

    m3_store.mark_pushed(new1[0].id, db_path)
    unpushed = m3_store.get_unpushed(db_path)
    _check("M3 mark_pushed 后 unpushed 剔除该条", len(unpushed) == 4 and new1[0].id not in {u.id for u in unpushed})
    print(f"    db: {db_path}")
    return db_path


# ---------------------------------------------------------------------------
# M2：详情解析（真实网络）
# ---------------------------------------------------------------------------
def step_m2(item) -> NewsDetail:
    _step("M2 详情解析（真实网络）")
    detail = m2_parser.parse_detail(item)
    _check("M2 id/url/title/date 与列表一致",
           detail.id == item.id and detail.url == item.url
           and detail.title == item.title and detail.date == item.date)
    _check("M2 body_text 无 HTML 标签", "<" not in detail.body_text and ">" not in detail.body_text)
    _check("M2 段落 \\n\\n 分隔（非空正文）", (not detail.body_text) or "\n\n" in detail.body_text,
           f"{len(detail.body_text)} chars")
    _check("M2 images 为 CMS Image/get 形态且 ≤4 张",
           all(u.startswith("https://cmsapi-frontend.idolmaster-official.jp/sitern/api/idolmaster/Image/get?path=") for u in detail.images)
           and len(detail.images) <= m2_parser.MAX_IMAGES,
           f"{len(detail.images)} 张")
    print(f"    {detail.id}  title={detail.title[:40]!r}  body={len(detail.body_text)}chars  images={len(detail.images)}")
    return detail


# ---------------------------------------------------------------------------
# M4：翻译
# ---------------------------------------------------------------------------
def step_m4(detail: NewsDetail) -> None:
    _step("M4 翻译（缺 Key 路径 + fake client 管道 + 真实 Key 可选）")

    # 4.1 缺 Key 必须明确报错（不静默乱码）
    try:
        m4.translate(detail, config=m4.TranslatorConfig(api_key=""))
        _check("M4 缺 Key 明确报错", False, "未抛 TranslationError")
    except m4.TranslationError as exc:
        _check("M4 缺 Key 明确报错", "DEEPSEEK_API_KEY" in str(exc), str(exc)[:80])

    # 4.2 fake client 打通管道：NewsDetail -> TranslationResult
    canned = ('{"title_zh":"【活动】偶像大师 新情报发布会 决定举办！",'
              '"body_zh":"来自偶像大师 闪耀色彩的新情报发布会决定举办。\\n\\n预计于2026年9月12日（周六）实施。"}')
    fake = FakeClient([_ok(canned)])
    cfg = m4.TranslatorConfig(api_key="sk-pipeline-test")
    res = m4.translate(detail, config=cfg, client=fake)
    _check("M4 fake client 管道打通（TranslationResult）", isinstance(res, TranslationResult))
    _check("M4 title_zh/body_zh 非空", bool(res.title_zh.strip()) and bool(res.body_zh.strip()))
    _check("M4 fake 调用 1 次且端点为 chat/completions",
           len(fake.calls) == 1 and fake.calls[0]["url"].endswith("/chat/completions"),
           fake.calls[0]["url"] if fake.calls else "无调用")
    print(f"    fake title_zh: {res.title_zh}")

    # 4.3 真实翻译（有 Key 才跑）
    real_cfg = m4.load_config()
    if real_cfg.api_key:
        real = m4.translate(detail, config=real_cfg)
        _check("M4 真实翻译产出非空", bool(real.title_zh.strip()) and bool(real.body_zh.strip()))
        print(f"    [live] title_zh: {real.title_zh}")
        print(f"    [live] body_zh 前 60 字: {real.body_zh[:60]!r}")
    else:
        print("    [live] SKIP: 未配置 DEEPSEEK_API_KEY（.env），真实翻译待 Key 注入后复跑")


def main() -> int:
    print("M1→M3→M2→M4 管道集成测试 开始")
    t0 = time.time()
    items = step_m1()
    step_m3(items)
    detail = step_m2(items[0])
    step_m4(detail)
    print(f"\n耗时 {time.time() - t0:.1f}s")
    print(f"\n结果: {len(_PASSED)} passed, {len(_FAILED)} failed")
    if _FAILED:
        print("[FAILED] 失败项: " + ", ".join(_FAILED))
        return 1
    print("[ALL PASS]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
