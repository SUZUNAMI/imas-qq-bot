# S8 工作日志：歌曲反查 Live（songbot.s8_song_index + bot 集成）

> 所属：songbot 子项目。拍板计划：`docs/S8-song-lookup-plan.md`；实施施工图：`docs/modules/S8-song-lookup-plan.md`。
> 完成：2026-08-27。与 S9 线程并行开发（S9 负责 binding/unbind/bindings/update + 命令式入口，
> S8 负责 `song` 命令 + 歌曲反向索引；`s3_match.COMMANDS` 由 S9 预留 `song` 槽位、S8 加入）。

---

## 1. 交付物

```
songbot/models_song.py            # 契约扩展：Appearance / SongEntry（S8 §4）
songbot/s3_match.py               # COMMANDS 加入 "song"（split_command 由 S9 线程实现）
songbot/s8_song_index.py          # SongIndex + build/refresh/save/load + match_songs（新）
songbot/bot.py                    # song 分支两段交互 + 后台建索引 + 查询前增量刷新 + update live 钩子
tests/test_s8_song_index.py       # 20/20（全离线）
tests/test_s3_match.py            # +split_command song 用例（62/62）
tests/test_s6_bot.py              # +TestS8SongFlow 14 用例（54/54）
scripts/probe_song_index.py       # S8 探针（构建/加载索引 + 打印某歌出现过的 LIVE）
scripts/acceptance_song.py        # +S8 离线检查（song 唯一/多候选/无命中），离线 ALL PASS
config.yaml / config.example.yaml # songbot.song_index_cache
docs/modules/S8-song-lookup-plan.md / S8-song-lookup-worklog.md
data/songbot_song_index.json      # 真实索引持久缓存（live 构建后生成）
```

## 2. 关键设计决策（用户拍板，2026-08-27）

| 项 | 决策 | 实现 |
|---|---|---|
| 命令式入口 | `live`/`song` 强制前缀，裸输入回用法提示 | `_first_stage` 走 `split_command`；`song` 分支 `_handle_song` |
| 索引构建 | 启动**后台线程**全量构建（约 331 详情页）+ 落盘 JSON 缓存；构建中回「歌曲索引构建中…」 | `start_song_index()` / `_build_song_index_bg()`（daemon 线程，`song_index_lock` 串行化） |
| 增量刷新 | 每次 `song` 查询前重抓列表页 → **按列表顺序扫描详情 URL，遇到第一个已收录（source_urls）即停止**，仅抓新增并入索引 | `refresh_song_index`（列表页年份降序、新 LIVE 恒在顶部，首个已收录即边界）；列表重抓失败沿用旧索引并告警 |
| update live 钩子 | S9 `refresh_all` 重建歌曲索引并回报歌曲数 | `SongBot._song_refresher(events)`（main 中挂到 `song_refresher`；测试默认 None 保持 S9 语义） |
| 匹配 | 复用 `s3_match.normalize` + `_score_text`（阈值 60 / top 5 候选），**不静默猜**（精确命中只排最前，其他候选仍列出） | `match_songs(query, index_or_list)` |
| 去重 | 同一歌在同一场 LIVE（同详情 URL）多次演唱只记一次 appearance；normalize 同键合并为一条 SongEntry | `_merge_setlist` |

## 3. 实现要点

- **`_appearance_specs(events)`**：事件列表 → 详情页清单 `[{url, event_title, event_year, sub_title, date}]`，
  保持列表页顺序（增量刷新「首个已收录即停止」依赖此序）；单页事件 sub_title=""，多日事件每个 SubEvent 一条。
- **`refresh_song_index` 停止边界**：`if url in index.source_urls: break`——首个已收录 URL 之前的全部视为新增。
  代价：若站点在列表中部插入新 LIVE 会漏扫（列表页按年降序、新 LIVE 恒在顶部，实际不会发生；记入风险表）。
- **match_songs 语义修正（开发中发现）**：初版做了「精确键命中优先直接返回」，导致 `brand new` 只回精确命中的
  `Brand New!!`、把 `Brand New Wave!`（80 分）吞掉——与 `match_events`「候选列表不静默猜」语义不一致。
  改为纯打分（完全相等 100 自然排最前，其余 >= 60 仍列出），20/20 单测 + bot 多候选集成测试覆盖。
