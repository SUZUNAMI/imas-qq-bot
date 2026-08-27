# M7 主控 / 调度（Orchestrator）— 交接规格

> 项目：爱马仕官方新闻 QQ 转发机器人。追踪 https://idolmaster-official.jp/news，新新闻发布后推送「原文 + AI 日译中」到 QQ 群。
> 本文件自包含：只读本文件即可了解 M7 的用法、交互、留档与验收。
> 前置：M1–M6 已完成并通过验收（全仓单测 146/146）；NapCatQQ 已配置（M6 验收通过）。

## 1. 本模块在流水线中的位置

```
M1 列表抓取 ──► M3 增量 ──► M2 详情 ──► M4 翻译 ──► M5 组装 ──► M6 推送 ──► QQ群
        ▲              │
        └──── M7 主控调度（本文件）串联以上全部，常驻轮询 ────┘
                            │
                            └──► 本地留档（原文+图片+译文，按新闻日期分文件夹）
```

M7 不产生新契约类型（全部复用 `src/models.py`），只负责串联与调度。

## 2. 交付物

| 文件 | 说明 |
|---|---|
| `src/main.py` | **M7 主控（最终入口）**：企划询问 + 常驻轮询 + 留档 + 推送 + 失败补救 |
| `scripts/start_bot.cmd` | 后台挂载启动：新开独立 cmd 窗口（标题 M7 Bot）运行主控，脚本立即返回 |
| `scripts/stop_bot.cmd` | 按窗口标题结束 M7 Bot（Ctrl+C 之外的后备停止手段） |
| `tests/test_m7_main.py` | 单测 21/21（企划解析/记忆/流水线 mock/留档） |
| `data/m7_brands.json` | 上次企划选择（启动默认，运行时生成，不进 git） |
| `data/archive/<日期>/<news_id>/` | 本地留档（运行时生成，不进 git） |

## 3. 启动方式

### 3.1 后台挂载（日常使用）

```
scripts\start_bot.cmd                # 新开 "M7 Bot" 窗口，窗口内选企划，日志滚动
scripts\start_bot.cmd --brands SHINYCOLORS,GAKUEN --interval 300   # 传参透传
scripts\stop_bot.cmd                 # 结束（或窗口内 Ctrl+C / 关闭窗口）
```

### 3.2 每次启动的企划询问

- 列出官方 7 个企划 code（`m1_fetcher.BRAND_CODES`）编号多选：如 `1,5,6`；`0`/`all` = 全部不过滤；输入非法重新询问。
- **上次选择**持久化于 `data/m7_brands.json`，启动时显示为默认，直接回车沿用。
- stdin 非交互（被调度器拉起）时回退上次选择/全部并告警。
- 非交互跳过：`--brands SHINYCOLORS,GAKUEN`（供 M9 服务化/定时任务）。

### 3.3 命令行参数（`python src/main.py --help`）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--brands` | 交互询问 | 企划白名单，逗号分隔，如 `SHINYCOLORS,GAKUEN` |
| `--interval` | config 的 `orchestrator.poll_interval_sec`，再缺省 300 | 轮询间隔秒数 |
| `--limit` | 20 | 每轮抓取窗口条数 |
| `--max-len` | 3500 | 单条 QQ 消息字符上限 |
| `--archive-dir` | `data/archive` | 本地留档根目录 |
| `--no-archive` | 开 | 关闭本地留档 |
| `--once` | 否 | 只跑一轮退出（验收用） |
| `--no-cutoff` | 开 | 关闭「启动时间截断」（补推启动前就存在的新闻；默认只推启动后更新的） |
| `--dry-run` | 否 | 抓取/翻译/组装/留档但不推送；用独立 `data/state_dryrun.db`，不污染正式库 |

### 3.4 显式配置（config.yaml 的 `orchestrator:` 段，2026-08-26 追加）

