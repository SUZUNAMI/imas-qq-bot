# S7 总交付实施计划（songbot 收尾）

> 所属：子项目 songbot（歌曲列表 bot） · 施工图：`docs/S1-S7-taskplan.md` §S7
> 创建：2026-08-27 · 状态：⏳ 执行中（用户已确认开工并追加 4 条要求）
> 本线程职责：**S7 总交付**（S1–S6 / S8 / S9 均已完成，见 `docs/index.md` §6）

---

## 0. 用户追加要求（2026-08-27，本计划必须满足）

1. **日后计划迁移到服务器上**：与主线程 M9 迁移要求一致——挂载脚本、路径、配置需为「本地 Windows → Windows Server」迁移做好准备。
2. **bot 启动和结束时在测试和主群都发送消息**：songbot 启动成功 / 优雅停止时，向测试群 + 主群发状态通知（新增功能，本计划 §2）。
3. **任何准备在主群进行的测试需征得用户同意**：live 验收默认只验证测试群（450599137）；涉及主群（1033148779）的实发必须先征得用户明确允许。
4. **该模块与主新闻检测模块分别运行分别启动**：songbot 独立进程、独立入口（`python -m songbot.bot`）、独立挂载脚本（`start_songbot.cmd` / `stop_songbot.cmd`），不并入 M7 主控/调度。

## 1. 现状基线

| 项 | 结果 |
|---|---|
| 全仓单测 | ✅ 2026-08-27 基线 **291/291 全绿**（danger-full-access 下 S4 沙箱 4 项亦通过；此前受限环境为 262/266） |
| 工作日志 | ✅ S1–S9 九份均已存在（`docs/modules/`） |
| 缺失项 | ❌ `scripts/start_songbot.cmd` / `stop_songbot.cmd` 尚未创建；启动/结束通知功能未实现 |
| 仓库形态 | 主仓库与子工作区均**非 git 仓库**——「合并回主仓库」= 文件复制操作 |

## 2. 任务 1：启动 / 结束双群状态通知（需求 2）

### 2.1 配置（config.yaml `songbot:` 段新增）

```yaml
notify_groups: ["1033148779", "450599137"]  # 启动/结束状态通知群（主群 + 测试群）；留空 [] 关闭
stop_file: data/songbot.stop                # 优雅停止请求文件（stop_songbot.cmd 写入触发）；空串关闭
```

- `BotConfig` 新增 `notify_groups: list[str]`、`stop_file: str` 两字段；
- `notify_groups` 解析复用 `ref/m6_notifier.py::_coerce_group_ids`（YAML 子集不支持内联列表，兼容 真 list / JSON 数组字符串 / 逗号分隔）；
- 代码默认空列表/空串（**显式配置才生效**，与 M7 `notify_groups` 约定一致）。

### 2.2 新增纯函数（bot.py，便于离线单测）

| 函数 | 职责 |
|---|---|
| `_startup_text(cfg, *, port, event_count, latest_year, song_index_ready, dry_run) -> str` | 启动通知文案（端口/事件数/最新年份/歌曲索引状态/模式） |
| `_shutdown_text(started_at, stopped_at) -> str` | 停止通知文案（启动与停止时刻） |
| `_notify_groups(sender, groups, text) -> int` | 逐群发通知，返回成功数；失败仅告警不抛 |
| `_wait_for_stop(stop_file, interval=5.0) -> bool` | 主循环：检测停止文件出现则返回 True（替代裸 `sleep(3600)`，支持优雅停止） |

### 2.3 main() 流程修改

```
启动成功（receiver.start() 之后）→ _notify_groups(bot.sender, cfg.notify_groups, _startup_text(...))
主循环 → while not _wait_for_stop(cfg.stop_file): pass   # Ctrl+C 或停止文件均可退出
finally → receiver.stop() → 清理停止文件 → _notify_groups(... _shutdown_text(...))
```

- dry-run：sender 为 `_dry_run_sender`，通知只打印不真发；
- 发送失败不崩（`_send_text` 语义）；强制 kill（`taskkill /F`）无法发停止通知——文档注明，`stop_songbot.cmd` 优先走优雅路径；
- 事件索引构建失败（`return 1`）视为未启动成功，不发启动通知。

### 2.4 单测（test_s6_bot.py 新增 `TestStartStopNotices`）

文案格式 / `notify_groups` 三种形态解析 / `_notify_groups` 逐群调用与异常容错 / `_wait_for_stop` 文件触发与超时。

## 3. 任务 2：后台挂载脚本（需求 1、4）

