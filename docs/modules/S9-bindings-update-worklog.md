# S9 工作日志：绑定别名 + 手动刷新（binding / unbind / bindings / update live）

> 所属：songbot 子项目；计划：`docs/S9-bindings-update-plan.md`。
> 日期：2026-08-27；状态：✅ 完成（单测 + 离线全链路验收通过）。

---

## 1. 执行范围（用户拍板，2026-08-27）

| 项 | 决策 |
|---|---|
| 命令前缀 | **强制前缀**：`@bot live <名/年月>` / `binding` / `unbind` / `bindings` / `update live`；裸查询回用法提示（落实 S-songbot-plan 已拍板的命令式入口，S6 worklog §7.6 延后的改造在此完成） |
| `update live` | **只重建事件索引 + 留 S8 钩子**（歌曲反向索引属 S8，未实现前不越界）；`song_refresher` 注入点缺省跳过 |
| `song` 命令 | **本次不动**（S8 并行线程负责）：`split_command` 的 `COMMANDS` 不含 `song`，bot 收到 `song …` 按未知命令回用法；S8 完成后把 `"song"` 加入 `COMMANDS` 并替换 `_first_stage` 的兜底 else 分支 |

## 2. 实现要点

### 2.1 `songbot/s3_match.py` — `split_command`（命令分流唯一入口）

```python
COMMANDS = frozenset({"live", "binding", "unbind", "bindings", "update"})   # song 由 S8 加入

def split_command(s) -> Optional[tuple[str, str]]:
    # 按首个空白切分，首词元 casefold 后 ∈ COMMANDS 返回 (cmd, rest)；否则 None（强制前缀）
```

- `"bindings"` → `("bindings", "")`；`"update live"` → `("update", "live")`；
- 大小写不敏感；前导空白容忍；`song` 暂不在集合内（S8 接入前按未知命令处理）。

### 2.2 `songbot/s9_binding.py`（新，S9.1）

- `BindingStore`：线程安全（RLock）`set` / `get` / `remove` / `list` / `resolve` + JSON 持久化；
- key = `normalize(略缩)`（精确 normalize 匹配）；值 = `{"alias": 原样略缩, "event": event_dict}`
  （比计划「形如 {略缩(normalize): event_dict}」多存一个原样略缩，供 `bindings` 列表展示）；
- 启动加载、变更即存；文件缺失/损坏回退空表；持久化失败记日志不崩、内存内生效（风险表约定）；
- `event_to_dict` / `event_from_dict` / `sub_to_dict` 统一定义在此，bot.py 事件索引缓存改为复用
  （`_event_to_dict = event_to_dict` 等别名，旧测试引用 `bot_mod._events_*` 不受影响）——消除重复实现。

### 2.3 `songbot/bot.py` — 主控集成（S9.2）

- `BotConfig` 增 `bindings_file`（默认 `data/songbot_bindings.json`；`load_bot_config` 从
  config.yaml `songbot.bindings_file` 读取）；`config.yaml` / `config.example.yaml` 已同步；
- `SongBot.__init__` 增 `bindings: Optional[BindingStore]`（默认按 cfg 建）与
  `song_refresher: Optional[Callable]`（S8 钩子：`callable(events) -> 歌曲数`）；
- `_first_stage` 改为 `split_command` 分流：
  - 无前缀/未知命令 → `USAGE`（已更新为命令前缀说明）；
  - `live` → `_handle_live`：**绑定（精确 normalize）→ 时间查询 → 名称匹配**；
    绑定命中但事件已不在索引（下架/改版）→ 忽略绑定并提示（风险表约定），
    `_find_index_event` 按 normalize(title)/url/子事件 url 映射回当前索引（用新鲜数据）；
  - `binding <略缩> <事件名>` → `match_events` **唯一命中才绑**，0/多命中提示「请用更精确的名字」
    （不列候选，S9 计划 §0）；略缩按首个空白切分（单 token）；
  - `unbind <略缩>` → 删除回执（不存在则提示）；`bindings` → 列出全部；
  - `update live` → `_handle_update` → `refresh_all()`：**强制重抓列表 → 重建进程内事件索引 →
    落盘缓存 → `song_refresher` 钩子（缺省跳过）** → 回执「N 事件」（S8 接入后附「/ M 歌曲」）；
    刷新期间旧索引继续服务，完成后原子替换（风险表约定）；
  - 其余已识别命令（当前只有未来 S8 的 `song`）→ 兜底「尚未开放」提示。
- 索引落盘逻辑提取为 `_save_index_cache(events)`（`build_index` 与 `refresh_all` 共用）。

### 2.4 测试与验收