```yaml
orchestrator:
  poll_interval_sec: 60    # 轮询间隔（秒）；--interval 可覆盖
  api_base: https://cmsapi-frontend.idolmaster-official.jp/sitern/api/   # 新闻 CMS API 基址（含尾部斜杠）
  notify_groups: ["450599137"]   # 开机/关机状态通知群；留空 [] 关闭
```

- 轮询间隔、新闻 API 基址、状态通知群**无需改代码**即可调整（API 换源/站点调整时只改这里）。
- `api_base` 透传给 `fetch_news_list(api_base=...)`（M1 2026-08 追加的向后兼容可选参数）。
- `notify_groups`：启动/停止时给这些群发一条状态消息（开机含监听企划/间隔/模式，关机含本次轮数/推送统计），
  方便确认 bot 启停；用普通文本消息发送（临时关闭 merge_forward）。

### 3.5 启动时间截断（2026-08-26 追加，默认开启）

- **默认只推送「启动时间之后更新/发布」的新闻**：每轮 `fetch_news_list(..., min_updated=启动时刻)` 客户端截断
  （CMS `updated` 字段，缺失回退 `startdate`）——启动前就存在的旧新闻一律不推，**避免首次启动把最新一批新闻全部补推刷屏**。
- 启动时把状态库中**历史遗留的未推送条目标记为已处理**（跳过），防止补救循环（get_unpushed）补推旧新闻。
- 关闭：`--no-cutoff`（补推/特殊用途）。

### 3.6 开机/关机状态通知（2026-08-26 追加）

- 配置 `orchestrator.notify_groups` 后，**启动时**向该列表的**全部群**发「已启动」消息，
  **停止时**（Ctrl+C 或正常退出，finally 兜底）发「已停止」消息（含本次轮数/推送统计）。
- **文案可在 config.yaml 自定义**（`notify_startup` / `notify_shutdown`，`\n` 表示换行），
  支持占位符自动替换：
  - 开机：`{brands}` 监听企划 / `{interval}` 轮询秒数 / `{mode}` 模式 / `{groups}` 推送目标群 /
    `{cutoff}` 时间截断 / `{time}` 当前时间；
  - 关机：`{rounds}` 运行轮数 / `{ok}` 推送成功条数 / `{fail}` 失败条数 / `{time}` 当前时间。
  - 未知占位符原样保留不崩溃；不配置则用内置默认文案。
- 用普通文本消息发送（临时关闭 merge_forward，避免短状态也包合并记录）；通知失败仅告警不阻断。
- 群号兼容 list / JSON 数组字符串 / 逗号分隔三种形态；留空 `[]` 或删掉该行 = 关闭通知。

## 4. 主循环（每轮）

1. `fetch_news_list(limit, brands, api_base, min_updated=启动时刻)` 抓取（企划白名单 + **启动时间截断**，见 §3.5）；
2. `get_new_items()` 增量检测（无新增则本轮结束）；
3. 逐条：`parse_detail → 留档(原文+图片) → translate → 留档(译文) → format_message → push`；
   **翻译失败**：留档原文（meta 记 `translation_error`）→ **直接推送原文并附「⚠️ AI 翻译失败：<原因>（本条为原文直发）」**；
   失败原因按 news_id 进程内记忆（重启清除），后续轮次跳过重复翻译、直接直发（避免反复调用翻译 API）；
4. 推送成功 `mark_pushed`，逐群 `record_push_result`（失败留 push_log）；
5. `get_unpushed()` 补救之前推送失败的条目（含本轮刚失败的，M3 规格 §6 约定）。

**异常不退出**（规格 M7）：详情解析/组装/推送失败记日志，条目留 unpushed 下轮自动补救；整轮抓取失败记日志等下一周期；`Ctrl+C` 优雅退出。

**推送成功口径**：至少一个群成功即 `mark_pushed`（避免健康群重复推送；失败群记录在 push_log 供人工补发）。翻译失败直发原文也计入「推送成功」（消息已发出）。

## 5. 本地留档（原文 + 图片 + 译文，按日期分文件夹）

