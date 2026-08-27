# M5 消息组装（Formatter）— 执行工作日志

> 线程：M5（本线程）；项目：爱马仕官方新闻 QQ 转发机器人
> 契约：以 `docs/modules/M5-formatter.md` 与 `docs/module-specs.md` §1 为准（冻结）；类型复用 `src/models.py`（单一事实源）。
> 创建：2026-08-26；状态：✅ 已完成（单测 26/26 全绿，验收脚本 ALL PASS；v2 模板变更已回改规格）

---

## 1. 执行计划（2026-08-26）

| 步骤 | 内容 | 验收 | 状态 |
|---|---|---|---|
| S1 规格与契约核对 | 读 M5 交接文档；确认 `models.py` 已含 NewsDetail / TranslationResult / PushMessage（§1.4 已冻结），无需改契约 | 契约类型直接 `from models import`，不重复定义 | ✅ |
| S2 实现 | `src/m5_formatter.py`：`format_message(detail, tr, group_ids, *, max_len=3500) -> PushMessage`，纯函数无 IO | 入口签名符合契约，模板/分片/图片按 §4–§6 | ✅ |
| S3 单测 | `tests/test_m5_formatter.py`（纯逻辑，零网络） | 覆盖 §8 验收 1–5 + 分片边界/硬切/鸭子类型/防御性，26/26 | ✅ |
| S4 验收 | `scripts/acceptance_m5.py`（4 项检查：结构模板/长文分片/图片透传/空正文） | `[ALL PASS]` | ✅ |
| S5 文档回写 | 本日志 + `docs/index.md`（§2/§3/§4）+ `agent.md`（§4） | 索引与实际一致 | ✅ |

## 2. 环境记录（2026-08-26）

