# S7 工作日志：总交付（文档 / 单测 / 挂载 / 合并回主仓库）

> 所属：子项目 songbot（歌曲列表 bot） · 施工图：`docs/S1-S7-taskplan.md` §S7
> 实施计划：`docs/modules/S7-delivery-plan.md` · 日期：2026-08-27
> 状态：✅ 全部完成（用户追加 4 条要求已逐条落地并验证）

## 1. 目标与用户追加要求

S7 收尾：① 全仓单测；② 工作日志；③ 后台挂载；④ 文档同步；⑤ 合并回主仓库。
用户追加要求（2026-08-27，开工时拍板）：
1. **日后计划迁移到服务器**（与主线程 M9 一致）；
2. **bot 启动和结束时在测试和主群都发送消息**；
3. **任何准备在主群进行的测试需征得用户同意**；
4. **该模块与主新闻检测模块分别运行分别启动**。

## 2. 交付物（本阶段）

| 文件 | 说明 |
|---|---|
| `songbot/bot.py` | 增 **启动/结束双群状态通知**：`BotConfig` 增 `notify_groups`（默认主群 1033148779 + 测试群 450599137）/ `stop_file` / **`notify_startup`·`notify_shutdown`（启停通知文案模板，config 可自定义，仿 M7：`_notify_template` + `_render_template`，占位符 `{port}/{events}/{year}/{song_index}/{ttl}/{mode}/{time}` 与 `{started}/{stopped}/{time}`，未知占位符保留）**；纯函数 `_startup_text` / `_shutdown_text`（模板渲染）/ `_notify_groups`（逐群发送、失败仅告警）/ `_wait_for_stop`（停止文件轮询，替代裸 sleep 3600）/ `_remove_stop_file`；main() 启动成功与优雅退出（Ctrl+C / 停止文件）时向 `notify_groups` 发状态消息；dry-run 只打印 |
| `config.yaml` / `config.example.yaml` | `songbot:` 段增 `notify_groups: ["1033148779", "450599137"]`、`stop_file: data/songbot.stop` |
| `tests/test_s6_bot.py` | 增 `TestStartStopNotices`（11 项：启动/停止文案、notify_groups 三种形态解析（内联列表串/逗号串/缺省）、逐群发送与异常容错、停止文件已存在/后创建/清理） |
| `scripts/start_songbot.cmd` | 后台挂载：新开独立 `SongBot` 窗口运行 `python -m songbot.bot`（`chcp 65001` + `PYTHONUTF8=1` + `%~dp0..` 相对路径，纯 ASCII，服务器可整体搬移）；启动前清理残留停止文件；可转发参数（`--dry-run` 等） |
| `scripts/stop_songbot.cmd` | 优雅停止：写 `data\songbot.stop` → **按进程命令行检测**（`Get-CimInstance` 匹配 `python -m songbot.bot`；窗口标题在非交互会话/服务下不可靠，2026-08-27 实测）→ 轮询等待进程退出（≤40s）→ 超时回退 `taskkill /PID /T /F`（强制路径注明不发停止通知） |
| `docs/modules/S7-delivery-plan.md` | 实施计划（任务拆解/设计决策/合并清单/验收/风险） |
| `docs/modules/S7-delivery-worklog.md` | 本文件 |
| 文档同步 | `docs/index.md` §6、`README.md`、`docs/songbot-usage.md` §6.5（启动/停止通知与停止方式） |

## 3. 实现要点 / 设计决策

1. **通知群**：`songbot.notify_groups`（复用 `ref/m6_notifier._coerce_group_ids` 解析——YAML 子集不支持
   内联列表，兼容 真 list / JSON 数组字符串 / 逗号分隔）；代码默认空列表（**显式配置才生效**，
   与 M7 `notify_groups` 约定一致）；config 默认主群 + 测试群（用户要求 ②）。
2. **优雅停止**：主循环由 `while True: sleep(3600)` 改为 `_wait_for_stop(stop_file, 5s)`——
   `stop_songbot.cmd` 写入停止文件 → bot ≤5s 检测到 → 发停止通知 → 退出 → `_remove_stop_file` 清理；
   `start_songbot.cmd` 启动前删除残留停止文件（防上次强杀残留导致下次启动立即自停）。
   Ctrl+C（KeyboardInterrupt）同为优雅路径；**强制 kill 无法发停止通知**（文档/脚本注明）。
