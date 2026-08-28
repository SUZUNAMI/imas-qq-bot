# 项目文档索引

> 项目：爱马仕官方新闻 QQ 转发机器人（追踪 https://idolmaster-official.jp/news，推送「原文 + AI 日译中」到 QQ 群）
> 技术栈：Python 3.11+ · NapCatQQ(OneBot 11) · DeepSeek · SQLite
> **维护约定**：本索引由所有线程共同维护——创建/修改文件后必须同步更新本文件对应章节。
> 最近更新：2026-08-27（**S7 总交付完成（2026-08-27）：启动/停止双群状态通知（`songbot.notify_groups`，主群+测试群）+ 优雅停止文件机制（`songbot.stop_file`，`stop_songbot.cmd` 写入触发）+ `scripts/start_songbot.cmd` / `stop_songbot.cmd` 后台挂载（独立窗口 `SongBot`，与 M7 独立运行独立启动）+ 全仓 **323/323** 单测全绿（danger-full-access 下 S4 沙箱 4 项亦通过）+ 离线冒烟验证（dry-run 双群启停通知打印通过）+ 合并回主仓库（songbot/tests/scripts/fixtures/vendor/docs/data/config）；用户追加要求：日后迁移服务器（与 M9 一致）、主群测试需用户同意、与主新闻模块分别运行分别启动**；§0 新增 QQ 群 live 测试约定：默认只发测试群，主群测试需用户允许；测试线程复验：S1–S5 全量 170/170 单测 + S1→S5 链路通过，S4 渲染在受限沙箱下需脱离（playwright 子进程管道）；新增 §6「群内使用说明：Q 群输入格式」songbot-usage.md；S4 渲染接入原网页颜色数据：scripts/refresh_site_colors.py + data/songbot_site_colors.json；S2/S4 增加**演者应援色**（idol-name data 属性 → 色，契约新增 performer_colors；**character 角色个人色 348 优先于 group/attr/brand**，S1–S5 全量 176/176；render_setlist 输出文件名带标题 slug 防覆盖；**S9 绑定别名+手动刷新完成（2026-08-27）：强制前缀命令 `live`/`binding`/`unbind`/`bindings`/`update live` + `songbot/s9_binding.py`（BindingStore 线程安全 + JSON 持久化）+ `split_command` 分流 + `refresh_all`（事件索引重建 + S8 歌曲索引钩子）+ **管理命令权限控制（`binding`/`unbind`/`bindings`/`update live` 仅群主/管理员，`sender.role` → `Incoming.role`）**；S9 单测 37 项新增、S6 离线全链路 ALL PASS、全仓 266 项 262 通过（仅余 S4 沙箱既有 4 项）**；**S8 歌曲反查完成（2026-08-27）：`songbot/s8_song_index.py`（歌曲反向索引：build/增量刷新「首个已收录即停止」/save-load 缓存/match_songs 复用 s3 打分）+ `models_song` 契约扩展 `Appearance`/`SongEntry` + bot `song` 分支两段交互（选歌→列 LIVE→选序号→出图）+ 启动后台建索引（构建中提示）+ 查询前增量刷新 + `update live` 歌曲索引钩子；S8 单测 20 项 + S6 增 14 项 song 流程、全仓非 S4 259/259 通过，acceptance_song 离线含 S8 检查 ALL PASS，探针 `probe_song_index.py` 离线验证「Dance in the Light → 2 场 LIVE」）；**S10 完成（2026-08-27）：列表图片渲染 + @only 门控**——`songbot/s4_render.py` 泛化出共享管线 `render_html_pages` + `build_list_html` + `render_list`（候选/子列表/时间筛选/歌曲出现/bindings 六类列表 + 会话内重列统一发图，footer「回复序号」，时间/候选列表**全部事件进图不截断**）；`songbot/bot.py` `_handle` **@only 门控**（未 @bot 一律忽略，二次确认与 quit 也要求 @bot）+ `_send_list` 失败回退纯文本 + 可注入 `list_renderer`；`tests/test_s4_render.py` 33/33、`tests/test_s6_bot.py` 105/105、全仓 **361/361 单测全绿**；`acceptance_song.py` 离线全链路 ALL PASS（mock + **真实 Edge 双模式**）；`docs/songbot-usage.md` 更新「每轮都需 @bot + 列表为图片」；**追加修复（live 反馈）**：列表分页改先量高再分页，消除长标题超高 PNG 导致的发送慢；详见 `docs/modules/S10-list-image-atbot-worklog.md`；**S11 早期演者颜色兼容修复完成（2026-08-27）：S2 演者 span 泛化（`idol-name` **或** 早期 `idol_*` 类名）+ `refresh_site_colors.py` 增 `extract_idol_class_colors`（块级解析 `.idol_*{color:…}`，去 `!important`）→ `data/songbot_site_colors.json` 新键 `idol_class_colors`（**174 条**，sc/ml/gk/765AS/SideM/CG 全覆盖）；早期名字取 title 去 `(CV:…)`（角色名）、单元名取 span 文本；S2 单测 46→**55**（+9 早期版式用例）、S1/S3/S5/S6/S8/S9 + S4 纯函数全绿、新抓三份早期 fixture（MUGEN BEAT day1/day2、SETSUNA BEAT day1）、**MUGEN day1 真实渲染 PNG 像素级 16/16 应援色命中**（s4_render 无需改动，下划线色带自动生效）；**追加修复 1（S11 §6，2026-08-27）：`character_colors` 被 `color-overwrite` 规则污染**——站点对 `data-character-id` 有「纯个人色」与「`color-overwrite-group` 组合色覆盖」两套规则，`_extract_rule_colors` 按后定义覆盖把组合色收进个人色表（实测污染 **77 条**，如 344 小宮果穂 `#fa8333`→正确 `#e5461c`）；修复为**跳过含 `color-overwrite` 的选择器** + 重跑 `refresh_site_colors.py` 重新生成 JSON（344 恢复，其余表不变）；**追加修复 2（契约同步，2026-08-27）：`src/models.py` PushMessage 缺 `ats` 字段**——M 模块测试先 import 缓存旧契约导致 songbot `PushMessage(ats=…)` 5 项测试 TypeError；已补 `ats` 字段 + `src/m6_notifier.py` 同步 ats 支持（普通/合并转发拼 at 段）+ `_MESSAGE_FIELDS` 修正（ats 为可选字段不进必填校验，src/ref 对齐）；全量 **566/566 单测全绿**（主仓库根 cwd）**）