- Python 3.13.7；**M5 纯函数，无新增依赖**（不碰 httpx / vendor / requirements.txt）。
- 沙箱提示：Windows 控制台默认 GBK 无法编码 emoji（`🔗` U+1F517），CLI/验收脚本入口 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` 兜底。

## 3. 实现要点（S2）★

**契约复用**：`from models import NewsDetail, TranslationResult, PushMessage`，本模块仅 re-export（与 M4 同法），`from m5_formatter import PushMessage` 公共 API 可用。

**入口**：`format_message(detail, tr, group_ids, *, max_len=3500)`——`max_len` 为 keyword-only 可配置项（规格 §5「可配置」，不破坏契约签名），非法值（≤0 / 非 int / bool）抛 `ValueError`。

**输入鸭子类型**：`_coerce_detail` / `_coerce_tr` 接受 models 类 / 任意含契约字段的 dataclass / dict（兼容 M2 并行期自建类与 JSON 管道，与 M4 同思路）；缺字段/类型非法抛 `ValueError`。注意 `NewsDetail` 六字段全必填，`_DETAIL_FIELDS` 必须含 `images`。

**模板**（规格 §4）：`【NEWS】<date>\n<title>\n\n——— 中文翻译 ———\n<title_zh>\n<body_zh>\n\n🔗 原文：<url>`；空字段（title / title_zh / body_zh 为空）跳过对应行，保留空行结构，消息仍合法。

**分片**（规格 §5）：贪心按段落（`\n\n`）边界累加，单段 ≤ max_len；某段落本身超限才 `_hard_split` 内部硬切——先按 `\n` 行边界凑段，行仍超限才按字符切（每段 ≤ max_len）。链接行 `🔗 原文：<url>` 随末段或独立成段，完整保留。

**图片**（规格 §6）：`detail.images[:4]` 原样透传（防御性截断，不做下载/转码——那是 M6 的事）；`group_ids` / `images` 均拷贝进结果，不持有外部引用。

## 4. 验收记录（S3/S4，2026-08-26）

- `python -m unittest tests.test_m5_formatter -v`：**26/26 通过**。覆盖：契约 re-export、§8 验收 1–5（结构/模板完整/长文分片/图片透传≤4/空正文）、段落边界切分（甲/乙不混段）、单段超限硬切（行边界优先 + 无换行字符硬切）、自定义 max_len、恰好等于上限不分片、链接末段保留、空字段组合、鸭子类型（dict / 异构 dataclass）、非法输入与非法 max_len、group_ids/images 拷贝防御。
- `python -m unittest discover -s tests -p "test_m[1-5]*.py"`：**113/113 通过**（M1 13 + M2 30 + M3 10 + M4 34 + M5 26）——M5 未破坏任何既有模块。
- `python scripts/acceptance_m5.py`：`[ALL PASS]`（structure 1 段 192 字符 · split 2 段各 ≤3500 段落边界切、链接在末段 · images 7→4 截断/空透传 · empty-body 合法）。
- `python src/m5_formatter.py`：CLI 自测输出模板正确（含 emoji 链接行）。

## 5. 交接与遗留

- **交付物**：`src/m5_formatter.py`（`format_message() -> PushMessage`）、`tests/test_m5_formatter.py`（26/26）、`scripts/acceptance_m5.py`。
- **M6 对接**：`format_message(detail, tr, group_ids) -> PushMessage`，字段 `group_ids`（字符串群号）/ `segments`（已分好片，M6 按顺序逐条发）/ `images`（CMS 接口形态 URL，M6 决定转 `[CQ:image]`）/ `link`。无需再排版或分片。
- **M7 对接**：`max_len=3500` 默认即可；如需运营调小（如 3000）传 keyword-only 参数，签名不变。
- **全仓测试说明（2026-08-26）**：`python -m unittest discover -s tests` 全仓 146 个用例中 **2 个失败来自 M6 线程自己的 `test_m6_notifier`**（`test_config_yaml_napcat_section` / `test_partial_failure_does_not_block_other_groups`），M6 并行开发中，非 M5 引入、不在本线程职责内修改。
- **遗留提示**：实测 `test_m[1-5]*` 共 113/113（M1 13 + M2 30 + M3 10 + M4 34 + M5 26）；`docs/index.md` §4 记 M1「8/8」（M1 后续加了品牌白名单测试，实测 13）、M4 worklog 记「35/35」（实测 34）——均属对应线程文档滞后，M5 未代改，建议 M1/M4 线程自行核对。

## 6. v2 变更记录（2026-08-26，线程指令：「将原文与译文均拼接到消息中」）

| 项 | 变更 |
|---|---|
| 模板 | §4 改为**原文与译文均拼接**：原文（标题 + `body_text` 正文）在上、`——— 中文翻译 ———` 分隔线以下为译文（标题 + 正文），结尾 `🔗 原文：<url>` 不变 |
| 输入/输出契约 | **不变**——`PushMessage`（group_ids/segments/images/link）结构照旧，`segments` 为自由文本；`NewsDetail.body_text` 原字段直接使用 |
| 实现 | `_build_full_text()` 重构为「非空内容块之间空行分隔」：`title`/`body_text` 各自成块，空字段自动跳过，空正文组合仍合法 |
| 分片 | 算法不变（段落边界贪心 + 超限硬切）；原文+译文双份使文本翻倍，分片更常触发（验收样例 2 段 → 4 段），正是分片存在的意义 |
| 测试 | 模板断言加原文正文与位置关系（原文在分隔线上方）；分片计数按双份修正（甲/乙 2000→4000、字 8000→16000）；`test_exactly_at_limit_no_split` 改为 `test_below_limit_single_segment`（整条 ≤ 上限时 1 段）——26/26 保持全绿 |
| 验收脚本 | structure 检查加 `body_text` 与「原文在分隔线上方」断言；split 检查 4 段各 ≤ 3500、段落边界、链接末段——`[ALL PASS]` |
| 文档回改 | `docs/modules/M5-formatter.md`（§3 示例 / §4 模板 / §5 说明 / §8 验收 2）、`docs/module-specs.md` §2 M5 模板、`docs/architecture-and-plan.md` §4.4 模板同步更新 |

**v2 实测**：`python -m unittest tests.test_m5_formatter` 26/26 OK；`test_m[1-5]*` 113/113 OK；`python scripts/acceptance_m5.py` ALL PASS；`python src/m5_formatter.py` CLI 输出原文+译文+链接结构正确（单段 275 字符样例）。
