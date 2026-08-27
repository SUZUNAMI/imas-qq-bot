# S8 歌曲反查 Live — 实施计划（施工图）

> 所属：songbot 子项目。拍板计划见 [`docs/S8-song-lookup-plan.md`](../../docs/S8-song-lookup-plan.md)；
> 本文档是**可执行施工图**（实现细节 / 选择器 / 单测要点 / 验收清单 / 产出文件），开工直接照写。
> 创建：2026-08-27；执行确认（用户拍板）：**① 命令式前缀按计划执行（裸输入回用法提示）；
> ② 验收含测试群 live；③ 歌曲索引增量刷新「按列表顺序扫描，遇到第一个已收录 LIVE 即停止」**。

---

## 0. 总览

### 0.1 目标

`@bot song <歌名>` → 歌曲反向索引查出该歌出现过的所有 LIVE（序号 + 日期）→ 用户选序号 →
复用 `_full_flow`（S2 抓详情 → S4 渲染 → 发图）。

### 0.2 依赖与前置

- 复用：`s3_match.normalize` + `_score_text` 打分（`match_songs`）；S2 `fetch_setlist`；S6 `_full_flow`。
- **前置补漏**：命令式入口 `split_command`（S3 施工图 §S3 已设计未实现，S8.3 需要，本轮一并落地）。
- 强制前缀：`live` / `song`（`binding`/`unbind`/`bindings`/`update` 属 S9，本轮只分流不实现）。
- 索引构建策略（用户拍板）：
  - 启动时**后台全量构建**（约 331 详情页）+ 落盘 JSON 缓存；构建中 `song` 查询回「歌曲索引构建中…」；
  - 每次 `song` 查询前**增量刷新**：重抓列表页 → 按列表顺序扫描详情 URL，
    **遇到第一个已收录（`source_urls`）即停止**（列表页年份降序、新 LIVE 永远在顶部，首个已收录即边界），
    仅抓取停止点之前的新增 setlist 并入索引。

### 0.3 交付物清单

```
songbot/models_song.py         # 契约扩展：Appearance / SongEntry
songbot/s3_match.py            # 新增 split_command（命令前缀分流）
songbot/s8_song_index.py       # 歌曲反向索引：SongIndex + build/refresh/save/load + match_songs
songbot/bot.py                 # 命令式入口（split_command 分流）+ song 两段交互 + 后台索引 + 增量刷新
tests/test_s8_song_index.py    # S8 单测（build/refresh/save/load/match_songs，全离线）
tests/test_s3_match.py         # 增 split_command 单测
tests/test_s6_bot.py           # 改命令前缀 + 增 song 两段交互单测
scripts/probe_song_index.py    # 探针：构建/加载索引 + 打印某歌出现过的 LIVE
scripts/acceptance_song.py     # 增 S8 离线检查（+ live 步骤说明）
data/songbot_song_index.json   # 歌曲反向索引持久缓存（真实构建后生成）
docs/modules/S8-song-lookup-worklog.md   # 工作日志（实现完成后写）
```

---

## 1. 契约扩展（songbot/models_song.py）

```python
@dataclass
class Appearance:              # 歌曲在某一公演的一次出演
    event_title: str           # 事件名
    event_year: str            # "2026"
    sub_title: str             # 子公演标题；单页事件为 ""
    date: str                  # 日期文本
    url: str                   # 该公演详情页 URL（渲染用）

@dataclass
class SongEntry:               # 反向索引一条（一首歌）
    title: str                 # 歌名（原文本，展示用）
    appearances: list[Appearance] = field(default_factory=list)
```

去重语义：
- 同一首歌在**同一场 LIVE（同详情 URL）的多次演唱只记一次** Appearance；
- 不同歌曲 `normalize` 后同键 → 合并为一个 `SongEntry`（展示名取首见），appearances 去重合并。

## 2. 命令前缀分流（songbot/s3_match.py）

```python
COMMANDS = ("live", "song", "binding", "unbind", "bindings", "update")
def split_command(s: str) -> Optional[tuple[str, str]]
#   "live IWSF2026" -> ("live", "IWSF2026")；"song Marionetteは眠らない" -> ("song", rest)
#   "bindings" -> ("bindings", "")；无前缀/未知命令 -> None（强制前缀）
```