- **bot 集成**：
  - `_handle_song`：空歌名提示 → 索引未就绪提示 → `_refresh_song_index()` → `match_songs` →
    唯一 `_list_song_lives`（CTX_SONG_LIVES）/ 多候选 CTX_SONG_CANDIDATES / 无命中 + 用法；
  - `_try_confirm` 新增两 kind：`CTX_SONG_CANDIDATES`（序号选歌 / 候选内歌名再匹配）、
    `CTX_SONG_LIVES`（序号选 LIVE → `_full_flow` 发图 → 清会话）；
  - `BotConfig.song_index_cache`（默认 ""；config.yaml 设 `data/songbot_song_index.json`）。
- **离线测试路径**：迷你索引用 3 个 fixture 详情页（`imas_db_iwsf_day1.html` / `imas_db_million_13th_day1.html` /
  `imas_db_cg_musical_dd.html`）构建；「Dance in the Light」在 IWSF day1 + 13th day1 各一次 → 2 场 LIVE。
  注意 fixture URL 实际为 `idolmaster_iwsf_day1.html` 等带前缀名，映射用 **endswith**（与 S2 MockTransport 同约定），
  初版用 basename 精确匹配导致构建漏页（测试调试中发现）。

## 4. 测试与验收

- 单测：`test_s8_song_index` 20/20（build 覆盖/同场去重/坏页跳过/空事件；refresh 全已知零抓取、
  新增在顶部只抓新增并停止、保留既有；save/load roundtrip/缺失/损坏；match_songs 精确/全角/子串/多候选/无命中/空/列表入参）；
  `test_s3_match` 62/62（+song split_command）；`test_s6_bot` 54/54（+14 S8 song 流程：唯一列 LIVE/序号发图/越界/
  多候选选歌/无命中/空歌名/索引未就绪/列表刷新失败沿用/update live 重建）；`test_s9_binding` 24/24。
- 非 S4 全仓：**259/259 通过**（S4 4 项因沙箱禁写 %TEMP% 无法在受限环境跑，属已知环境限制，非代码问题）。
- `scripts/acceptance_song.py` 离线全链路 **ALL PASS**（含新增 3 项 S8 检查）。
- `scripts/probe_song_index.py --local` 离线验证：「Dance in the Light」→ 2 场 LIVE（IWSF 第一公演 -YAKUDOU- 2026/07/24(金)
  + 13thLIVE DAY1 2026/05/05(火祝)），事件名/子公演/日期/URL 齐全。
- **live 验收（测试群）**：见第 6 节。

## 5. 与 S9 线程的协调（并行开发记录）

- S9 线程先落地 `split_command`（COMMANDS 不含 song，注释明确「song 由 S8 并行线程接入」）；
  S8 把 `"song"` 加入 COMMANDS 并把 `TestSplitCommand.test_unknown_command_returns_none` 中
  「song 返回 None」的断言改为 song 正常分流（`test_song`）。
- S9 在 `SongBot` 预留 `song_refresher` 钩子（update live 重建歌曲索引），S8 提供 `_song_refresher` 实现，
  仅在 `main()` 挂载（测试注入 None 保持 S9 既有断言「未接 S8 钩子」不变）。
- 双方并发编辑 `bot.py`/`test_s6_bot.py`，文件级「changed since read」守卫未发生覆盖丢失（S8 改动在
  S9 后续写入后仍完整，已复查）。

## 6. live 验收（测试群）

- [x] 真实索引构建完成（`data/songbot_song_index.json`：**2264 首歌 / 322 个来源页**，覆盖 2013–2026；
      9 页抓取失败跳过（多为已下线旧页），不影响反查）；
- [x] **测试群 live 验收通过（2026-08-27）**：`@bot song Dance in the Light` → 收到「出现在 4 场 LIVE」列表
      （IWSF 2026 / 13thLIVE / MOIW 2025 / 10thLIVE Act-4 DAY2）→ 回复序号 → 收到对应公演歌曲列表**图片**
      （真实 Edge 渲染，完整权限环境）；`song Marionetteは眠らない` 等多条查询正常；
- [x] 增量刷新逻辑 live 就绪：每次 `song` 查询前重抓列表页 diff，首个已收录即停止（零新增时零抓取）。
- 附带验证：接收器链路（POST → 200 → 处理 → 抓详情 → 渲染 → 发送尝试）经假群 canary 全链路打通，
  NapCat 拒绝不存在群属预期行为（群内真实测试由用户完成，无问题）。

