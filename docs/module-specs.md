# 模块化交付规格 — 爱马仕新闻 QQ 转发机器人

> 本文件是**交接文档**：每个模块 = 一个可独立开发/测试/交接给线程的单元。
> 所有线程必须严格遵守第 1 节的数据契约，避免接口漂移导致集成返工。
> 状态：v1（2026-08 探针结论已固化）

---

## 0. 总体数据流与依赖图

```
M1 列表抓取 ──NewsItem[]──► M3 增量检测 ──新增NewsItem[]──► M2 详情解析 ──NewsDetail──► M4 翻译 ──TranslationResult──► M5 组装 ──PushMessage──► M6 推送 ──► QQ群
                                │                                                                                         ▲
                                └────────────── M8 状态库(SQLite) ◄────────────────────────────────────────────────────────┘
```

```mermaid
flowchart LR
  M1[M1 列表抓取] -->|NewsItem[]| M3[M3 增量检测]
  M3 -->|新增NewsItem[]| M2[M2 详情解析]
  M2 -->|NewsDetail| M4[M4 翻译]
  M4 -->|TranslationResult| M5[M5 消息组装]
  M5 -->|PushMessage| M6[M6 QQ推送]
  M6 --> G[QQ群]
  M3 <--> DB[(M8 SQLite)]
  M6 -. 回写已推送 .-> DB
  M7[M7 主控/调度] -. 串联调度 .-> M1
```

**依赖关系**：
- `M3 依赖 M1 的输出类型`；`M2 依赖 M1 给出的 URL`；`M4/M5/M6 依赖上游契约`。
- 只要契约（§1）冻结，**M2/M4/M5/M6 可与 M1 并行开发**（各自用 mock 输入自测）。
- 唯一阻塞点：**M1 的数据源识别**（见 §2 已知信息），需先行。

---

## 1. 数据契约（冻结，所有线程照此实现）

> **契约类型的共享定义在 [`src/models.py`](../src/models.py)**（NewsItem / NewsDetail / TranslationResult / PushMessage / PushResult）。
> 各模块实现时直接 `from models import ...`，**禁止在模块内重复定义同名 dataclass**（M1 已改为复用并 re-export，M2+ 请同样处理），避免接口漂移导致 `==` / `isinstance` 失效。
> 本文件 §1 的 JSON 结构仍是契约正文；`models.py` 只是其 Python 落地，字段增删必须回改本文件。

### 1.1 NewsItem（列表条目，M1 输出 / M3 输入）

```json
{
  "id": "01_17821",
  "url": "https://idolmaster-official.jp/news/01_17821",
  "title": "【イベント】…",
  "date": "2026-08-06",
  "thumbnail": "https://idolmaster-official.jp/...jpg"
}
```
- `id`：从 URL 末段提取（如 `01_17821`），**全局唯一键**，用于去重。
- `date`：`YYYY-MM-DD` 字符串；`thumbnail` 可为 `null`。

### 1.2 NewsDetail（详情，M2 输出 / M4 输入）

```json
{
  "id": "01_17821",
  "url": "https://idolmaster-official.jp/news/01_17821",
  "title": "【イベント】…",
  "date": "2026-08-06",
  "body_text": "第一段…\n\n第二段…",
  "images": ["https://...jpg", "https://...png"]
}
```
- `body_text`：纯文本，**保留段落换行**（`\n\n` 分隔），供翻译。
- `images`：正文配图 URL 数组，可为空 `[]`；建议上限 4 张。
- 注（2026-08-26 M2 探针固化）：本站配图**直连 `idolmaster-official.jp` 同路径 404**，`images` 的 URL 一律采用 CMS 接口形态 `https://cmsapi-frontend.idolmaster-official.jp/sitern/api/idolmaster/Image/get?path=<相对路径>`（200 image/jpeg，与 §1.1 thumbnail 同法）。

### 1.3 TranslationResult（翻译，M4 输出 / M5 输入）

```json
{
  "title_zh": "【活动】…",
  "body_zh": "第一段译文…\n\n第二段译文…"
}
```

### 1.4 PushMessage（组装，M5 输出 / M6 输入）

