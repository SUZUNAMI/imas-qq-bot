# 项目文档索引

> 项目：爱马仕官方新闻 QQ 转发机器人（追踪 https://idolmaster-official.jp/news，推送「原文 + AI 日译中」到 QQ 群）
> 技术栈：Python 3.11+ · NapCatQQ(OneBot 11) · DeepSeek · SQLite
> **维护约定**：本索引由所有线程共同维护——创建/修改文件后必须同步更新本文件对应章节。
> 最近更新：2026-08-27（songbot 子项目 S1–S9 + **S7 总交付完成**并合并回主仓库：全仓 songbot 单测 **323/323** 全绿、启动/停止双群状态通知（`songbot.notify_groups`，**文案 `notify_startup`/`notify_shutdown` 可自定义**）、优雅停止文件（`songbot.stop_file`）、`scripts/start_songbot.cmd` / `stop_songbot.cmd` 后台挂载；与主新闻模块（M7）分别运行分别启动）

---

## 1. 入口文档（先读这些）

| 文档 | 说明 |
|---|---|
| [architecture-and-plan.md](architecture-and-plan.md) | 总体架构、技术选型、分阶段实施计划、风险对策 |
| [module-specs.md](module-specs.md) | 模块拆分总览、**冻结数据契约（§1）**、并行顺序 |
| [../src/models.py](../src/models.py) | **冻结契约 dataclass（§1.1–§1.5）的单一事实源**，所有模块统一 import |
| [../agent.md](../agent.md) | 全局约定（含索引维护规则）+ M9 线程职责 |
| [../requirements.txt](../requirements.txt) | Python 运行依赖清单 |

## 2. 模块交接文档（docs/modules/）

| 模块 | 文档 | 状态 |
|---|---|---|
| M1 列表抓取 | [M1-fetcher.md](modules/M1-fetcher.md) | ✅ 已完成（2026-08-26） |
| M2 详情解析 | [M2-detail-parser.md](modules/M2-detail-parser.md) | ✅ 已完成（2026-08-26） |
| M3 增量检测 | [M3-state-store.md](modules/M3-state-store.md) | ✅ 已完成（2026-08-26） |
| M4 翻译 | [M4-translator.md](modules/M4-translator.md) | ✅ 已完成（2026-08-26） |
| M5 消息组装 | [M5-formatter.md](modules/M5-formatter.md) | ✅ 已完成（2026-08-26） |
| M6 QQ 推送 | [M6-notifier.md](modules/M6-notifier.md) | ✅ 实现+单测+live 验收全部完成（2026-08-26：43/43 单测、全仓 193/193、NapCat 配置完成、真实推送 666 群成功；**合并转发** live 验收通过） |
| M7 主控调度 | [M7-orchestrator.md](modules/M7-orchestrator.md) | ✅ 已完成（2026-08-26：主控 + 企划交互 + 本地留档 + 翻译失败直发原文 + config 显式配置 + 启动时间截断防刷屏 + 合并转发推送 + 开机/关机双群通知（文案 config 可自定义）+ 后台挂载脚本，单测 43/43、全仓 205/205、dry-run 全链路验收通过） |
| M8 配置/密钥 | 未拆分，见 [module-specs.md](module-specs.md) §2 | ⏳ 未拆分 |
| M9 打包迁移 | [../agent.md](../agent.md) §1 · [M9-migration-plan.md](modules/M9-migration-plan.md) | 🔄 进行中（2026-08-27：代码已打包上传 GitHub `SUZUNAMI/imas-qq-bot`（私有），部署脚本 `setup_server.ps1` 就绪，待服务器部署） |

## 3. 工作日志（docs/modules/）

