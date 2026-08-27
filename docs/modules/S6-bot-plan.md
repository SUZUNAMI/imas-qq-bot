# S6 实施计划：主控串联 + 验收（songbot/bot.py）

> 所属：子项目 songbot（歌曲列表 bot） · 施工图：`docs/S1-S7-taskplan.md` §S6
> 创建：2026-08-27 · 状态：✅ **全部完成**（Phase A：29/29 单测 + 离线验收 ALL PASS；Phase B：live 群内两段交互验收通过 + 两处 live 反馈修复）
> 前置：S1–S5 全部完成（单测 28+40+50+18+34 全绿，契约 `models_song.py` 冻结）

---

## 1. 目标（照抄施工图 §S6）

`bot.py` 常驻，串联 S1（事件索引）/ S2（详情抓取）/ S3（匹配+时间筛选）/
S4（图片渲染）/ S5（事件接收+会话），完成群内 `@bot` **两段交互**：

1. `@bot <Live名/时间>` → 回复子列表/候选/时间列表；
2. 用户无 `@` 回复 `DAY1`/序号/公演名 → 抓详情 → 渲染 PNG → 发图 → 清会话。

验收清单（live）：测试群 `@bot <Live名>` → 收到子列表；回复 `DAY1` → 收到歌曲列表图片。

## 2. 环境侦察结论（2026-08-27，开工前确认）

| 项 | 现状（2026-08-27 复核） | 对 S6 的影响 |
|---|---|---|
| NapCat 组件 | ✅ **在运行**：OneBot HTTP `127.0.0.1:3000` 返回 200，`get_login_info` 返回 bot 1666562110（時津風）；Desktop 进程 + QQ 进程均在 | live 验收前置已具备（此前 `Get-NetTCPConnection` 在沙箱静默返回空导致的误判已更正） |
| postUrls | ❌ **未配置**：`C:\ProgramData\NapCatQQ Desktop\config\bot.json` 的 `connect.httpServers` 仅 3000（`messagePostFormat=array`），`httpClients=[]`、无 postUrls | Phase B 经 WebUI API 追加 `postUrls: ["http://127.0.0.1:8090/event"]`（改 live 配置，先征得用户同意） |
| NapCat WebUI | `C:\ProgramData\NapCatQQ Desktop\components\NapCatQQ\config\webui.json`：port 6099 / token `3941ae102452`（`/api/auth/login` 已实测可用） | postUrls 通过 WebUI API 配置（Phase B 做） |
| `.env` | 子工作区**无** `.env`（主仓库有，含 NAPCAT_* / DEEPSEEK_API_KEY） | 发送配置走本工作区 `config.yaml` 的 `napcat:` 段即可，无需 .env |
| 事件索引 | 无缓存文件 | `data/songbot_events.json` 可选落盘（新建） |

## 3. 交付物（Phase A：纯代码 + 离线验收，不碰 live 环境）

```
songbot/bot.py              # S6 主控（新增）
tests/test_s6_bot.py        # S6 单测（新增，离线）
scripts/acceptance_song.py  # S6 验收脚本（离线默认 + --live）
config.yaml / config.example.yaml   # 增 songbot: 段
docs/modules/S6-bot-worklog.md      # 工作日志（完成时写）
docs/index.md / README.md / docs/S1-S7-taskplan.md  # 进度同步
```

### 3.1 `songbot/bot.py` 设计

**模块组成**

| 部件 | 说明 |
|---|---|
| `BotConfig` dataclass + `load_bot_config()` | 读 `config.yaml` 的 `songbot:` 段（port / ttl_sec / event_list_url / index_cache / render_dir / reply_limit）；复用 `ref/m6_notifier._read_config_file`（YAML 子集解析单一事实源） |
| `SongBot` 类 | 处理链主体；依赖全部可注入（events / setlist_client / renderer / sender / session / clock），便于离线单测 |
| `format_event_list` / `format_sub_list` / `setlist_text` | 纯函数回复排版（时间列表截断、多日子列表、纯文本歌单兜底） |
| `main()` | UTF-8 输出 + 日志（`data/logs/songbot.log` RotatingFileHandler，同 M7 习惯）→ 建索引 → 起 `EventReceiver` → 常驻；CLI：`--config/--port/--no-cache/--dry-run/--index-file` |

