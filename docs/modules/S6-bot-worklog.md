# S6 工作日志：主控串联（songbot/bot.py）

> 所属：子项目 songbot（歌曲列表 bot） · 施工图：`docs/S1-S7-taskplan.md` §S6
> 实施计划：`docs/modules/S6-bot-plan.md` · 日期：2026-08-27
> 状态：✅ **全部完成**（Phase A：代码 + 单测 30/30 + 离线验收 ALL PASS；Phase B：live 群内两段交互验收通过 + 两处 live 反馈修复（发图去文字 / MOIW 别名））

## 1. 目标

`bot.py` 常驻，串联 S1（事件索引）/ S2（详情抓取）/ S3（匹配+时间筛选）/
S4（图片渲染）/ S5（事件接收+会话），完成群内 `@bot` **两段交互**：
`@bot <LIVE 名/时间>` → 子列表/候选/时间列表；无 `@` 回复 `DAY1`/序号/公演名 → 抓详情 → 渲染 PNG → 发图 → 清会话。

## 2. 交付物（Phase A）

| 文件 | 说明 |
|---|---|
| `songbot/bot.py` | S6 主控：`BotConfig`/`load_bot_config`（`songbot:` 配置段）、`SongBot`（处理链，依赖全部可注入）、回复排版纯函数（`format_event_list`/`format_sub_list`/`setlist_text`）、索引落盘缓存（`data/songbot_events.json` + TTL）、`main()`（UTF-8 + `data/logs/songbot.log` 轮转日志 + CLI） |
| `tests/test_s6_bot.py` | 29 个单测（全部离线）：排版纯函数 / 第一段（多日·单页·多候选·无命中·时间筛选）/ 第二段（DAY1·序号·候选内名称·越界·会话优先·回落）/ 兜底（发图失败·渲染失败·抓取失败→文字版）/ 配置解析 / 索引缓存读写 |
| `scripts/acceptance_song.py` | 离线全链路验收（fixture 索引 + MockTransport 抓取 + 渲染（默认 mock，`--real-render` 真实 Edge）+ capture 发送 + 真实 EventReceiver HTTP 端到端）→ `[ALL PASS]`；`--live` 真实验收入口（Phase B） |
| `config.yaml` / `config.example.yaml` | 增 `songbot:` 段：port 8090 / ttl 300 / event_list_url / index_cache(+TTL) / render_dir / reply_limit 10 |
| `docs/modules/S6-bot-plan.md` | 实施计划（先写后做，备查） |
| `docs/modules/S6-bot-worklog.md` | 本文件 |

## 3. 实现要点 / 设计决策

### 3.1 处理链（`SongBot.handle`）

```
text 为空 -> 忽略
1) ctx = session.get(群, 人)
2) ctx 存在：
   - 尝试二次确认：event -> match_sub（DAY1/序号/公演名）；candidates -> 纯数字取第 N 个 / match_events 候选内
   - 确认成功 -> 执行（子列表 / 发图）并更新或清会话
   - 失败：@bot -> 回落第一段（视为新查询）；未@ -> 「没看懂」提示，保留会话
3) 无会话且 @bot -> 第一段：
   time -> parse_time_query -> filter_by_time -> 序号列表（reply_limit 截断 + 「还有 N 场」）+ 候选会话
   name -> match_events：唯一多日 -> 子列表 + event 会话；唯一单页 -> 全流程发图；
           多候选 -> 候选列表 + candidates 会话；无命中 -> 未找到 + 用法
4) 无会话且未@ -> 忽略
```

关键决策：
- **会话优先**：有会话时即使 `@bot`，文本也先走二次确认解析（防「@bot DAY1」被误判成全索引新查询）；解析失败才回落新查询。
- **发图失败回退纯文本歌单**（风险表约定）：图片发送返回 False / 抛异常 / 渲染失败 / 渲染为空 → 自动 `setlist_text` 文字版 + 告警；抓取失败给明确错误提示，进程不崩。
- 全流程先 `session.clear` 再执行（防并发重入重复发图）。

### 3.2 发送层：复用 `ref/m6_notifier.py`

- `SongBot` 默认 sender = `push(PushMessage(group_ids=[群], segments=[文本], images=["base64://"+b64]), config=replace(load_notifier_config(), merge_forward=False))`；
- 图片用 `[CQ:image,file=base64://<png_base64>]`（最通用，NapCat 支持），M6 内部拆成「文本条 + 多图条」并承担重试/容错；
- `merge_forward=False`：回复不包合并聊天记录（短回复包一层很怪）；M6 的 YAML 子集解析（`_read_config_file`）一并复用为配置解析单一事实源。