| 线程 | 日志 |
|---|---|
| M1 | [M1-fetcher-worklog.md](modules/M1-fetcher-worklog.md) |
| M2 | [M2-detail-parser-worklog.md](modules/M2-detail-parser-worklog.md) |
| M3 | [M3-state-store-worklog.md](modules/M3-state-store-worklog.md) |
| M4 | [M4-translator-worklog.md](modules/M4-translator-worklog.md) |
| M5 | [M5-formatter-worklog.md](modules/M5-formatter-worklog.md) |
| M6 | [M6-notifier-worklog.md](modules/M6-notifier-worklog.md) · [M6-napcat-setup.md](modules/M6-napcat-setup.md)（NapCat 运维/配置） |
| M7 | [M7-orchestrator-worklog.md](modules/M7-orchestrator-worklog.md) · [M7-orchestrator-plan.md](modules/M7-orchestrator-plan.md)（实施计划） |
| 测试可用性 | [pipeline-test-worklog.md](modules/pipeline-test-worklog.md)（M1–M4 管道集成测试，17/17 通过） |

## 4. 代码交付物

| 文件 | 归属 | 说明 |
|---|---|---|
| [../src/models.py](../src/models.py) | 共享 | **冻结契约类型（§1.1–§1.5）单一事实源**：NewsItem / NewsDetail / TranslationResult / PushMessage / PushResult |
| [../src/m1_fetcher.py](../src/m1_fetcher.py) | M1 | 列表抓取，入口 `fetch_news_list(limit=20, brands=None, api_base=None, min_updated=None) -> list[NewsItem]`（NewsItem 复用 models.py；api_base/min_updated 为 2026-08 追加的可选参数，供 M7 从 config 调整 API、按启动时间截断） |
| [../src/m2_parser.py](../src/m2_parser.py) | M2 | 详情解析，入口 `parse_detail(item) -> NewsDetail`（NewsDetail/NewsItem 复用 models.py；`__NEXT_DATA__` 主路径 + HTML fallback） |
| [../src/m3_store.py](../src/m3_store.py) | M3 | 增量检测 + 状态库：`init_db` / `get_new_items` / `mark_pushed` / `record_push_result` / `get_unpushed` |
| [../src/m4_translator.py](../src/m4_translator.py) | M4 | 翻译，入口 `translate(detail, *, config=None, client=None) -> TranslationResult`（NewsDetail/TranslationResult 复用 models.py） |
| [../src/m5_formatter.py](../src/m5_formatter.py) | M5 | 消息组装，入口 `format_message(detail, tr, group_ids, *, max_len=3500) -> PushMessage`（纯函数，**原文+译文均拼接**、段落边界分片、图片透传，契约全部复用 models.py） |
| [../src/m6_notifier.py](../src/m6_notifier.py) | M6 | QQ 推送，入口 `push(message, *, config=None, client=None) -> list[PushResult]`（OneBot 11 `send_group_msg`；`merge_forward: true` 时改走 `send_forward_msg` 合并为一条聊天记录，多群/群间间隔/重试/容错，契约全部复用 models.py） |
| [../src/main.py](../src/main.py) | M7 | **最终入口（主控/调度）**：每次启动交互询问监听企划（`--brands` 可跳过）、常驻轮询串联 M1→M6、本地留档（原文+译文+图片，按新闻日期分文件夹）、翻译失败直发原文并附失败说明、**默认只推启动时间之后更新的新闻（`--no-cutoff` 关闭）**、失败自动补救、**开机/关机多群状态通知（文案 config 可自定义）**、`--once`/`--dry-run`/`--no-archive`；轮询间隔（默认 60s）、新闻 API 基址、通知群与通知文案由 config.yaml `orchestrator:` 段显式配置 |
| [../scripts/start_bot.cmd](../scripts/start_bot.cmd) | M7 | 后台挂载启动：新开独立 cmd 窗口（标题 M7 Bot）运行主控，脚本立即返回 |
| [../scripts/stop_bot.cmd](../scripts/stop_bot.cmd) | M7 | 按窗口标题结束 M7 Bot（Ctrl+C 之外的后备停止） |
| [../tests/test_m1_fetcher.py](../tests/test_m1_fetcher.py) | M1 | 单测（19/19 通过，含品牌白名单筛选、api_base 覆盖、min_updated 时间截断） |
| [../tests/test_m2_parser.py](../tests/test_m2_parser.py) | M2 | 单测（30/30 通过：正文转换/提取/映射/回退/异常路径，无网络） |
| [../tests/test_m3_store.py](../tests/test_m3_store.py) | M3 | 单测（10/10 通过，覆盖 M3 规格 §7 全部验收 + 并发/持久化/边界） |
| [../tests/test_m4_translator.py](../tests/test_m4_translator.py) | M4 | 单测（34/34 通过：配置合并/重试/JSON 回退/缺 Key/输入兼容/术语表；全仓 82/82） |
| [../tests/test_m5_formatter.py](../tests/test_m5_formatter.py) | M5 | 单测（26/26 通过：§8 验收 1–5 + 段落边界/硬切/自定义上限/空正文/原文译文拼接/鸭子类型/防御性；M1–M5 合计 113/113） |
| [../tests/test_m6_notifier.py](../tests/test_m6_notifier.py) | M6 | 单测（43/43 通过：配置/归一化/发送顺序/图片合并/多群/重试/解析失败/网络不可达/单群失败不阻断/合并转发；全仓 193/193） |
| [../tests/test_m7_main.py](../tests/test_m7_main.py) | M7 | 单测（43/43 通过：企划输入解析/企划记忆/orchestrator 配置加载/单轮流水线 mock/翻译失败直发原文/失败记忆/启动截断透传与遗留抑制/开机通知与模板渲染/本地留档幂等；全仓 205/205） |
| [../scripts/probe_m1_api.py](../scripts/probe_m1_api.py) | M1 | 探针：定位 CMS API |
| [../scripts/acceptance_m1.py](../scripts/acceptance_m1.py) | M1 | M1 验收脚本 |
| [../scripts/acceptance_m2.py](../scripts/acceptance_m2.py) | M2 | M2 验收脚本（3 个真实 URL：标题/日期一致 + 正文纯文本 + 图片 200 + 异常明确） |
| [../scripts/acceptance_m4.py](../scripts/acceptance_m4.py) | M4 | M4 验收脚本（缺 Key 路径 + 有 Key 时真实翻译） |
| [../scripts/acceptance_m5.py](../scripts/acceptance_m5.py) | M5 | M5 验收脚本（结构模板/原文译文拼接/长文分片/图片透传/空正文，纯函数零网络） |
| [../scripts/acceptance_m6.py](../scripts/acceptance_m6.py) | M6 | M6 验收脚本（配置面 + dry-run 预演 + 有 NapCat 时真实推送） |
| [../scripts/pipeline_test_m1m4.py](../scripts/pipeline_test_m1m4.py) | 测试 | **M1–M4 管道集成测试**（真实数据流 M1→M3→M2→M4，含 M4 真实翻译，17/17 通过） |
| [../scripts/fetch_vendor_deps.py](../scripts/fetch_vendor_deps.py) | M1 | 沙箱内 vendor 化依赖（已追加 python-dotenv） |
| [../.env.example](../.env.example) | M4/M6/M8 | 环境变量模板（DEEPSEEK_API_KEY、NAPCAT_* 等，复制为 .env） |
| [../config.example.yaml](../config.example.yaml) | M4/M6/M8 | 配置模板（translator + napcat 参数 + 术语表，复制为 config.yaml） |
| ../scripts/*.py（其余） | M1 | 探针辅助脚本（dump_module / search_chunks 等） |

## 5. 维护约定

1. 创建/修改任何文件后，更新本索引对应章节（§2 状态、§3 日志、§4 交付物）。
2. 模块状态变更（⏳ → ✅ / ❌）必须反映在 §2 状态列。
3. 新增工作日志补进 §3；新增代码/脚本补进 §4。
4. 契约改动需回改 `module-specs.md` §1 并同步本索引。

## 6. 子项目：歌曲列表 bot（songbot）

> 基于 https://imas-db.jp/song/event，群内 `@bot` 回复 Live 名字 → 子列表 → 确认 → 歌曲列表图片。
> **状态（2026-08-27）：S1–S9 + S7 总交付全部完成**，成果已从子工作区（`子任务-曲库查询/`）合并回主仓库；
> songbot 与主新闻模块（M7）**分别运行分别启动**（独立进程 `python -m songbot.bot`、独立窗口 `SongBot`、独立挂载脚本）。
> 决策：独立子目录 `songbot/`、无头浏览器（Edge）高保真截图、OneBot HTTP POST 事件接收、独立进程；
> 按年/月筛选 LIVE（契约扩展 `Event.date`）；命令式入口 `live`/`song` + S8 歌曲反查；S9 绑定别名 + 手动刷新
> （`binding`/`unbind`/`bindings`/`update live`）；S7 启动/停止双群状态通知 + 优雅停止文件 + 后台挂载脚本。

| 文档 | 说明 | 状态 |
|---|---|---|
| [S-songbot-plan.md](S-songbot-plan.md) | 子项目实施计划（决策/逆向结论/契约/模块 S1–S7） | ✅ S1–S9 已完成（2026-08-27） |
| [S1-S7-taskplan.md](S1-S7-taskplan.md) | **S1–S7 完整施工图**（逐步骤/选择器/单测/验收/产出文件） | ✅ S1–S6 照写完成；S7 收尾亦完成 |
| [S8-song-lookup-plan.md](S8-song-lookup-plan.md) | **S8 歌曲反查 Live 计划**（反向索引/契约扩展/两段式验收） | ✅ 已完成（2026-08-27） |
| [S9-bindings-update-plan.md](S9-bindings-update-plan.md) | **S9 绑定别名 + 手动刷新计划** | ✅ 已完成（2026-08-27） |
| [songbot-usage.md](songbot-usage.md) | **群内使用说明：Q 群输入格式**（含启动/停止状态通知与停止方式 §6.5） | ✅ 2026-08-27 |
| [modules/S7-delivery-plan.md](modules/S7-delivery-plan.md) | **S7 收尾实施计划**（启动/停止通知、挂载脚本、文档同步、合并回主仓库 + 用户 4 条要求） | ✅ 2026-08-27 |
| [modules/S7-delivery-worklog.md](modules/S7-delivery-worklog.md) | **S7 工作日志**（通知功能实现/挂载脚本/离线冒烟/文档同步/合并/验收终检） | ✅ 2026-08-27 |
| [modules/](modules/) 内 S1–S6 / S8 / S9 各阶段 plan + worklog | 14 份实施计划与工作日志 | ✅ 2026-08-27 |

**代码交付物**：`songbot/`（`models_song.py` 契约单一事实源 + `s1_fetch_events` / `s2_fetch_setlist` / `s3_match` /
`s4_render` / `s5_receiver` / `s8_song_index` / `s9_binding` / `bot.py` 主控）、
`tests/test_s{1,2,3,4,5,6,8,9}_*.py`（8 份，全仓 **313/313 单测全绿**）、
`scripts/`（`probe_song_event` / `probe_song_index` / `acceptance_s5` / `acceptance_song` / `refresh_site_colors` /
`fetch_s4_vendor_deps` + **`start_songbot.cmd` / `stop_songbot.cmd` 后台挂载** + `restore_napcat_webhook.py`（NapCat 上报配置一键恢复，Desktop 重启清空 httpClients 后幂等补回））、
`fixtures/`（4 份详情页/列表页 HTML 样本）、`ref/`（M6 发送层等，songbot 依赖；
⚠️ **主仓库 `src/m6_notifier.py` / `src/models.py` 尚为旧版，需主线程按 `ref/` 同步 @归属特性**）、
`vendor/` 增量（playwright / pyee / greenlet）、`data/songbot_*.json`（事件/绑定/颜色/歌曲反向索引缓存）、
`config.yaml` / `config.example.yaml` 已追加 `songbot:` 段（含 `notify_groups` / `stop_file` / **`notify_startup`·`notify_shutdown` 启停通知文案模板（config 可自定义，仿 M7）**）。

**S7 用户要求落地**：
① **日后迁移服务器**（与 M9 一致：`python -m songbot.bot` 模块入口、`%~dp0..` 相对路径脚本、配置外置，可整体搬移/WinSW 服务化）；
② **启动/结束双群状态通知**（主群 1033148779 + 测试群 450599137，`songbot.notify_groups`；dry-run 只打印；强制 kill 不发停止通知）；
③ **主群测试需用户同意**（live 验收默认只发测试群 450599137）；
④ **与主新闻模块分别运行分别启动**（`start_songbot.cmd` 独立挂载，不并入 M7 主控/调度）。