**处理链（`handle(Incoming)`）**

```
text 为空 -> 忽略（S5 parse_event 已保证非空）
1) ctx = session.get(group,user)
2) ctx 存在：
   - 尝试二次确认解析：
       kind=event:      match_sub(text, event) 命中 -> 全流程发图 + clear；未命中 -> False
       kind=candidates: 纯数字 -> 取第 N 个候选；否则 match_events(text, 候选内)
                        命中 1 个 -> 走 _resolve_event（多日->子列表+更新会话；单页->全流程+clear）
                        命中多个 -> 更新候选列表重新列；0 -> False
   - 确认成功 -> return
   - 确认失败：@了 bot -> 落到第一段（视为新查询）；没 @ -> 回复提示，保留会话
3) ctx 不存在且 @bot -> 第一段：
   classify_query(text):
     time -> parse_time_query -> filter_by_time(索引, year, month)
             命中 0 -> 「未找到」；命中 -> 序号列表（截断 reply_limit=10 + 「还有 N 场」）
             会话记 {"kind":"candidates","events":hits}，提示「回复序号或 LIVE 名」
     name -> match_events(text, 索引)
             0 -> 未找到 + 用法提示
             1 -> _resolve_event：
                    多日 -> 「事件名 + 1..N. 子公演名(日期)」+ 会话记 event，提示「回复 DAY1 或公演名」
                    单页 -> 全流程发图 + clear
             >1 -> 候选序号列表 + 会话记 candidates，提示「回复序号或 LIVE 名」
4) ctx 不存在且未 @ -> 忽略（不打扰无关消息）
```

**全流程（`_full_flow`）**：`fetch_setlist(url, client=setlist_client)` → `renderer(setlist)`（默认 `render_setlist`，输出 `data/songbot_img/<ts>/`）→ 发送：
- 文本「标题/日期场馆/曲目数」+ 每张 PNG `[CQ:image,file=base64://<b64>]`；
- **发送层复用 `ref/m6_notifier.py`**：`push(PushMessage(group_ids=[群], segments=[文本], images=[base64://...]), config=replace(load_notifier_config(), merge_forward=False))`（merge_forward 关掉，避免回复也包成合并聊天记录；文本每条独立发、图片合并一条多图消息，重试/容错 M6 已实现）；
- **图片发送失败 → 回退纯文本歌单（`setlist_text`）并告警**（风险表约定）；抓取失败/渲染失败 → 回复错误说明，不崩。

**索引构建**：`fetch_events(event_list_url)`；可选落盘 `data/songbot_events.json`（带 fetched_at，TTL 默认 24h，`--no-cache` 强制重抓）；进程内 `latest_year = max(int(e.year))`。

### 3.2 `scripts/acceptance_song.py`（离线默认 + `--live`）

- **离线（默认，零网络/QQ）**：
  - 索引用 `fixtures/imas_db_song_event.html`（真实 S1 解析）；
  - 详情抓取用 httpx `MockTransport` 按 URL 回 `iwsf_day1/million_13th_day1` fixture；
  - 渲染 mock（返回假 PNG 路径；真实渲染属 S4 验收）；发送 capture（记录文本/图片）；
  - 场景断言：`@bot IWSF2026` → 子列表（含 DAY1/DAY2）+ 会话；无@ `DAY1` → fetch+render+发图+清会话；`@bot 2026年7月` → 时间列表（IWSF+DERE）+ 候选会话；无@ `1` → 取首个候选；`@bot 13thLIVE` → MILLION 子列表；`@bot 不存在` → 未找到；图片发送失败 → 回退纯文本歌单；
  - 再起真实 `EventReceiver` + HTTP POST 两条事件（复用 S5 形态）验证端到端。