### 3.3 事件索引

- 启动 `fetch_events(event_list_url)`；可选落盘 `data/songbot_events.json`（带 `fetched_at`，TTL 默认 24h，`--no-cache` 强制重抓）；
- 重抓失败但有过期缓存 → 回退缓存并告警（比裸奔好）；
- `latest_year` 用于「7月」这类无年份时间查询兜底。

### 3.4 配置（config.yaml `songbot:` 段）

port 8090（接收器，NapCat postUrls 指向 `http://127.0.0.1:8090/event`）/ ttl_sec 300 / event_list_url / index_cache / index_cache_ttl_sec 86400 / render_dir / reply_limit 10。发送侧复用 `napcat:` 段（base_url 3000）。

## 4. 测试与验收

- 单测：`python -m unittest tests.test_s6_bot -v` → **29/29 通过**（全部离线，零网络）。
- 全仓回归：`python -m unittest discover -s tests -p "test_s*.py"` → **195/199**（4 个错误全部为 `test_s4_render` 既有 tempfile 环境性问题，与 S6 无关，S5 工作日志已记载同坑）。
- 离线验收：`python scripts/acceptance_song.py` → **`[ALL PASS]`**（7 项：名称多日子列表 / DAY1 确认发图 / 时间筛选 / 序号取候选 / 单页直发 / 无命中 / HTTP 端到端 200+发图）。
- 启动冒烟：`python -m songbot.bot --dry-run --port 0` → 真实抓取 125 事件索引 → 缓存落盘 → 接收器启动成功。

## 5. 环境侦察结论（2026-08-27）

| 项 | 结论 |
|---|---|
| NapCat | ✅ **在运行**：OneBot HTTP `127.0.0.1:3000` 200，`get_login_info` 返回 bot 1666562110（時津風）。注：`Get-NetTCPConnection` 在 DSH 沙箱内静默返回空，**不可信**，须用真实 API 探测 |
| postUrls | ❌ **未配置**：`C:\ProgramData\NapCatQQ Desktop\config\bot.json` 的 `connect.httpServers` 仅 3000（`messagePostFormat=array`），`httpClients=[]` → Phase B 经 NapCat WebUI API（6099，token 见 webui.json）追加 `postUrls: ["http://127.0.0.1:8090/event"]` |
| playwright | ⚠️ **DSH 沙箱内被拒**：Node 驱动子进程走命名管道（`asyncio` pipe）被沙箱拦截（WinError 5）。S6 离线验收默认 mock 渲染器（`--real-render` 供非沙箱）；真实渲染由 S4 验收产物 + Phase B live 覆盖 |
| 沙箱写盘 | ⚠️ `tempfile.mkdtemp` 在 Windows 建受限 ACL 目录，沙箱进程写入被拒；测试/脚本临时目录一律用 `os.makedirs` 建在工作区内（`.tmp_test/`） |

## 6. 已知项 / 后续（Phase B + S7）

- **Phase B（live 验收）**：确认 NapCat 3000 在线 → WebUI 追加 postUrls → 起 `bot.py` → 测试群（666 群 827029417）`@bot IWSF2026/13thLIVE/2026年7月` → 子列表/候选 → 回复 `DAY1`/序号 → 收到歌曲列表图片（QQ 群 live 约定：默认只发测试群）。
- **S7 收尾**：`scripts/start_songbot.cmd` / `stop_songbot.cmd` 后台挂载、合并回主仓库、文档终检。
- 会话为进程内内存态：bot 重启即清（TTL 5 分钟可接受，与 S5 一致）。
- IWSF 的子公演名是「第一公演 -YAKUDOU-」而非 DAY1（DAY1 是 13thLIVE 等事件的命名）——子列表已含序号，用户回复序号或公演名均可。

## 7. Phase B：live 群内验收（2026-08-27 完成 ✅）

### 7.1 前置配置（NapCat 侧）