```json
{
  "group_ids": ["123456789", "987654321"],
  "segments": [
    "【NEWS】2026-08-06\n【イベント】原标题\n\n——— 中文翻译 ———\n【活动】标题译文\n正文译文…",
    "（超长正文的后续分片）…"
  ],
  "images": ["https://...jpg"],
  "link": "https://idolmaster-official.jp/news/01_17821"
}
```
- `segments`：已按 QQ 单条消息长度分好片的文本段，M6 顺序发送。
- `images`：可选，转 OneBot `[CQ:image,file=...]` 由 M6 处理。

### 1.5 PushResult（推送结果，M6 输出 / M8 记录）

```json
{ "group_id": "123456789", "ok": true, "message_id": "123", "error": null }
```

---

## 2. 模块规格

### M1 列表抓取（Fetcher）★ 先行，架构唯一不确定点
- **职责**：拿到「最新新闻列表」，输出 `NewsItem[]`（最新在前）。
- **输入**：无（配置轮询间隔）；**输出**：`NewsItem[]`。
- **可选筛选（2026-08 追加，向后兼容）**：`fetch_news_list(limit=20, brands=None)` 支持分企划白名单——官方 7 个 brand code（IDOLMASTER/CINDERELLAGIRLS/MILLIONLIVE/SIDEM/SHINYCOLORS/GAKUEN/OTHER，见 `m1_fetcher.BRAND_CODES`）；「任一 brand ∈ 白名单即保留」；服务端 data.brand 不真正过滤，须客户端筛。M7/M8 可从 config.yaml 读 `brands` 传入。
- **可选 API 基址（2026-08 追加，向后兼容）**：`fetch_news_list(limit=20, brands=None, api_base=None)`——`api_base` 覆盖 CMS API 基址（含尾部斜杠），缺省 `CMS_API_BASE`；M7 从 config.yaml 的 `orchestrator.api_base` 显式传入（站点调整/换源时改配置即可，无需改代码）。
- **可选时间截断（2026-08 追加，向后兼容）**：`fetch_news_list(limit=20, brands=None, api_base=None, min_updated=None)`——只保留 `updated`（缺失回退 `startdate`）>= `min_updated`（Unix 秒）的条目；M7 用它实现「只推送启动时间之后更新的新闻」。
- **已知信息（2026-08 探针结论）**：
  - 页面是 Next.js SPA，`https://idolmaster-official.jp/news` 的 `__NEXT_DATA__` 仅 178B，**列表不在服务端渲染**，由前端 JS 拉取。
  - 无 RSS（`/news/feed` 404）、无 sitemap（`/sitemap.xml` 404）。
  - 列表组件 `s.w(category:"NEWS")` 在 chunk `6223/3391/9780` 等，实际数据接口待定位。
- **两条实现路径**（线程先试 A，失败再 B）：
  - **A. 直连 JSON API**：抓前端 chunk 里的请求地址（可能形如 `/api/news` 或第三方 CMS），直接 `httpx` 调 JSON，最轻最稳。定位方法：下载 `_next/static/chunks/*.js` 搜 `fetch`/URL，或用浏览器 DevTools 看 Network。
  - **B. 无头渲染**：Playwright 渲染列表页，等列表渲染后从 DOM 提取。稳但重（需 Chromium）。
- **验收**：脚本能打印最近 10 条 `NewsItem`（标题/URL/日期正确）。
- **可并行**：否（阻塞 M2 的真实输入，但 M2 可用 mock 先行）。

### M2 详情解析（DetailParser）
- **职责**：给定 URL 抓详情页，提取标题/日期/正文/配图，输出 `NewsDetail`。
- **输入**：`NewsItem`；**输出**：`NewsDetail`。
- **已知信息**：详情页 `__NEXT_DATA__` 有内容（6686B），**可直接从 JSON 解析，无需浏览器**。正文可能含 HTML，需转纯文本保留换行。
- **验收**：给定 3 个真实详情 URL，输出正确 `NewsDetail`。
- **可并行**：是（用 mock `NewsItem` 自测）。

