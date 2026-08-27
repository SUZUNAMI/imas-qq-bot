# 子任务：歌曲列表 bot（songbot）

> 本目录是从主仓库 `官号转发bot` 拆分出的**子工作区**，用于独立完成「歌曲列表查询」子任务。
> **状态（2026-08-27）：S1–S6 / S8 / S9 / S7 收尾 / **S11 早期演者颜色兼容** 全部完成**
> （S2 55/55 + 非渲染全量单测全绿 + MUGEN BEAT 真实渲染像素验收；仅 S4 渲染 5 项需脱离沙箱运行），
> 成果已合并回主仓库 `官号转发bot`（见主仓库 `docs/index.md` §6）；songbot 与主新闻模块（M7）**独立进程、独立启动**。

## 任务

基于 https://imas-db.jp/song/event 的歌曲列表，实现：群内 `@bot` 回复 Live 名字 → 给出子列表（day1/day2…）→ 用户再次确认名字 → 给出该公演歌曲列表，并按**原网页格式渲染成图片**发送。

**计划文档（先读，两者配套）**：
- [`docs/S-songbot-plan.md`](docs/S-songbot-plan.md) —— 总体计划（决策、逆向结论、数据契约、模块与验收、风险对策）。
- [`docs/S1-S7-taskplan.md`](docs/S1-S7-taskplan.md) —— **S1–S7 完整施工图**（逐步骤实现、选择器、单测要点、验收清单、产出文件），开工直接照写。
- [`docs/S8-song-lookup-plan.md`](docs/S8-song-lookup-plan.md) —— **S8 歌曲反查 Live 计划**（反向索引构建/增量刷新/契约扩展/两段式验收）。
- [`docs/S9-bindings-update-plan.md`](docs/S9-bindings-update-plan.md) —— **S9 绑定别名 + 手动刷新计划**（`binding`/`unbind`/`bindings`/`update live`）。
- [`docs/S11-legacy-color-fix-plan.md`](docs/S11-legacy-color-fix-plan.md) —— **S11 早期演者颜色兼容修复计划**（S2 识别早期 `idol_*` 类名演者 + `idol_class_colors` 颜色表，已完成）。
- [`docs/modules/S7-delivery-plan.md`](docs/modules/S7-delivery-plan.md) —— **S7 收尾计划**（启动/停止双群通知、后台挂载脚本、合并回主仓库）。

## 目录说明

| 路径 | 内容 |
|---|---|
| `docs/S-songbot-plan.md` | 总体计划（决策/逆向结论/契约/S1–S7/风险） |
| `docs/S1-S7-taskplan.md` | **S1–S7 完整施工图（逐步骤/选择器/单测/验收，开工直接照写）** |
| `docs/S8-song-lookup-plan.md` | **S8 歌曲反查 Live 计划（已完成）**；实施施工图与工作日志见 `docs/modules/S8-song-lookup-plan.md` / `S8-song-lookup-worklog.md` |
| `docs/S9-bindings-update-plan.md` | **S9 绑定别名 + 手动刷新计划（已完成）**；工作日志见 `docs/modules/S9-bindings-update-worklog.md` |
| `docs/module-specs.md` | 主项目模块契约约定（§1 dataclass 契约模式参考） |
| `docs/index.md` | 主项目文档索引（维护约定参考） |
| `agent.md` | 全局约定（需求不清晰先提问、大改前先给计划、及时写文档备查） |
| `ref/` | 参考实现：`m1_fetcher.py`(httpx 抓取) / `m6_notifier.py`(OneBot 发送) / `models.py`(契约 dataclass) / `main.py`(主控/后台/日志) / `acceptance_m6.py`(OneBot 验收) |
| `fixtures/` | 抓好的 HTML 样例（`imas_db_song_event.html` 列表页、`imas_db_iwsf_day1.html` 详情页等，供离线写解析/测试；S11 增早期样本 `imas_db_mugenbeat_day1.html` 等） |
| `vendor/` | 运行时依赖（httpx / bs4 / python-dotenv / playwright / pyee / greenlet 等），已解包可直接 import |
| `songbot/` | 模块包：`models_song.py`(契约) / `s1_fetch_events.py` / `s2_fetch_setlist.py` / `s3_match.py` / `s4_render.py` / `s5_receiver.py` / **`s8_song_index.py`（S8 歌曲反查索引）** / **`s9_binding.py`（S9 绑定别名）** / **`bot.py`（S6 主控，常驻入口；S7 增启动/停止双群状态通知 + 停止文件优雅退出）** |
| `scripts/start_songbot.cmd` / `stop_songbot.cmd` | **S7 后台挂载**：新开独立 `SongBot` 窗口运行 / 优雅停止（写停止文件 → bot 发停止通知后退出，超时回退强杀）；**启动前自动检查/恢复 NapCat 8090 上报配置**（幂等，Desktop 重启清空 httpClients 时自动补回） |
| `scripts/restore_napcat_webhook.py` | **NapCat 上报配置一键恢复**：Desktop 重启清空 httpClients 后运行（幂等补回 8090 上报，无需重启 NapCat、不影响 M7） |
| `config.yaml` / `config.example.yaml` | NapCat 配置（base_url=127.0.0.1:3000、群号等）+ `songbot:` 段（接收端口 8090 / TTL / 索引缓存 / 渲染目录 / 回复上限 / 绑定文件 / 歌曲索引缓存 / **notify_groups 启动停止通知群 / stop_file 优雅停止文件 / notify_startup·notify_shutdown 启停通知文案（可自定义，占位符）**） |
| `.env.example` | 环境变量模板 |

