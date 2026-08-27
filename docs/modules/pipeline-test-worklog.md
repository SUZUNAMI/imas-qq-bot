# 管道集成测试（M1–M4 可用性）— 执行工作日志

> 线程：测试可用性（本线程）；项目：爱马仕官方新闻 QQ 转发机器人
> 创建：2026-08-26；状态：✅ M1–M4 管道可用性验证通过
> 验证对象：`src/m1_fetcher.py` → `src/m3_store.py` → `src/m2_parser.py` → `src/m4_translator.py`

---

## 1. 测试计划（2026-08-26）

| 步骤 | 内容 | 验收 | 状态 |
|---|---|---|---|
| T1 环境确认 | Python 3.13.7、vendor 依赖（httpx/bs4/dotenv）可 import | 三者均可用 | ✅ |
| T2 全量单测 | `python -m unittest discover -s tests` | 全部通过 | ✅ 82/82 |
| T3 管道集成 | 真实数据流 M1 抓取 → M3 增量 → M2 详情 → M4 翻译 | 逐段断言全过 | ✅ 17/17 |
| T4 文档回写 | 本日志 + `docs/index.md` 同步 | 索引与实际一致 | ✅ |

## 2. 环境（T1）

- Python 3.13.7（`C:\Users\Z\AppData\Local\Programs\Python\Python313\python.exe`）
- vendor 依赖：httpx 0.28.1 · beautifulsoup4 4.15.0 · python-dotenv 1.2.3 均正常 import
- **`.env` 于 17:03 由用户/并行线程补入 `DEEPSEEK_API_KEY`**（测试开始前不存在），M4 真实翻译得以执行

## 3. 全量单测（T2）

`python -m unittest discover -s tests` → **Ran 82 tests, OK**（0.97s）：

| 模块 | 单测数 | 状态 |
|---|---|---|
| M1 列表抓取 | 8 | ✅ |
| M2 详情解析 | 30 | ✅ |
| M3 增量检测 | 10 | ✅ |
| M4 翻译 | 34 | ✅（**文档漂移**：index.md 记 35/35，实际文件 34 条 `def test_`，已修正） |

## 4. 管道集成测试（T3）★

交付物：`scripts/pipeline_test_m1m4.py`（可重复运行：`python scripts/pipeline_test_m1m4.py`）。
运行结果：**17 passed, 0 failed**，耗时 13.8s。

| 段 | 断言 | 结果 |
|---|---|---|
| M1 真实抓取 | 返回 5 条、最新在前（首条 `01_19666` 2026-08-26）、id=URL 末段、title 非空 | ✅ |
| M3 增量检测 | 全新库首喂 5 条 → 5 新增且顺序一致；重喂同一批 → 0 新增（幂等）；`mark_pushed` 后 `get_unpushed` 剔除该条 | ✅ |
| M2 真实详情 | `01_19666` title/date/id/url 与列表一致；body 3527 字符无 HTML 标签、段落 `\n\n` 分隔；images 3 张均为 CMS `Image/get` 形态且 ≤4 | ✅ |
| M4 缺 Key 路径 | `translate(api_key="")` 抛 `TranslationError`，message 含 `DEEPSEEK_API_KEY` 提示 | ✅ |
| M4 fake 管道 | 注入 fake client：`NewsDetail → TranslationResult`，端点归一化为 `…/chat/completions`，恰好 1 次调用 | ✅ |
| M4 真实翻译 | **真实 DeepSeek 调用成功**：标题→「【学园偶像大师】#美铃泳装扭蛋举办中…」，正文段落对齐，术语表生效 | ✅ |

关键观察：
- 管道四段数据契约（`NewsItem[]` / `NewsDetail` / `TranslationResult`）在真实数据流下衔接无漂移，`isinstance` / 字段名全部互通。
- M3 测试库落在仓库内 `.tmp/pipeline_test/run_<ts>/`（沙箱约定：系统 %TEMP% 不可写，必须工作区内），未污染 `data/` 正式库。
- 真实翻译耗时约 5s（含网络），单条新闻成本可忽略，符合架构文档预估。

## 5. 结论

- **M1–M4 管道可用性 ✅ 全部通过**：抓取 → 增量 → 详情 → 翻译真实数据流闭环成功，含一次真实 LLM 翻译。
- 遗留提示（非阻塞）：
  1. 文档漂移已修：`docs/index.md` M4 单测数 35→34（总 82/82 不变）。
  2. ~~M5/M6 尚未开发~~ → 已解决：M5 消息组装（2026-08-26）、M6 QQ 推送 + NapCat 环境 + live 真实推送验收（2026-08-26）均已完成，管道终点已延伸至 QQ 群推送，见 `docs/index.md` §2 与 `docs/modules/M6-napcat-setup.md`。