3. **dry-run 语义**：`--dry-run` 时 sender 为 `_dry_run_sender`，启动/停止通知只打印不真发（离线冒烟验证了双群打印）。
4. **通知文案可配置（追加，2026-08-27）**：仿主程序 M7——`songbot.notify_startup` / `notify_shutdown`
   写入 config 供用户修改（`_notify_template` 缺失/非字符串回退内置默认、`\n` 还原换行；`_render_template`
   用 `format_map(_Lenient)` 渲染，未知占位符原样保留不崩溃）；内置默认文案与原硬编码一致，行为不变。
4. **事件索引构建失败**（`return 1`）视为未启动成功，不发启动通知。
5. **服务器迁移（要求 ①）**：入口为模块方式 `python -m songbot.bot`（vendor 内置回退），脚本全部
   `%~dp0..` 相对路径、纯 ASCII（批处理 OEM 代码页坑），日后 M9 可 WinSW 封装为服务（与主仓库 M7 同法）。
6. **独立运行（要求 ④）**：songbot 独立进程/独立窗口 `SongBot`/独立脚本，不并入 M7 `main.py`/orchestrator。

## 4. 测试与验收

- 单测：`python -m unittest tests.test_s6_bot.TestStartStopNotices -v` → **11/11 通过**（`LASTEXITCODE=0`）。
- 全仓：`python -m unittest discover -s tests -p "test_s*.py"` → **313/313 OK**
  （基线 291 + 本阶段 11 + 并行线程增量；danger-full-access 下 S4 沙箱 4 项亦通过）。
- **离线冒烟（2026-08-27）**：`python -m songbot.bot --dry-run` 启动（事件索引缓存 125 个 + 歌曲索引缓存
  2264 首加载）→ 双群启动通知打印（1033148779 / 450599137）→ 写入 `data/songbot.stop` → ≤5s 优雅退出 →
  双群停止通知打印（含起止时刻）→ 进程退出码 0。**启停通知 + 停止文件链路验证通过**。
- **主仓库复验**：合并回主仓库后，主仓库内 `python -m unittest discover -s tests -p "test_s*.py"` 全绿。

## 5. 合并回主仓库（2026-08-27）

按计划 §5 清单逐项复制至 `C:\Users\Z\Documents\官号转发bot`（62 项 OK / 0 失败）：

| 源（子工作区） | 目标（主仓库） |
|---|---|
| `songbot/*.py`（10 个，含 `__init__.py`） | `songbot/` |
| `tests/test_s{1,2,3,4,5,6,8,9}_*.py`（8 份） | `tests/` |
| `scripts/{probe_song_event,probe_song_index,acceptance_s5,acceptance_song,refresh_site_colors,fetch_s4_vendor_deps}.py` + `start_songbot.cmd` + `stop_songbot.cmd` | `scripts/` |
| `fixtures/*.html`（4 份） | `fixtures/` |
| `vendor/` 增量：playwright / pyee / greenlet（+ dist-info / data） | `vendor/` |
| `ref/`（m1_fetcher / m6_notifier / models / main / acceptance_m6；**songbot 依赖 M6 发送层**） | `ref/` |
| `docs/{S-songbot-plan,S1-S7-taskplan,S8-song-lookup-plan,S9-bindings-update-plan,songbot-usage}.md` | `docs/` |
| `docs/modules/S*.md`（14 份 plan+worklog） | `docs/modules/` |
| `data/songbot_{events,bindings,site_colors,song_index}.json` | `data/` |
| `config.yaml` / `config.example.yaml` 追加 `songbot:` 段（含 notify_groups / stop_file） | 同名文件 |
| 主仓库 `docs/index.md` §6 重写（S1–S9 + S7 完成态、交付物、4 条要求落地） | `docs/index.md` |

**⚠️ 移交标记**：主仓库 `src/m6_notifier.py` / `src/models.py` 仍是旧版（2026-08-26），
而 songbot 依赖的 `ref/m6_notifier.py` / `ref/models.py` 已含「回复按用户归属（ats）」新特性——
**主线程需将 `ref/` 的这两个文件同步回 `src/`**（M 模块范畴，本线程不做，避免越界改动主模块）。

