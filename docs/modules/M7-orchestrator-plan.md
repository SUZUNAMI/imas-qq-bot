# M7 主控/调度 + 后台启动脚本 — 实施计划

> 状态：已确认并实施（2026-08-26）；实施中新增需求「本地留档」已并入（见 §3.4）
> 依据：`docs/module-specs.md` §2 M7、§0 数据流；M1–M6 已完成并通过验收。

## 1. 目标

把 M1→M3→M2→M4→M5→M6 串联成常驻调度进程；提供一个脚本一键「后台挂载」启动，
**每次启动时交互询问本次要监听哪些企划（brand）的新闻**，随后按固定间隔轮询推送。

## 2. 交付物

| 文件 | 说明 |
|---|---|
| `src/main.py` | M7 主控（常驻单进程，最终入口） |
| `scripts/start_bot.cmd` | 后台挂载启动脚本（新开独立 cmd 窗口运行 main.py，脚本本身立即返回） |
| `scripts/stop_bot.cmd` | 按窗口标题结束 M7 进程（Ctrl+C 之外的备用停止手段） |
| `docs/modules/M7-orchestrator.md` | M7 交接文档（实现/用法/验收） |
| `docs/modules/M7-orchestrator-worklog.md` | 工作日志 |
| `docs/index.md` / `agent.md` | 按全局约定同步索引 |

## 3. 设计要点

### 3.1 启动交互（每次启动必问）

- 列出官方 7 个企划 code（`m1_fetcher.BRAND_CODES`）：IDOLMASTER / CINDERELLAGIRLS /
  MILLIONLIVE / SIDEM / SHINYCOLORS / GAKUEN / OTHER。
- 编号多选（如 `1,5,6`），`0` 或「全部」= 不过滤；输入非法则重新询问。
- 上次选择持久化到 `data/m7_brands.json`，启动时显示为默认，直接回车沿用。
- 非交互路径：`--brands SHINYCOLORS,GAKUEN` 跳过询问（供 M9 服务化/定时任务用）。

### 3.2 调度主循环

- 默认每 300 秒一轮（`--interval` 可改）；每轮：
  1. `fetch_news_list(limit, brands)` 抓取（带企划白名单）；
  2. `get_new_items()` 增量检测（无新增则本轮结束）；
  3. 逐条 `parse_detail → translate → format_message → push`；
  4. 推送成功 `mark_pushed`，逐群 `record_push_result`；
  5. 顺带 `get_unpushed()` 补救之前推送失败的条目（M3 规格 §6 约定由 M7 承担）。
- 异常不退出：单条新闻失败记日志继续；整轮抓取失败记日志等下一周期。
- 统一 logging：控制台 + `data/logs/m7.log`（rotating）；Windows 控制台强制 UTF-8。
- `--once` 跑一轮即退出（验收用）；`--dry-run` 只组装不推送。

### 3.3 后台挂载

- `start_bot.cmd`：`chcp 65001` → `start "M7 Bot" cmd /k "cd /d %~dp0.. && python src\main.py"`
  —— 新开独立 cmd 窗口挂后台，启动脚本立即返回；窗口内完成企划询问并持续输出日志，
  关闭窗口或 Ctrl+C 即停止。
- `stop_bot.cmd`：`taskkill /FI "WINDOWTITLE eq M7 Bot*"`（按标题杀，不影响其他进程）。

### 3.4 本地留档（实施中新增需求）

- 每次抓到新闻即留档（与推送解耦，翻译失败也留原文+图片）；
- `data/archive/<新闻日期 YYYY-MM-DD>/<news_id>/`：原文.md + 译文.md + meta.json + images/；
- 文本覆写幂等（译文失败重试成功后补上）、图片只补缺；`--no-archive` 关闭。

## 4. 待确认项（已确认）

1. 启动形态：**新开独立 cmd 窗口**（确认）。
2. 记住上次企划选择作为默认（回车沿用）：**是**（确认）。
3. 其余按 §3 默认执行（轮询 300s、失败补救、--once/--dry-run、stop 脚本）。

## 5. 验收

- `start_bot.cmd` 一键拉起独立窗口，窗口内完成企划多选，日志滚动输出；
- 造一条新新闻（或临时 `--interval 60`）→ 群内收到推送；
- 重启进程重复询问、无重复推送（state.db 幂等）；
- 全部模块单测仍 146/146 通过。