---

## 1. 入口文档（先读这些）

| 文档 | 说明 |
|---|---|
| [architecture-and-plan.md](architecture-and-plan.md) | 总体架构、技术选型、分阶段实施计划、风险对策 |
| [module-specs.md](module-specs.md) | 模块拆分总览、**冻结数据契约（§1）**、并行顺序 |
| [../src/models.py](../src/models.py) | **冻结契约 dataclass（§1.1–§1.5）的单一事实源**，所有模块统一 import |
| [../agent.md](../agent.md) | 全局约定（含索引维护规则、**QQ 群 live 测试约定**：默认只发测试群 666/827029417，主群测试需用户允许）+ M9 线程职责 |
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
| M9 打包迁移 | [../agent.md](../agent.md) §1 | ⏳ 未开始 |

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
| [../src/models.py](../src/models.py) | 共享 | **冻结契约类型（§1.1–§1.5）单一事实源**：NewsItem / NewsDetail / TranslationResult / PushMessage / PushResult（2026-08-27 songbot 追加 `PushMessage.ats`：@ 归属 QQ 列表，默认空、向后兼容；M6 拼独立 at 段） |
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
> 决策：独立子目录 `songbot/`、无头浏览器（Edge）高保真截图、OneBot HTTP POST 事件接收、独立进程；新增「按年/月筛选 LIVE」入口（2026-08-27，契约扩展 `Event.date`）；命令式入口 `live`/`song` + S8 歌曲反查（2026-08-27）；S9 绑定别名 + 手动刷新（`binding`/`unbind`/`bindings`/`update live`，2026-08-27）；**S7 总交付完成（2026-08-27）**：启动/停止双群状态通知、优雅停止文件、`start_songbot.cmd`/`stop_songbot.cmd` 后台挂载、合并回主仓库——与主新闻模块（M7）**分别运行分别启动**（用户要求 4 条：①日后迁移服务器与 M9 一致 ②启停双群通知 ③主群测试需用户同意 ④独立运行独立启动）；追加 S10（2026-08-27 **已完成**）：列表图片渲染（S4 `render_list`）+ @only 门控，见 `docs/S10-list-image-atbot-plan.md` / `docs/modules/S10-list-image-atbot-worklog.md`；**S11 早期演者颜色兼容修复完成（2026-08-27）**：S2 识别早期 `idol_*` 类名演者 + 颜色提取 `.idol_*{color}` → `idol_class_colors`，见 `docs/S11-legacy-color-fix-plan.md` / `docs/modules/S11-legacy-color-fix-worklog.md`。