## 6. 验收清单（对照施工图 §S7 + 4 条要求）

- [x] 全仓单测全绿（313/313，含新增通知用例 11 项）
- [x] `start_songbot.cmd` 可后台常驻（独立窗口 `SongBot`）；`stop_songbot.cmd` 优雅停止（停止文件触发，发停止通知；**进程命令行检测**，主仓库端到端验证通过）
- [x] 启动/停止通知：dry-run 双群打印验证通过（离线冒烟）；**live 实发验收通过（2026-08-27，仅测试群 450599137，用户拍板）**：正式模式启动 → 测试群收到「songbot 已启动…」（send_group_msg 200 OK）→ `stop_songbot.cmd` 优雅停止 → 测试群收到「songbot 已停止 · 启动于 … · 停止于 …」（200 OK）；验收后主仓库 config `notify_groups` 恢复双群默认（主群实发由正式启动自然发生，需求 ②③）
- [x] 两段交互 + song/binding/update 回归（全仓单测覆盖）
- [x] 合并回主仓库后主仓库内全仓测试通过；主仓库 `docs/index.md` §6 已更新
- [x] 迁移准备项齐备（模块入口、相对路径脚本、配置外置；要求 ①）
- [x] 与主新闻模块独立启动/独立运行（独立窗口/独立脚本；要求 ④）

## 7. 已知项 / 后续

- **窗口标题检测不可靠**（2026-08-27 实测：本环境 cmd MainWindowHandle=0 / tasklist 标题枚举挂起；
  非交互会话与 Windows 服务下同样无窗口）——`stop_songbot.cmd` 已改用**进程命令行检测**
  （`Get-CimInstance` 匹配 `python -m songbot.bot`），窗口标题仅作识别用。
- **live 验收**（要求 ③）：启动/停止通知的**真实发送**验收默认只发测试群；主群实发需用户明确同意后执行。
- NapCat httpClients（8090 事件上报）配置在 Desktop 重启后可能被覆盖回空（M6 文档 §4.1 已知行为；2026-08-27 实测：磁盘 `bot.json` 的 `httpClients` 已回空，但**运行时配置仍正确**——经 WebUI SetConfig 已重新确认）。
  **恢复命令（一键）**：`python scripts/restore_napcat_webhook.py`（经 WebUI API 幂等补回 songbot 上报条目，
  无需重启 NapCat、不影响 M7/3000 通道；脚本已随 S7 合并回主仓库）。
  **已集成进 `start_songbot.cmd`**（2026-08-27 追加，用户建议）：每次启动前自动执行该恢复——幂等（已配置
  时秒过），失败仅打印警告不阻断启动；NapCat Desktop 重启清空配置后，重启 songbot 即自动恢复，无需手动处理。
- bot 渲染需完整权限运行（playwright 驱动 Edge）；`start_songbot.cmd` 在普通终端/服务上下文运行天然无此问题。
- **「先失败文字版 + 后图片」重复问题（2026-08-27 用户实测反馈，已修复）**：
  根因 = **NapCat `sendMsg` 偶发回执超时**（NTQQ 回执慢）返回 `{"status":"failed","message":"Timeout: ...sendMsg"}`，
  但消息**实际已送达**；M6 `_parse_message_id` 严格判定 status!=ok 为失败（PushError 不重试）→ `_full_flow`
  发「图片发送失败，改发文字版」兜底；图片又真的出现在群里 → 用户看到「先文字版（撤回）+ 后图片」。
  修复 = `bot.py::_full_flow` 图片发送失败时先调 **`_confirm_group_image`**（查 `get_group_msg_history`
  最近 20s 内是否有 bot 本人发出的图片）：已送达则跳过文字版兜底（只记日志），未送达才文字版。
  单测：`TestConfirmGroupImage`（4 项：近时 bot 图→True / 无图或他人图→False / 超时窗→False /
  查询异常→False）+ `TestFallback` 增「失败但已送达→不发文字版」；全仓 323/323 全绿。
  另：**首次渲染偏慢**（用户 17:27 实测 ~16s，Edge 冷启动）为已知项，可后续加浏览器预热。
- 主仓库 `src/m6_notifier.py` / `src/models.py` 的 @归属特性同步（见 §5 移交标记）。