## 3. 歌曲反向索引（songbot/s8_song_index.py）

```python
class SongIndex:
    entries: dict[str, SongEntry]    # 键 = normalize(歌名)
    source_urls: set[str]            # 已抓过的详情页 URL（增量刷新去重/停止边界）
    fetched_at: float

def _appearance_specs(events) -> list[dict]
#   事件列表 -> 详情页清单 [{url, event_title, event_year, sub_title, date}]（保持列表页顺序）
#   单页事件：url=Event.url, sub_title="", date=Event.date；多日事件：每个 SubEvent 一条

def build_song_index(events, fetch_setlist, *, progress=None) -> SongIndex
#   全量：逐个详情 URL 抓 setlist -> Track.title 入索引；单页失败记日志跳过，不中断

def refresh_song_index(index, events, fetch_setlist, *, progress=None) -> SongIndex
#   增量：按列表顺序扫描详情 URL，遇到第一个在 index.source_urls 的 URL 即停止；
#   停止点之前均为新增 -> 仅抓取这些并并入索引；原地更新，返回同一 index

def save_song_index(index, path) -> None        # JSON 落盘（ensure_ascii=False，父目录自动建）
def load_song_index(path) -> Optional[SongIndex] # 缺失/损坏 -> None（调用方重建）

def match_songs(query, index, top_n=5) -> list[SongEntry]
#   index 可为 SongIndex 或 list[SongEntry]（候选内再匹配用）
#   精确键命中优先；否则复用 s3_match._score_text 打分（阈值 60，top 5 候选）
```

## 4. 主控集成（songbot/bot.py）

### 4.1 命令分流

- `_handle` → `_first_stage` 先 `split_command(text)`：
  - `None` → 回「没看懂指令 + USAGE」（强制前缀）；
  - `"song"` → `_song_stage(inc, rest)`；
  - `"live"` → 原查询逻辑改名 `_live_stage(inc, rest)`；
  - `binding/unbind/bindings/update` → 回「该命令尚未实现（S9 计划中）」。
- `USAGE` 更新：

```
用法：
@bot live <LIVE 名/年月>（如 live IWSF2026 / live 13thLIVE / live 2026年7月）
@bot song <歌名>（如 song Marionetteは眠らない）
```

### 4.2 会话扩展（新增两种 kind）

| kind | context | 二次确认输入 |
|---|---|---|
| `CTX_SONG_CANDIDATES` | `{kind, songs: list[SongEntry]}` | 序号选歌 → `_list_song_lives`；歌名再匹配（候选内） |
| `CTX_SONG_LIVES` | `{kind, song: SongEntry, lives: list[Appearance]}` | 序号选 LIVE → `_full_flow(app.url)` → 清会话 |

### 4.3 索引生命周期

- `BotConfig.song_index_cache`（默认 `""`；config.yaml 设 `data/songbot_song_index.json`）；
- `SongBot.__init__` 增 `song_index: Optional[SongIndex]`（注入/测试）+ `song_index_lock`；
- `start_song_index()`（main 中 build_index 后调用）：有缓存则加载；无则**后台线程**全量构建 + 落盘；
- `_refresh_song_index()`（`_song_stage` 内调用）：重抓列表页（`fetch_events`）→
  `refresh_song_index`（首个已收录即停止）→ 有新增则落盘；列表重抓失败沿用现有索引并告警；
- 索引未就绪（构建中）→ 回「歌曲索引尚未构建完成…」。

### 4.4 排版纯函数（离线可单测）

```python
def format_song_candidates(songs) -> str   # "1. <歌名>（N 场 LIVE）…" + "回复序号或歌名"
def format_song_lives(entry) -> str        # "「<歌名>」出现在 N 场 LIVE：\n1. <事件名>（<子公演>） <日期>…\n回复序号查看该 LIVE 的歌曲列表"
```

## 5. 单测要点（全离线，fixtures/*.html + MockTransport / mock fetch_setlist）