| 文档 | 说明 | 状态 |
|---|---|---|
| [S-songbot-plan.md](S-songbot-plan.md) | 子项目实施计划（决策/逆向结论/契约/模块 S1–S7） | ✅ S1–S5 已完成（2026-08-27：S1 28/28 + live 125 事件一致；S2 40/40 + 3 真实 URL setlist 一致；S3 50/50 + 样本查询/时间筛选离线验收通过（含契约补漏 `Event.date`）；S4 18/18 + 三 fixture 渲染 PNG 一致；S5 34/34 + 模拟 POST/会话 TTL 验收通过）；**S6 全部完成（2026-08-27：30/30 单测 + acceptance_song 离线全链路 ALL PASS（含 --real-render 真实 Edge）+ live 群内两段交互验收通过）** |
| [S1-S7-taskplan.md](S1-S7-taskplan.md) | **S1–S7 完整施工图**（逐步骤/选择器/单测/验收/产出文件） | 施工图（S1–S6 已照写完成；S3 已含 `split_command` 命令分流设计） |
| [S8-song-lookup-plan.md](S8-song-lookup-plan.md) | **S8 歌曲反查 Live 计划**（反向索引构建/增量刷新/契约 `Appearance`·`SongEntry`/两段式验收） | ✅ 已完成（2026-08-27：`s8_song_index.py` + bot `song` 分支 + 21/21 单测 + acceptance_song 离线 ALL PASS（含 S8 检查）+ 探针离线验证 + **测试群 live 验收通过**（真实索引 2264 首歌，`song` 两段式出图正常）） |
| [S9-bindings-update-plan.md](S9-bindings-update-plan.md) | **S9 绑定别名 + 手动刷新计划**（`binding`/`unbind`/`bindings`/`update live`；`BindingStore` + 持久化 + `refresh_all`；**管理命令仅群主/管理员可用**） | ✅ 已完成（2026-08-27：强制前缀命令 + `s9_binding.py` + `split_command` + `refresh_all` + **权限控制**（`sender.role`）；S9 单测全过 + 离线全链路 ALL PASS） |
| [S10-list-image-atbot-plan.md](S10-list-image-atbot-plan.md) | **S10 列表图片渲染 + @only 门控计划**（`render_list` + `build_list_html` + `render_html_pages`；`_handle` 未 @ 忽略） | ✅ 已完成（2026-08-27：S4 33/33、S6 105/105、全仓 361/361；`acceptance_song.py` 离线全链路 ALL PASS（mock + 真实 Edge 双模式）；**追加修复：列表量高分页（长标题发送慢）**；live 验收待 NapCat 常驻后执行） |
| [S11-legacy-color-fix-plan.md](S11-legacy-color-fix-plan.md) | **S11 早期演者颜色兼容修复计划**（S2 识别早期 `idol_*` 类名演者；`refresh_site_colors` 提取 `.idol_*{color}` → `idol_class_colors`） | ✅ 已完成（2026-08-27：S2 泛化 + 174 条 `idol_class_colors` + 三份早期 fixture + 9 项新单测 + MUGEN 渲染像素验收） |
| [S1-fetch-events-worklog.md](modules/S1-fetch-events-worklog.md) | S1 工作日志（实现要点 / bs4 4.15 三个坑 / 测试与验收） | ✅ 2026-08-27 |
| [S2-fetch-setlist-worklog.md](modules/S2-fetch-setlist-worklog.md) | S2 工作日志（详情页三种版式泛化解析 / 测试与验收） | ✅ 2026-08-27 |
| [S3-match-worklog.md](modules/S3-match-worklog.md) | S3 工作日志（匹配策略 / 三个误命中教训 / 时间筛选语义 / 测试与验收） | ✅ 2026-08-27 |
| [S4-render-worklog.md](modules/S4-render-worklog.md) | S4 工作日志（playwright 驱动 Edge 高保真渲染 / 分页 / CLI 兜底 / 测试与验收） | ✅ 2026-08-27 |
| [S5-receiver-worklog.md](modules/S5-receiver-worklog.md) | S5 工作日志（OneBot 事件解析 array/string 双形态 / 会话 TTL / 本地 HTTP 接收器 / 测试与验收） | ✅ 2026-08-27 |
| [S6-bot-plan.md](modules/S6-bot-plan.md) | S6 实施计划（主控串联 bot.py / 处理链 / Phase B live 验收步骤） | ✅ 全部完成（2026-08-27：Phase A + Phase B live 验收） |
| [S6-bot-worklog.md](modules/S6-bot-worklog.md) | S6 工作日志（处理链设计 / 发送层复用 M6 / 索引缓存 / 沙箱环境坑 / live 验收与两处修复 / 移交说明） | ✅ 2026-08-27（含 Phase B live 记录） |
| [S7-delivery-plan.md](modules/S7-delivery-plan.md) | **S7 收尾实施计划**（启动/停止双群通知、挂载脚本、文档同步、合并回主仓库 + 用户 4 条要求落地） | ✅ 2026-08-27 |
| [S7-delivery-worklog.md](modules/S7-delivery-worklog.md) | **S7 工作日志**（通知功能实现、挂载脚本、离线冒烟、文档同步、合并回主仓库、验收终检） | ✅ 2026-08-27 |
| [S9-bindings-update-worklog.md](modules/S9-bindings-update-worklog.md) | S9 工作日志（强制前缀改造 / BindingStore / split_command / refresh_all / 测试与验收 / S8 接入点移交说明） | ✅ 2026-08-27 |
| [S11-legacy-color-fix-worklog.md](modules/S11-legacy-color-fix-worklog.md) | **S11 工作日志**（早期 `idol_*` 版式泛化 / `idol_class_colors` 提取 / 三份早期 fixture / 测试与 PNG 像素验收 / 名字口径说明） | ✅ 2026-08-27 |
| [S10-list-image-atbot-worklog.md](modules/S10-list-image-atbot-worklog.md) | **S10 工作日志**（S4 共享管线泛化 `render_html_pages` / `build_list_html` / `render_list` / `_handle` @only 门控 / 八处列表发图点 / 测试与双模式验收） | ✅ 2026-08-27 |
| [S8-song-lookup-plan.md](modules/S8-song-lookup-plan.md) | **S8 实施计划（施工图）**（索引构建/增量刷新停止边界/单测要点/验收清单/产出文件） | ✅ 2026-08-27 |
| [S8-song-lookup-worklog.md](modules/S8-song-lookup-worklog.md) | **S8 工作日志**（契约扩展 / s8_song_index 实现要点 / match_songs 语义修正 / 与 S9 并行协调 / 测试与验收） | ✅ 2026-08-27 |
| [songbot-usage.md](songbot-usage.md) | **群内使用说明：Q 群输入格式**（@bot 两段交互 / 时间与名称查询 / 绑定与手动刷新 / 容错与限制） | ✅ 已更新（2026-08-27，S10）：**每轮回复都需 @bot** + **列表回复均为图片**（footer「回复序号」，全部事件进图不截断，失败回退纯文本） |