| 项 | 结果 |
|---|---|
| `tests/test_s9_binding.py`（新，16 项） | ✅ 全过：set/get/remove/list/resolve、normalize 精确匹配、覆盖、持久化读回、缺失/损坏文件回退、多线程并发 |
| `tests/test_s3_match.py` 补 `TestSplitCommand`（9 项） | ✅ 全过（含 `song` 暂不识别、`update live`、大小写、无前缀 None） |
| `tests/test_s6_bot.py` | ✅ 全部改为 `live` 前缀 + 新增 `TestS9BindingCommands`（12 项：强制前缀用法、绑定/解绑/列表、live 先查绑定、失效绑定提示、update live 成功/失败/带 song_refresher/参数错误） |
| `scripts/acceptance_song.py` | ✅ 离线全链路 ALL PASS（裸查询全部改 `live` 前缀；HTTP 端到端 POST 同步改） |
| 全仓单测 | 266 项中 **262 通过**；仅余 4 项 `test_s4_render` 失败为**沙箱既有环境问题**（系统 `%TEMP%` 受限 ACL，docs 已注明「S4 同坑」，与本次无关） |
| 顺带修复 | `tests/test_s1_fetch_events.py` 导入顺序 bug：原在触发 vendor 兜底前 `import httpx`，单独跑必挂；改为先 `from songbot import s1_fetch_events` 再 import（与 test_s3 同写法） |

## 3. 已知事项 / 移交说明

1. **全名绑定多命中**：`binding x THE IDOLM@STER MILLION LIVE! 13thLIVE` 会「命中 5 个」不绑
   （13th/12th/11th/10th/8th 因 SequenceMatcher ≥0.8 兜底误近），这是 S3 既有模糊匹配语义
   （对**短查询** `13thLIVE` 有 11th/12th 防误配，长全名仍会近匹配）；按拍板规则 0/多命中不绑。
   用户用唯一名（如 `13thLIVE`）绑定即可。如需收紧可在 S7 收尾时评估（改 FALLBACK_MIN_RATIO 或对全名查询禁用兜底）。
2. **绑定持久化**：`data/songbot_bindings.json`（本次已建空 `{}` 占位）；bot 运行后由
   `BindingStore` 维护，勿手工编辑格式。
3. **S7 收尾（留到最后执行，本次不做）**：start/stop_songbot.cmd 挂载脚本、合并回主仓库、
   README 同步、全仓（含 S4 真机）复跑。

## 4. 后记：S8 已并行落地（2026-08-27 同日）

S9 执行期间 S8 线程并行完成并已并入本工作区，合并后全量 277 项测试中 273 通过
（仅余 4 项 S4 沙箱既有环境错误，系统 %TEMP% 受限 ACL）。S9 预留的接入点已全部被 S8 使用：

- `s3_match.COMMANDS` 已加入 `"song"`（S8 改）；`TestSplitCommand` 同步增 `test_song`、删 `test_unknown_command_returns_none`（song 不再是未知命令）；
- `bot.py`：`_first_stage` 的兜底 else 已替换为 S8 的 `elif cmd == "song": _handle_song(...)`；
  新增 `song_index` / `song_index_lock` / `start_song_index` / `_refresh_song_index` / `_song_refresher`；
- **`update live` 的歌曲索引部分**：S8 实现 `_song_refresher(events)`（全量重建反向索引并返回歌曲数），
  测试中 `bot.song_refresher = bot._song_refresher` 注入——正是 S9 预留的 `song_refresher` 钩子，`refresh_all` 签名未变；
- 会话新增 `CTX_SONG_CANDIDATES` / `CTX_SONG_LIVES`（`_try_confirm` 分支 S8 实现）。

**S8 测试副作用已修复（2026-08-27 16:16）**：S8 的 `_song_bot()` 已显式传 `config=BotConfig()`
（空缓存路径，不继承 config.yaml 的真实 index_cache / song_index_cache / bindings_file），
不再把迷你索引写进真实 `data/` 缓存（与 S9 `_update_bot` 同款隔离）。
本工作日志早前手工重建的 `data/songbot_events.json`（fixture 125 事件）保持有效；
`data/songbot_song_index.json` 为 S8 真实全量构建产物（2264 首歌 / 322 来源页，2026-08-27 16:05），保留。

## 5. 权限控制（2026-08-27 追加拍板：管理命令仅群主/管理员）

- **范围**：`binding` / `unbind` / `bindings` / `update live` 仅 `owner`（群主）/ `administrator`（管理员）可用；
  `live` / `song` 全员可用。原 S9 计划「非目标：不做权限控制」已回改。
- **实现**：
  - `songbot/s5_receiver.py`：`Incoming` 增 `role` 字段（默认 `"member"`）；`parse_event` 从
    `payload.sender.role` 解析并 casefold 归一，缺失/非 dict/非法值一律回退 `"member"`（权限默认收紧，防伪造绕过）；
  - `songbot/bot.py`：常量 `ADMIN_ROLES = {"owner", "administrator"}`、`MANAGE_COMMANDS =
    {"binding", "unbind", "bindings", "update"}`；`_first_stage` 在分流前对管理命令检查
    `inc.role`，非管理员回 `ADMIN_DENY_TEXT`（「该命令仅群主/管理员可用…」）且**不执行**（无副作用）。
- **测试**：`tests/test_s5_receiver.py` 增 `TestParseEventRole`（owner/administrator/member/缺失/非 dict/非法值/大小写，8 项）；
  `tests/test_s6_bot.py` 的 `_inc` 增 `role` 参数（默认 `"owner"`，管理命令用例默认有权限）；
  `TestS9BindingCommands` 增 3 项：member 用 binding/unbind/bindings/update 全拒且无副作用（update 断言 fetch 未被调用）、
  administrator 可用 binding、member 可用 live。
- **NapCat 侧**：无需额外配置——OneBot 11 群消息事件自带 `sender.role`（NapCat httpClients 上报含 `sender`）。