**test_s8_song_index.py**
- `build_song_index`：3 个 fixture 详情 URL（mock fetch_setlist 按 URL 回 fixture）→
  `"Dance in the Light"` 命中且 **2 个 appearances**（IWSF day1 + 13th day1）；
  同场重复演唱去重（构造 mock setlist 两次同名 → 1 个 appearance）；
  `source_urls` 覆盖全部成功 URL；坏 URL（404）跳过不中断。
- `refresh_song_index`：index 已含某 URL → 事件列表首个 URL 即已收录 → **零抓取**；
  新事件插在顶部（首个 URL 未收录）→ 只抓新 URL，遇到第一个已收录即停止（断言 fetch_setlist 调用序列）。
- `save/load`：roundtrip 一致（entries/appearances/source_urls）；缺失/损坏 JSON → None。
- `match_songs`：精确（`Marionetteは眠らない`）/ 缩写或子串（`dance in the light`）/
  多候选（top N）/ 无命中 / 空 query / 候选列表入参（list[SongEntry]）。

**test_s3_match.py**：`split_command` ——
`('live IWSF2026') == ('live','IWSF2026')`、`('song Marionetteは眠らない')`、
`('bindings') == ('bindings','')`、`('binding iwsf IDOL WORLD SUPER FESTIVAL 2026')`、
`('update live') == ('update','live')`、裸输入 `'IWSF2026'` → None、空串 → None。

**test_s6_bot.py（改动 + 新增）**
- 既有第一段用例全部加 `live ` 前缀（第二段不变）；新增 `test_no_prefix_usage`（裸输入回用法）。
- song 流程：`@bot song Dance in the Light` → LIVE 列表（2 场）+ `CTX_SONG_LIVES` 会话；
  回复 `1` → 发图（mock 渲染）+ 清会话；多候选 → 候选歌列表 + `CTX_SONG_CANDIDATES` →
  回复 `1` → LIVE 列表 + `CTX_SONG_LIVES`；无命中提示；序号越界提示；
  索引未就绪 → 「构建中」提示；候选内歌名再匹配。
  （注入 `song_index` + `mock.patch("songbot.bot.fetch_events")` 返回子集事件，零网络）

## 6. 探针（scripts/probe_song_index.py）

```
python scripts/probe_song_index.py --local fixtures/imas_db_song_event.html --song "Dance in the Light"
    # 离线：fixture 列表 + fixture 详情页映射，构建迷你索引并打印出现过的 LIVE
python scripts/probe_song_index.py --song "Marionetteは眠らない" [--cache data/songbot_song_index.json]
    # 在线：真实抓取全部详情建索引（或加载 --cache）后查询
```

## 7. 验收清单

- [ ] 全仓单测全绿（S1–S6 + S8 新增）；
- [ ] `probe_song_index.py --local` 打印「Dance in the Light → 2 场 LIVE」；
- [ ] `acceptance_song.py` 离线含 S8 两段交互 ALL PASS；
- [ ] live：测试群 `@bot song <歌名>` → LIVE 列表 → 回复序号 → 歌曲列表图片（用户配合）；
- [ ] 文档同步：`docs/S8-song-lookup-plan.md` 状态、`docs/index.md` §6、`docs/songbot-usage.md`、
      `docs/modules/S8-song-lookup-worklog.md`。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 首次全量构建约 331 请求耗时 | 后台线程构建 + 落盘缓存 + 构建中提示（拍板） |
| 新 LIVE 出现 | 每次 song 查询前重抓列表页 diff，首个已收录即停止（拍板；列表页年份降序，新 LIVE 恒在顶部） |
| 歌名重名/变体 | 候选列表，不静默猜（复用 match_events 语义） |
| 站点改版 | 选择器/URL 逻辑集中在 s8 常量，单页失败跳过不崩 |
| 列表页重抓失败 | 沿用现有索引 + 告警（查询仍可用旧索引） |
| 索引并发（构建/刷新/查询） | `song_index_lock` 串行化构建与刷新；查询读 entries 不持锁（构建完成后才发布） |