## 运行方式（vendor 依赖）

```powershell
$env:PYTHONPATH = "$pwd\vendor"
python <脚本>.py
```

代码里也已内置 vendor 回退（参考 `ref/m1_fetcher.py` 顶部 `sys.path.insert` 写法）。

**后台挂载（S7 推荐，独立于 M7 新闻模块）**：

```bat
scripts\start_songbot.cmd            :: 新开「SongBot」窗口运行 python -m songbot.bot，脚本立即返回
scripts\stop_songbot.cmd             :: 优雅停止：写 data\songbot.stop → bot 发停止通知后退出（最长等 40s）
```

**直接运行（需 NapCat OneBot 已配 httpClients 指向本机 8090）**：

```powershell
$env:PYTHONPATH = "$pwd\vendor"
python -m songbot.bot            # 正式运行（读 config.yaml songbot: 段；启动/停止时向 notify_groups 双群发状态通知）
python -m songbot.bot --dry-run  # 预演：只打印不真实发送（含状态通知）
```

> 启动/停止通知（S7 需求）：bot 启动成功与优雅停止（Ctrl+C / stop_songbot.cmd）时，向
> `songbot.notify_groups`（默认主群 1033148779 + 测试群 450599137）发「已启动/已停止」状态消息；
> **文案可用 `songbot.notify_startup` / `notify_shutdown` 自定义**（占位符见 config 注释）；
> 强制 kill（taskkill /F）不发停止通知。详见 `docs/songbot-usage.md` §6.5。

## 关键提示（逆向结论摘要）

1. 站点为 **纯 HTTP**（`https://` 连接失败），服务端渲染 HTML，**无 JSON API**。
2. **编码坑**：`Content-Type` 无 charset，务必按**字节显式 UTF-8** 解码（`resp.content.decode('utf-8')`），不要信任默认解码。
3. 列表页 `/song/event`：125 个顶层事件，按 `<h2>YYYY年</h2>` 分组；事件分「单页」（一个 `<a>`）与「多日」（嵌套 `<ul><li>` DAY1/DAY2…）。
4. 详情页核心是 `<table class="tracklist">`：表头 No./楽曲/演者；行内歌名 + 品牌徽章、演者为 `<span class="idol-name">`。**详情页有四种版式**（IWSF 型 `div.m-2` / 13thLIVE 型 `<p>` / 音乐剧型含公演日程表+幕标题行+无序号行 / **早期版式（2022 及更早）用 `span[class^="idol_"]` 类名演者 + `.idol_*{color}` 文字色（S11 已兼容）**），S2 解析已泛化，详见 `docs/modules/S2-fetch-setlist-worklog.md` / `docs/modules/S11-legacy-color-fix-worklog.md`。
5. `@bot` 接收需给 NapCat 的 OneBot 配置追加 **`postUrls`（HTTP POST 事件上报）**，本地起 HTTP 服务接收；不动现有 3000 发送通道。
6. 图片渲染用**无头浏览器（Edge）**：系统已装 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`；可 vendor 化 Playwright wheel + `channel="msedge"`（免下载 Chromium）。

## 完成后

- 代码放 `songbot/` 包 + `tests/` + `scripts/`（契约见计划 §5.1，禁止重复定义同名 dataclass）。
- 每阶段完成后写工作日志，并更新 `docs/index.md`（本子工作区如无独立 index 则回主仓库后统一维护）。
- **S7（2026-08-27）已完成**：全仓 318/318 单测全绿、`start_songbot.cmd` / `stop_songbot.cmd` 后台挂载、
  启动/停止双群状态通知、文档同步；成果已合并回主仓库 `官号转发bot`（含 `songbot/`、`tests/`、`scripts/`、
  `fixtures/`、`vendor/` 增量、`docs/` 子项目文档、`data/` 缓存与主仓库 `config.yaml` 的 `songbot:` 段）。