## 7. 风险与遗留

| 项 | 说明 |
|---|---|
| 增量刷新中部插入漏扫 | 列表页按年降序、新 LIVE 恒在顶部，首个已收录即停止成立；若站点改排序需改 `refresh_song_index` |
| 首次构建耗时 | 后台线程 + 缓存 + 构建中提示；live 环境已预构建缓存，启动即用 |
| song 与 binding 命令集 | S9 的 `binding` 等命令对 `live` 生效；`song` 不受绑定影响（按计划） |

## 8. 修复记录（live 反馈）

- **「回复序号或歌名」重复（2026-08-27 用户 live 反馈）**：`format_song_candidates` 内部已带
  二次确认提示，但 `_handle_song` 多候选与 `_try_confirm` CTX_SONG_CANDIDATES 重列两处调用点
  又追加了一次 `"\n回复序号或歌名"` → 回复中出现两次。已删除两处调用点的重复拼接
  （保留函数内部提示，与 `format_song_lives`/`format_sub_list` 风格一致）；
  新增回归断言（`test_song_multi_candidate_then_pick` / `test_song_candidate_relist_single_hint`
  断言提示恰好出现 1 次），S6 56/56 + S8 21/21 通过；bot 已重启，live 实测
  `song ダンス` 多候选回复提示只出现 1 次。

## 9. 功能增补（2026-08-27 用户追加要求）

### 9.1 `quit` 取消等待

- 用户在任何等待状态下回复 `quit`（大小写不敏感）→ **立即清会话**，回「已取消本次查询，
  可重新 @bot 发起（live / song）」；无 @ 且无进行中查询时忽略（不打扰）；
  `@bot quit` 无会话时回「当前没有进行中的查询」。
- 实现：`SongBot._handle` 顶部先判 `text.casefold() == "quit"`（先于会话确认/命令分流）。

### 9.2 回复按用户归属（@）

- **会话本就按 `(group_id, user_id)` 分类**（S5 `SessionStore` 键），不同用户的等待互不串线；
- 本次补齐**回复归属**：所有文本回复开头带 `[CQ:at,qq=<发起用户>]`（`_send_text` 增 `user_id` 参数，
  约 35 处调用点全部传入）；图片发送（`_full_flow`）同样带 @ 归属。
- **2026-08-27 用户 live 反馈修正**：`[CQ:at,...]` 嵌 text 段在 NapCat array 消息形态下**字面显示**
  （回复前出现显式 CQ 码前缀）→ 改为**独立 `at` 段**：`PushMessage` 契约追加 `ats` 字段（默认空、向后兼容，
  回改 `docs/module-specs.md` §1.4），M6 `_push_one_group` 把 ats 拼为 `{"type":"at","data":{"qq":...}}`
  段附在首段文本/图片前；bot.py `_default_sender` 把 `_send_text` 拼的 CQ 前缀经 `_extract_at_qq` 拆出
  转 ats（测试 sender 仍见带 CQ 文本，断言不变）。
- 作用：群内多人并发查询时，每条回复/图片明确 @ 指向发起用户，且以规范 at 段渲染（非字面 CQ 码）。

### 9.3 测试与验收

- `tests/test_s6_bot.py` 新增 `TestQuit`（5 项：取消 event 会话 / 取消 song lives 会话 / `@bot quit` 无会话 /
  无会话无 @ 忽略 / 回复 @ 归属 + 双用户会话隔离）与 `TestReplyAttribution`（6 项：`_extract_at_qq` 纯函数、
  `_default_sender` CQ→ats 转换（文本/图片/无 @）、M6 wire 格式（at+text / at+image 独立段））；
  图片发送断言由「空文本」改为「@归属文本」；
- `scripts/acceptance_song.py` 图片断言同步更新；全仓非 S4 **284/284 通过**，离线全链路 ALL PASS；
- bot 已重启，live canary（假群 111111）验证 song 查询 → 选序号出图 → quit 三条链路均正常处理；
  at 段发送后 NapCat 进入「Get Uid Error」（假用户 999 无法解析 UID）——证明 @ 已被按真实 at 段处理，
  真实群内用户 QQ 可正常解析渲染。