- 目录：`data/archive/<新闻日期 YYYY-MM-DD>/<news_id>/`（按**新闻发布日期**分文件夹；item.date 为空回退抓取当天）。
- 内容：
  - `原文.md`：标题/日期/链接/新闻ID + 正文（原始日文，段落保留）；
  - `译文.md`：标题译文 + 正文译文（有译文时）；
  - `meta.json`：id/url/标题/日期/留档时间/是否已翻译/`translation_error`（翻译失败原因，有则记）；
  - `images/NN.<ext>`：正文配图（最多 4 张，扩展名从 URL 猜测，兜底 .jpg）。
- 幂等：文本每次覆写；图片只下载缺失的。
- 留档与推送**解耦**：详情解析成功即留档，翻译/推送失败也留原文与图片；留档失败仅告警绝不阻断。

## 6. 配置依赖（复用 M4/M6 + 自身 orchestrator 段）

- 翻译：`config.yaml` 的 `translator:` 段 + `.env` 的 `DEEPSEEK_API_KEY`（M4 `load_config`）。
- 推送：`config.yaml` 的 `napcat:` 段（目标群/地址/间隔，M6 `load_config`）。
- 自身：`config.yaml` 的 `orchestrator:` 段（§3.4：轮询间隔 / 新闻 API 基址）。

## 7. 验收记录（2026-08-26）

- 单测：`python -m unittest discover -s tests` → **177/177 通过**（M7 28/28、M1 含 api_base 覆盖 16/16）。
- 全链路 dry-run（翻译成功路径）：`python src/main.py --once --dry-run --brands SHINYCOLORS --limit 3`
  → 真实抓取 1 条（SC 过滤）→ 增量 → 详情 → 真实 DeepSeek 翻译 → 留档
  `data/archive/2026-08-26/01_19692/`（原文+译文+图片 2/3，1 张 SSL 瞬断优雅跳过）→ 不推送。
- 全链路 dry-run（**翻译失败直发原文**）：伪造 `DEEPSEEK_API_KEY=sk-bogus` →
  401 翻译失败 → 留档原文+图片（meta `translated:false` + `translation_error`）→
  [DRY-RUN] 预览显示直发原文消息（`【NEWS】… ⚠️ AI 翻译失败…原文直发`）→ 计入推送成功。
- 交互询问：管道输入 `1,5,6` → 显示上次选择、保存 `data/m7_brands.json`、正常跑轮。
- 窗口挂载：`start "M7 Bot" cmd /c "..."` 标记文件验证通过（独立窗口执行成功）。
- config 生效：启动日志显示「间隔 60s | API https://cmsapi-frontend…/sitern/api/」来自 `orchestrator:` 段。
- **启动时间截断**（2026-08-26）：dry-run 启动日志显示「时间截断：只推启动时间(Unix …)之后更新的新闻」，
  首轮 M1 抓取 0 条（启动前已存在的新闻全部被截断，不再刷屏）；`state.db` 中历史遗留的 13 条未推送
  旧新闻在下次启动时被自动标记跳过。

## 8. 已知决策（详见 worklog）

1. 推送成功口径：任一群成功即回写（防重复推送，失败群人工补发）。
2. 留档按新闻发布日期分文件夹（非抓取日期）。
3. `--dry-run` 用独立 `state_dryrun.db`，防止吞掉正式库的增量检测。
4. 批处理脚本保持纯 ASCII（避免 cmd OEM 代码页解析中文注释出错）；CJK 输出由 Python 侧 UTF-8 + 窗口内 `chcp 65001` 处理。
5. **翻译失败直发原文**（2026-08-26 需求变更）：不再等下轮补救；失败原因进程内记忆，重启后重试翻译。
6. 轮询间隔 / 新闻 API 基址进 `config.yaml` 的 `orchestrator:` 段（2026-08-26 需求变更）；命令行参数优先于配置。
7. **启动时间截断默认开启**（2026-08-26 需求变更）：只推启动时间之后更新的新闻；旧新闻不补推；
   轮询间隔已按用户要求设为 1 分钟（config.yaml `poll_interval_sec: 60`）。