### M3 增量检测 + 状态库（StateStore）
- **职责**：维护 `seen_items` 表，对比出「新增」条目，推送成功后回写。
- **输入**：`NewsItem[]`；**输出**：新增 `NewsItem[]`。
- **表结构**：`seen_items(id TEXT PRIMARY KEY, url TEXT, title TEXT, date TEXT, first_seen_at TEXT, pushed_at TEXT)`；另有 `push_log(id, group_id, ok, message_id, error, ts)`。
- **幂等规则**：`id NOT IN seen_items` → 新增；推送成功才写 `pushed_at`（事务）。
- **验收**：重复喂同一批列表，第二次无新增；喂新条目，只返回新条目。
- **可并行**：是。

### M4 翻译（Translator，DeepSeek）
- **职责**：日→中翻译，输出 `TranslationResult`。
- **输入**：`NewsDetail`；**输出**：`TranslationResult`。
- **要点**：`system prompt` 固化「专业日译中、保留格式、アイマス 专名术语规范」；可选术语表 JSON（如 アイドルマスター→偶像大师）；输出为结构化 JSON（title_zh/body_zh），失败重试 2 次。
- **验收**：给定 1 条真实正文，译文通顺且格式对齐。
- **可并行**：是（mock `NewsDetail`）。

### M5 消息组装（Formatter）
- **职责**：`NewsDetail + TranslationResult → PushMessage`（含分片、链接、图片）。
- **模板**（2026-08-26 v2：原文与译文均拼接）：
  ```
  【NEWS】<date>
  <原标题>

  <原文正文>

  ——— 中文翻译 ———
  <标题译文>
  <正文译文>

  🔗 原文：<url>
  ```
- **要点**：单段文本超 ~3500 字分片；图片附在首段后或单发。
- **验收**：给定样例输入，输出符合 §1.4 的 `PushMessage`。
- **可并行**：是。

### M6 QQ 推送（Notifier，NapCatQQ / OneBot 11）
- **职责**：把 `PushMessage` 推送到配置的多个群，返回 `PushResult[]`。
- **要点**：OneBot 11 HTTP/反向 WS；`send_group_msg`；群间加小间隔；单群失败不阻断其他群；图片转 `[CQ:image]`。
- **前置**：本机装 NTQQ + NapCatQQ，bot 小号已登录（运维任务，见 M9）。
- **验收**：测试群收到「原文+译文+链接」。
- **可并行**：是（用 mock `PushMessage`）。

### M7 主控 / 调度（Orchestrator）
- **职责**：APScheduler 定时（默认 5 分钟）串联 M1→M3→M2→M4→M5→M6；统一日志、异常捕获、失败告警（可选推管理员）。
- **要点**：`main.py` 常驻单进程；每轮跑完 sleep 到下一周期；异常不退出、记录日志。
- **验收**：整链路跑通，24h 无重复/漏报。
- **可并行**：否（最后集成）。

### M8 配置与密钥（Config）
- **职责**：`config.yaml`（群号、轮询间隔、术语表路径、图片开关）+ `.env`（DeepSeek key、OneBot 地址/token），均**不进 git**。
- **可并行**：是。

### M9 打包与迁移（Ops）
- **职责**：WinSW 把 Python 封装为 Windows 服务（开机自启）；NapCatQQ 在服务器首次交互登录后常驻；状态库随迁备份。
- **可并行**：是（P6 阶段做）。

---

## 3. 并行开发与集成顺序

| 阶段 | 线程分工 | 说明 |
|---|---|---|
| 先行 | **M1**（定位数据源） | 唯一阻塞点，先派一个线程 |
| 并行 | M2 / M3 / M4 / M5 / M6 / M8 | 契约冻结后各自 mock 开发 |
| 集成 | M7 | 等 M1–M6 单测通过后串联 |
| 收尾 | M9 | 稳定后打包迁移 |

## 4. 交接给线程的提示词模板

> 「读 `docs/module-specs.md` 第 1 节契约和第 2 节【模块 X】。实现 `src/<module>.py`，输入/输出严格按契约。自测：<该模块验收标准>。不要改动其他模块的契约。」

约定目录：`src/` 下每模块一个文件，`tests/` 下对应单测，契约变更必须回改本文档 §1。