- **`--live`**：真实索引 + 真实渲染 + 真实 NapCat 发送，常驻等待；打印操作指引（Phase B 用）。

### 3.3 `tests/test_s6_bot.py`（新增单测，离线）

覆盖：format 纯函数（截断/子列表/纯文本歌单）、handle 全分支（唯一多日/单页直发/多候选/无命中/时间筛选）、二次确认（序号/DAY1/候选内名称）、会话优先级（有会话时确认优先，@ 新查询回落）、无会话不 @ 忽略、图片失败回退、config 加载默认值与文件解析、索引缓存读写。

### 3.4 `config.yaml` 增 `songbot:` 段

```yaml
songbot:
  port: 8090              # 接收器监听端口（NapCat postUrls 指向 http://127.0.0.1:8090/event）
  ttl_sec: 300            # 两段交互会话 TTL（秒）
  event_list_url: http://imas-db.jp/song/event
  index_cache: data/songbot_events.json   # 事件索引落盘缓存（空串关闭）
  index_cache_ttl_sec: 86400
  render_dir: data/songbot_img
  reply_limit: 10         # 时间筛选/候选列表单次回复上限（超出提示「还有 N 场」）
```
（napcat 发送配置复用现有 `napcat:` 段。）

## 4. Phase B：live 验收（需用户配合 + 确认，另行执行）

1. 确认 NapCat OneBot 3000 在线（已实测 ✅）；若掉线则用户在 Desktop 重启；
2. 经 NapCat WebUI API（6099，token 见 webui.json）追加 `postUrls: ["http://127.0.0.1:8090/event"]`（`messagePostFormat=array`，方法同主仓库 M6-napcat-setup.md §4）——**改 live 配置，先征得用户同意再做**；
3. 后台起 `bot.py` → 测试群（666 群 827029417）`@bot IWSF2026` → 收到子列表 → 回复 `DAY1` → 收到歌曲列表图片（QQ 群 live 测试约定：默认只发测试群）；
4. 写 S6 工作日志 + 更新 `docs/index.md`、README、taskplan 进度。

## 5. 验收口径（S6 完成判定）

- [x] `tests/test_s6_bot.py` 全绿（30/30）+ 全仓 `tests/test_s*.py` 回归（S1–S6 合计 224/224，4 个 S4 渲染用例在受限沙箱的 tempfile 清理环境性问题除外）；
- [x] `python scripts/acceptance_song.py` → `[ALL PASS]`（离线全链路 7 项，`--real-render` 真实 Edge 渲染亦 ALL PASS）；
- [x] **live：测试群（450599137）两段交互验收通过**——`@bot IWSF2026` → 子列表；回复序号 → 真实歌曲列表图片（2026-08-27，详见 `docs/modules/S6-bot-worklog.md` §7）；
- [x] 文档同步（index.md / README / taskplan 进度行 / songbot-usage.md / 工作日志）。

> 注：`postUrls` 在 NapCat v4.18.19 不存在，事件上报实为 `network.httpClients`；`827029417` 为过期测试群号，现测试群 `450599137`。详见工作日志 §7。

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| `ref/m6_notifier` import 依赖 ref/ 在 sys.path | bot.py 顶部显式插入 `ref/` + vendor；`from m6_notifier/models import ...` 单一来源 |
| 会话 context 存 Event dataclass 内存态 | 与 S5 一致，仅进程内；TTL 5 分钟可接受 |
| 大图 base64 超 QQ 上限 | S4 已分页（单张 ≤3000px）；NapCat base64:// 支持；失败回退纯文本歌单 |
| 用户 @ 后又回 DAY1（@bot DAY1） | 有会话时二次确认优先解析，解析失败才回落新查询 |
| 事件索引过期 | 缓存 TTL 24h + `--no-cache` 强制重抓 |
| live 期间 NapCat 未启动 | acceptance --live 启动时探测 `/get_login_info`，失败给出明确指引不崩 |