**songbot 代码交付物（S1–S5）**：`songbot/models_song.py`（契约 dataclass 单一事实源，含 `Event.date`、
`Track/Setlist.performer_colors` 应援色字段）、
`songbot/s1_fetch_events.py`（列表抓取+解析，`fetch_events`）、`songbot/s2_fetch_setlist.py`（详情抓取+解析，
`fetch_setlist`，请求层复用 s1；**应援色**：idol-name 的 data-brand-id/attr/group-id → 色，
group>attr>brand 优先级）、`songbot/s3_match.py`（查询判别+模糊匹配+时间筛选：`classify_query`/
`parse_time_query`/`parse_month`/`filter_by_time`/`match_events`/`match_sub`，纯函数零网络）、
`songbot/s4_render.py`（无头浏览器渲染 `render_setlist` → PNG，playwright
驱动 Edge + Edge CLI 兜底 + Pillow 裁边，长表分页；**颜色数据来自原网页**：品牌徽章色/版式色
由 `scripts/refresh_site_colors.py` 抓取 CSS 写入 `data/songbot_site_colors.json`，渲染时加载，
缺失回退内置常量；演者名按 `.idol-name` 样式加应援色下划线）、`scripts/probe_song_event.py`（S1 列表探针 + S2
`--setlist` 探针）、`scripts/fetch_s4_vendor_deps.py`（vendor 化 playwright/greenlet/pyee）、
`scripts/refresh_site_colors.py`（抓 imas-db.jp CSS 提取品牌色 `--imas-color-brand-*`、版式色
`h1#page_title`/`part-header`/hover/caption，及 **idol 应援色表**：brand_id_map（14）/attr_colors（11）/
group_colors（48）/**character_colors（348 角色个人色，块级解析兼容共享块）** /
**idol_class_colors（174 早期类名色 `.idol_*{color:…}`，去 `!important`，S11 增）**
→ `data/songbot_site_colors.json`；渲染优先级 character > group > attr > brand，同原网页 CSS 后定义覆盖）、
`tests/test_s1_fetch_events.py`（28/28）、`tests/test_s2_fetch_setlist.py`（55/55，含应援色 6 项 +
早期 `idol_*` 版式 9 项）、
`tests/test_s3_match.py`（50/50）、`tests/test_s4_render.py`（18/18，含真实浏览器渲染）、
`songbot/s5_receiver.py`（事件接收+会话：`parse_event` / `SessionStore` / `EventReceiver`，**零第三方依赖**）、
`tests/test_s5_receiver.py`（34/34）、`scripts/acceptance_s5.py`（S5 离线验收：模拟 POST + 会话 TTL）。

**songbot 代码交付物（S6 全部完成）**：`songbot/bot.py`（**主控**：`SongBot` 两段交互处理链 + `BotConfig`/
`load_bot_config`（`config.yaml` 增 `songbot:` 段）+ 回复排版纯函数 + 事件索引落盘缓存 +
复用 `ref/m6_notifier` 发送层（`push`，图片 `base64://`，`merge_forward=False`），**只发图片（标题/日期/出演/曲目在 PNG 内，live 反馈去文字）**、失败回退纯文本歌单）、
`tests/test_s6_bot.py`（30/30，全部离线，含 MOIW 别名流程）、`scripts/acceptance_song.py`（**S6 离线全链路验收 ALL PASS**；
`--real-render` 真实 Edge 亦 ALL PASS、`--live` 真实验收入口）、`data/songbot_events.json`（事件索引缓存，TTL 24h）、
`songbot/s3_match.py` 增 **MOIW 缩写别名**（`ALIASES`，live 实测补漏；S3 单测 52/52）。
**live 验收通过（2026-08-27，测试群 450599137）**：`@bot IWSF2026` → 子列表 → 回复序号 → 真实歌曲列表图片；
NapCat 事件上报用 `network.httpClients`（**postUrls 在 v4.18.19 不存在**）指向 `http://127.0.0.1:8090/event`，
接收器已支持 chunked/无 Content-Length 请求体；bot 需以完整权限运行（沙箱内 playwright 被拒）。
命令式入口改造（`live <名>` / `song <歌名>`）为后续线程范围（见工作日志 §7.6）。
fixtures：`imas_db_song_event.html`（列表页）、`imas_db_iwsf_day1.html` / `imas_db_million_13th_day1.html` /
`imas_db_cg_musical_dd.html`（详情页三版式样本）；S4 验收产物 `data/songbot_img/acceptance_20260827/`。

**songbot 代码交付物（S9 全部完成，2026-08-27）**：`songbot/s3_match.py` 增 **`split_command`**（强制前缀分流：
`live`/`binding`/`unbind`/`bindings`/`update`；`song` 由 S8 并行线程加入 `COMMANDS`）、
`songbot/s9_binding.py`（**`BindingStore`**：线程安全 set/get/remove/list/resolve + JSON 持久化
`data/songbot_bindings.json`；key=normalize 略缩、值=序列化 Event（含原样略缩展示）；`event_to_dict`/
`event_from_dict` 统一定义于此，bot.py 事件索引缓存改为复用）、
`songbot/bot.py`（**强制前缀命令集成**：`_first_stage` 改 `split_command` 分流；`live` **先查绑定**
（`_find_index_event` 映射回当前索引，绑定事件下架则忽略并提示）→ 时间查询 → 名称匹配；
`binding` 唯一命中才绑（0/多命中提示更精确）/ `unbind` / `bindings` 回执；
`update live` → **`SongBot.refresh_all()`**（强制重抓列表 → 重建事件索引 → 落盘缓存 →
`song_refresher` S8 钩子缺省跳过 → 回执「N 事件」）；`USAGE` 更新为命令前缀；
**权限控制**：`binding`/`unbind`/`bindings`/`update live` 仅群主/管理员可用
（`Incoming.role` ← `sender.role` ∈ owner/administrator，缺失/非法回退 member 收紧拒绝；`ADMIN_ROLES`/`MANAGE_COMMANDS`）、
`songbot/s5_receiver.py` 增 `Incoming.role` 解析）、
`tests/test_s9_binding.py`（16 项）、`tests/test_s3_match.py` 增 `TestSplitCommand`（9 项）、
`tests/test_s5_receiver.py` 增 `TestParseEventRole`（8 项）、
`tests/test_s6_bot.py`（全部改 `live` 前缀 + `TestS9BindingCommands` 15 项，含权限控制用例）、
`scripts/acceptance_song.py`（裸查询改 `live` 前缀，离线全链路 ALL PASS）、
`data/songbot_bindings.json`（绑定持久缓存，空 `{}` 起步）、
`docs/modules/S9-bindings-update-worklog.md`（工作日志，含 S8 接入点移交说明）。
顺带修复：`tests/test_s1_fetch_events.py` 导入顺序 bug（vendor 兜底前 import httpx）。
S9 说明：`update live` 只重建事件索引，歌曲反向索引留 `song_refresher` 钩子（S8 落地后注入，回执自动附「/ M 歌曲」）。

**songbot 代码交付物（S8 全部完成，2026-08-27）**：`songbot/s8_song_index.py`（**歌曲反向索引**：
`SongIndex`（entries=normalize(歌名)→SongEntry + source_urls + fetched_at）、`build_song_index`（全量）、
`refresh_song_index`（增量：按列表顺序扫描详情 URL，**遇到第一个已收录即停止**，仅抓新增——用户拍板：
列表页年份降序、新 LIVE 恒在顶部）、`save_song_index`/`load_song_index`（JSON 落盘
`data/songbot_song_index.json`）、`match_songs`（复用 `s3_match.normalize` + `_score_text` 打分，
阈值 60 / top 5 候选，**不静默猜**））、`songbot/models_song.py` 契约扩展 **`Appearance`**（event_title/
event_year/sub_title/date/url）与 **`SongEntry`**（title + appearances，同场 LIVE 按 URL 去重）、
`songbot/s3_match.py` **COMMANDS 加入 `song`**（`split_command` 由 S9 实现）、
`songbot/bot.py`（**`song` 分支两段交互**：`_handle_song`（唯一→列 LIVE `CTX_SONG_LIVES` /
多候选 `CTX_SONG_CANDIDATES` / 无命中）/ `_list_song_lives` / `_try_confirm` 新增两 kind /
`BotConfig.song_index_cache` / `start_song_index`（启动后台全量构建 + 缓存加载）/ `_refresh_song_index`
（每次 song 查询前增量刷新，失败沿用旧索引）/ `_song_refresher`（update live 全量重建钩子））、
`tests/test_s8_song_index.py`（20/20）、`tests/test_s3_match.py`（62/62，+song split_command 用例）、
`tests/test_s6_bot.py`（54/54，+`TestS8SongFlow` 14 项）、`scripts/probe_song_index.py`（S8 探针：
`--local` 离线/在线构建/`--cache` 加载/`--refresh` 增量 + 打印某歌出现过的 LIVE）、
`scripts/acceptance_song.py`（+S8 离线检查：song 唯一→LIVE→序号发图 / 多候选→选歌 / 无命中，ALL PASS）、
`docs/modules/S8-song-lookup-plan.md`（实施施工图）、`docs/modules/S8-song-lookup-worklog.md`（工作日志）、
`data/songbot_song_index.json`（真实索引持久缓存，live 构建后生成）。
S8 验收：非 S4 全仓 **261/261 通过**（S4 4 项沙箱 %TEMP% 环境限制）；离线全链路 + 探针离线验证通过；
**live 群内两段交互验收通过（测试群，2026-08-27）**：`@bot song` → LIVE 列表 → 序号 → 歌曲列表图片（真实渲染），详见工作日志 §6。

**songbot 代码交付物（S11 全部完成，2026-08-27）**：`songbot/s2_fetch_setlist.py`（**早期演者泛化**：
`_is_idol_span`/`_idol_spans_of`（`idol-name` **或** `idol_*` 类名）、`_legacy_idol_name`（title 去 `(CV:…)`
角色名 / 单元名取文本）、`_idol_color` 新增 `idol_class_colors` 类名兜底、`_read_idol_color_tables` 返回
6 元组）、`scripts/refresh_site_colors.py`（**`extract_idol_class_colors`**：块级解析 `.idol_*{color:…}`、
去 `!important`、逗号分隔共享块全覆盖）→ `data/songbot_site_colors.json` 新键 **`idol_class_colors`（174 条：
sc 37 / ml 76 / gk 15 / 765AS `idol_har|chi|yuk…` / `idol_961_*` / cute/cool/passion / intelli/mental/physical /
valiv 等）**、fixtures 新增早期样本 `imas_db_mugenbeat_day1.html` / `imas_db_mugenbeat_day2.html` /
`imas_db_setsunabeat_day1.html`（2022，均 0 个 `idol-name`）、
`tests/test_s2_fetch_setlist.py`（46→**55**：+`TestLegacyIdolClassLayoutD` 9 项）、
`docs/modules/S11-legacy-color-fix-worklog.md`（工作日志）。
S11 验收：S2 55/55、S1/S3/S5/S6/S8/S9 + S4 纯函数全绿（仅 S4 渲染 5 项沙箱既有限制，s4_render 未改动）；
**MUGEN BEAT day1 真实渲染 PNG 像素级 16/16 应援色命中**；渲染决策：不加「文字色保真」分支，
下划线色带自动生效（契约零变更）。

**songbot 代码交付物（S10 全部完成，2026-08-27）**：`songbot/s4_render.py`（**泛化共享管线**：
`render_html_pages(pages_html, out_dir, *, slug="page", est_heights=None)` 接受**已预分页 HTML 列表**
逐页截图（playwright 首选 / Edge CLI + Pillow 裁白边兜底，取代原 `_render_playwright_pages` /
`_render_cli_pages`）；`render_setlist` 重构为「量高（或估算）→ 分页 → `build_html` →
`render_html_pages`」**行为不变**；新增 **`build_list_html(title, rows, *, hint="回复序号")`**
（标题 + 序号行「主文本 + 副文本（弱化色）」+ footer 提示，`_LIST_CSS` 沿用 setlist 版式）与
**`render_list(title, rows, *, out_dir=None, hint="回复序号", slug=None)`**（估算行高分页，
空 rows 返回 []，文件名 `标题 slug + 内容短哈希` 防同标题覆盖））、
`songbot/bot.py`（**@only 门控**：`_handle` 首行 `if not text or not inc.at_bot: return`，
删除「有会话但无 @ → 提示」路径；`_try_confirm` 仅 at_bot 调用；**列表类回复图片化**：
新增可注入依赖 `list_renderer`（默认 `render_list`）与 `_send_list`（`render_list` → 发图带 @ 归属，
渲染/发送失败回退 `format_*` 纯文本，含 NapCat 假失败送达确认），**八处发送点**改图：
时间筛选 / 多候选（`_event_list_rows` 全量进图不截断）/ 多日子子列表 / 歌曲候选 / 歌曲出现 /
bindings / 候选内重列 / 候选歌内重列；图内 footer 统一「回复序号」）、
`tests/test_s4_render.py`（18→**32**：+`BuildListHtmlTest` 6 项 / `RenderListTest` 4 项 /
`RenderListBrowserTest` 4 项（真实渲染：单页/分页/输出目录/非空白 PNG））、
`tests/test_s6_bot.py`（91→**105**：+`TestAtOnlyGating` 6 项 / `TestListImageS10` 6 项；
二次确认用例全部改 at_bot=True；`_make_bot`/`_song_bot` 注入 mock `list_renderer` +
`bot.list_render_calls` 观测）、
`scripts/acceptance_song.py`（对齐 S10：列表断言走 `list_render_calls`、二次确认改 @bot、
HTTP 端到端两条 POST 均带 @；`--real-render` 用真实 `render_list` 出图，**双模式离线全链路 ALL PASS**）、
`docs/songbot-usage.md`（**每轮都需 @bot** + **列表为图片** + 全部事件进图 + 失败回退文本）、
`docs/modules/S10-list-image-atbot-worklog.md`（工作日志）。
S10 验收：S4 33/33、S6 105/105、**全仓 361/361 单测全绿**；`acceptance_song.py --real-render`
真实 Edge 下子列表/时间筛选/歌曲 LIVE 列表均出图 PASS；契约零变更（仅新增渲染函数与 DI 注入点）；
**追加修复（2026-08-27 live 反馈）：列表分页改「先量高再分页」**——原估算行高在长事件名换行时
低估 ~1.4x，产出上万 px 超高 PNG（2x 缩放）导致截图 + base64 上传特别慢；`_build_list_pages`
复用 `_measure_html_height` 量高、失败才回退估算（详见工作日志 §8）；
live 群内验收待 NapCat 常驻后执行（验收步骤已更新：每轮回复都需 @bot）。

**songbot 代码交付物（S7 总交付，2026-08-27）**：`songbot/bot.py` 增 **启动/结束双群状态通知**——
`BotConfig` 增 `notify_groups`（默认主群 1033148779 + 测试群 450599137）/ `stop_file` /
**`notify_startup`·`notify_shutdown`（启停通知文案模板，config 可自定义，仿 M7：占位符渲染 + 未知占位符保留）**；
纯函数 `_startup_text` / `_shutdown_text` / `_notify_groups`（逐群发送、失败仅告警）/
`_wait_for_stop`（停止文件轮询，替代裸 sleep）/ `_remove_stop_file`；启动成功与优雅停止
（Ctrl+C 或停止文件触发）时向配置群发「已启动/已停止」状态消息；dry-run 只打印——
`config.yaml` / `config.example.yaml` `songbot:` 段增 `notify_groups` / `stop_file` /
`notify_startup` / `notify_shutdown`（含占位符注释）、
`scripts/start_songbot.cmd`（后台挂载：新开独立 `SongBot` 窗口运行 `python -m songbot.bot`，
纯 ASCII + `%~dp0..` 相对路径，服务器可整体搬移；启动前清理残留停止文件）/
`scripts/stop_songbot.cmd`（写停止文件 → **按进程命令行检测**（非窗口标题，非交互会话/服务下可靠）
轮询等待 ≤40s → 超时回退 `taskkill /PID /T /F`，强制路径注明不发停止通知）/
`scripts/restore_napcat_webhook.py`（**NapCat 上报配置一键恢复**：Desktop 重启清空 httpClients 后
幂等补回 8090 上报，经 WebUI API，无需重启 NapCat、不影响 M7）、
`tests/test_s6_bot.py` 增 `TestStartStopNotices`（16 项：文案/解析/逐群容错/停止文件监听与清理/
**模板渲染与自定义**）、全仓 **323/323 单测全绿** + 离线冒烟通过
（dry-run：双群启停通知打印 + 停止文件优雅退出）+ **live 实发验收通过（仅测试群，2026-08-27）**。
**合并回主仓库完成（2026-08-27）**：`songbot/`、`tests/test_s*.py`、`scripts/`（含两个 .cmd + restore_napcat_webhook.py）、
`fixtures/`、`vendor/` 增量（playwright / pyee / greenlet）、`docs/` 子项目文档与 `docs/modules/S*-*.md`、
`data/songbot_*.json` 已复制至主仓库 `官号转发bot`；主仓库 `config.yaml` / `config.example.yaml`
追加 `songbot:` 段（含 `notify_groups` / `stop_file` / `notify_startup` / `notify_shutdown`）；主仓库 `docs/index.md` §6 同步更新。