- **重要勘误**：`postUrls` 字段在 NapCat v4.18.19 **不存在**（`napcat.mjs` 源码中 0 处引用，WebUI `SetConfig` 会静默剥掉未知字段）。OneBot 配置 `httpServers[0]` 上加 `postUrls` 后磁盘文件无变化，即为被剥。
- 正确机制：`network.httpClients`（UI 文案「HTTP客户端/HTTP上报服务」）——反向 HTTP 客户端，把事件 JSON POST 到 `url`（请求头带 `x-self-id`，可选 `x-signature` token 签名）。
- 已配置：`{"name":"songbot","url":"http://127.0.0.1:8090/event","enable":true,"messagePostFormat":"array","reportSelfMessage":false}`（经 NapCat WebUI API `POST /api/OB11Config/SetConfig`，6099 / token 见 `webui.json`）。
- **测试群勘误**：bot 已不在旧「666 群 827029417」（返回 `result=110 你已被移出该群`）；当前测试群为 **450599137（test）**（`get_group_list` 实测），本子工作区文档中 827029417 的引用已过时。

### 7.2 事件接收器实测补强（s5_receiver）

NapCat httpClients 上报请求体为 **chunked / 无 Content-Length**，原 handler 只按 Content-Length 读 → 读到 0 字节 → `json.loads("")` 解析失败（日志 `Expecting value: line 1 column 1 (char 0)`）。
修复：`_Handler._read_body()` 兼容三种形态——Content-Length / Transfer-Encoding: chunked / 读到 EOF（上限 10MB）。S5 单测 34/34 仍绿。

### 7.3 渲染运行权限（关键）

- DSH 沙箱内 **playwright 无法启动**（Node 驱动走命名管道被拒，WinError 5），Edge CLI 兜底又依赖 `%TEMP%`（沙箱拒绝写入）→ 两条渲染路都被挡，live 收到的是文字歌单兜底。
- 解决：bot 进程以**完整权限**（danger-full-access）运行 → playwright + Edge 真实渲染正常（离线验收 `--real-render` ALL PASS，PNG 500–660KB）。
- 部署提示：正式常驻（S7）由 `start_songbot.cmd` 在普通终端/服务上下文运行，天然无此问题。

### 7.4 live 验收结果（测试群 450599137）

| 步骤 | 结果 |
|---|---|
| `@bot IWSF2026` → 子列表（第一/第二/第三公演 + 日期） | ✅ 事件上报 → 解析 → 回复全链路通 |
| 回复 `1` → 抓详情 → 渲染 → 发图 | ✅ 真实 PNG 发出（14:47 首次验证） |
| 用户反馈 | ⚠️ 「公演名文字 + 图片」两条消息 → **已修复：只发图片**（标题/日期/出演/曲目都在 PNG 内） |
| `@bot MOIW 2025` | ❌ 首字母缩写算法推不出 MOIW（标题 `THE IDOLM@STER M@STERS OF IDOL WORLD 2025`）→ **已修复：s3_match 增加别名表** |

### 7.5 两处修复（2026-08-27）

1. **发图去文字**（`songbot/bot.py` `_full_flow`）：不再发送 `「标题」+日期场馆` 文字消息，只发 PNG（图片内已含全部信息）；图片发送失败仍回退纯文本歌单。单测/验收同步更新（S3 52 + S5 34 + S6 30 = 116/116 绿；`acceptance_song.py --real-render` ALL PASS）。
2. **MOIW 别名**（`songbot/s3_match.py`）：新增 `ALIASES = {"moiw": "M@STERS OF IDOL WORLD"}`，`match_events` 对 query 做「原文形态 + 别名展开形态」双打分（`_query_forms`）。`MOIW 2025`/`MOIW2023` → 唯一命中对应年份；裸 `MOIW` → 4 个候选（2014/2015/2023/2025）。

### 7.6 移交说明（非本线程范围）

- 用户计划把 bot 入口改为命令式（`live <LIVE名>` / 未来 `song <歌名>` 查歌出现在哪些 LIVE）——**明确不属于本线程**，由后续线程/阶段负责（届时需全量曲库索引模块）。
- 本线程交付的 S6 主控保持当前「`@bot <名/时间>` 裸输入 + 两段交互」形态，路由改造时直接改 `bot.py._first_stage` 即可，第二段会话逻辑无需动。
- NapCat httpClients 上报配置（8090）为 live 验收已配好；**Desktop 重启后可能被 Desktop 的 `bot.json` 覆盖回空**（M6 文档 §4.1 已知行为），届时需经 WebUI SetConfig 重新追加（或后续线程在 Desktop UI 中配置持久化）。