- `scripts/start_songbot.cmd`：仿主仓库 `scripts/start_bot.cmd`（**纯 ASCII**——批处理按 OEM 代码页解析；`chcp 65001` + `PYTHONUTF8=1`；`cd /d %~dp0..` 相对路径，服务器可整体搬移）；新开独立 cmd 窗口（标题 `SongBot`）运行 `python -m songbot.bot`，转发附加参数（`--dry-run` 等）；脚本立即返回。
- `scripts/stop_songbot.cmd`：写入 `data\songbot.stop` → **按进程命令行检测**（`Get-CimInstance` 匹配
  `python -m songbot.bot`，非窗口标题——2026-08-27 实测窗口标题在非交互会话/服务下不可靠）→ 轮询等待进程
  退出（最多 ~40s）→ 仍存活则回退 `taskkill /PID /T /F`（强制路径注明：不发停止通知）。
- **服务器迁移准备（需求 1）**：入口为模块方式（`python -m songbot.bot`），日后 M9 可用 WinSW 封装为 Windows 服务（与主仓库 M7 同法）；NapCat `httpClients`（8090 事件上报）迁移要点、`config.yaml` 外置约定写入文档。

## 4. 任务 3：文档同步

- `docs/index.md` §6：S7 状态列、新增交付物（通知功能、挂载脚本）、测试数更新（291+）；
- `README.md`：S8/S9 完成态、启动/停止方式（start_songbot.cmd / stop_songbot.cmd）、通知行为；
- `songbot-usage.md`：新增「启动/结束状态通知」与「停止方式」小节；
- `docs/modules/S7-delivery-worklog.md`：本阶段工作日志（收尾时写）。

## 5. 任务 4：合并回主仓库（需求 4：独立运行）

复制到主仓库 `C:\Users\Z\Documents\官号转发bot` 对应位置：

| 源（子工作区） | 目标（主仓库） |
|---|---|
| `songbot/`（9 个模块 + `__init__.py`） | `songbot/` |
| `tests/test_s{1,2,3,4,5,6,8,9}_*.py`（8 份） | `tests/` |
| `scripts/{probe_song_event,probe_song_index,acceptance_s5,acceptance_song,refresh_site_colors,fetch_s4_vendor_deps}.py` + `start_songbot.cmd` + `stop_songbot.cmd` | `scripts/` |
| `fixtures/*.html`（4 份） | `fixtures/` |
| `vendor/` 增量：`playwright` / `pyee` / `greenlet`（+ dist-info） | `vendor/` |
| `docs/` 子项目 5 份计划/说明 + `docs/modules/S*-*.md`（14 份） | `docs/` |
| `data/songbot_events.json` / `songbot_bindings.json` / `songbot_site_colors.json` / `songbot_song_index.json` | `data/` |
| `config.yaml` / `config.example.yaml` 追加 `songbot:` 段（含 `notify_groups` / `stop_file`） | 同名文件追加 |

- 不并入 M7 `main.py` / orchestrator；songbot 保持独立进程与独立挂载脚本；
- 主仓库 `docs/index.md` 新增 §6 songbot 子项目章节（参照子工作区版）。

## 6. 验收清单（对照施工图 §S7 + 4 条需求）

- [ ] 全仓单测全绿（291 + 新增通知用例）
- [ ] `start_songbot.cmd` 可后台常驻（独立窗口 `SongBot`）；`stop_songbot.cmd` 优雅停止（发停止通知）
- [ ] 启动/停止通知：dry-run 打印双群；live 验收**测试群**实发通过；**主群实发须先征得用户同意**（需求 3）
- [ ] 两段交互 + song/binding/update 回归可复现
- [ ] 合并回主仓库后，主仓库内 `python -m songbot.bot --dry-run` 冒烟通过；主仓库 `docs/index.md` 已更新
- [ ] 迁移准备项（模块入口、相对路径脚本、配置外置说明）齐备（需求 1）
- [ ] 与主新闻模块独立启动/独立运行确认（需求 4）

## 7. 风险与处置

| 风险 | 处置 |
|---|---|
| `stop_songbot.cmd` 优雅等待超时 | 回退 `taskkill /F`（文档注明不发停止通知） |
| 通知发送时 NapCat 未就绪 | `_send_text` 容错仅告警，不阻塞启动流程 |
| 主群误发（需求 3） | 验收默认仅测试群；主群实发一律先征得用户同意 |
| 合并复制遗漏 | 按 §5 清单逐项核对，复制后主仓库冒烟测试 |
