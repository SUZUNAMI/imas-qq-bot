# S1 工作日志：列表抓取 + 解析（s1_fetch_events）

> 所属：子项目 songbot（歌曲列表 bot） · 施工图：`docs/S1-S7-taskplan.md` §S1
> 日期：2026-08-27 · 状态：✅ 完成（单测 25/25 通过；离线 fixture + live 官方站点双验收一致）

## 1. 目标

抓 `http://imas-db.jp/song/event`，解析出全部顶层事件（单页/多日、子公演、日期、URL、品牌徽章、年份），
输出 `Event[]`（年份降序，与网页顺序一致），供 S3 模糊匹配消费。

## 2. 交付物

| 文件 | 说明 |
|---|---|
| `songbot/models_song.py` | 契约 dataclass（单一事实源）：S1 冻结 `SubEvent` / `Event`；S2 契约 `Track` / `Setlist` 一并提前写入（计划 §0.1「契约先行冻结」） |
| `songbot/s1_fetch_events.py` | 抓取 + 解析：`fetch_events(url=EVENT_LIST_URL, *, client=None) -> list[Event]`；纯函数 `parse_events_html(html, base_url=...)` 供离线单测 |
| `tests/test_s1_fetch_events.py` | 25 个单测（unittest，本机无 pytest 且 pip 被拦截；合并回主仓库后 pytest 可直接发现） |
| `scripts/probe_song_event.py` | S1 探针：默认 live 抓取，`--local <html>` 离线验收，`--json` 输出；`--setlist` 为 S2 预留 |

## 3. 实现要点

- **选择器常量集中**（`SELECTOR_*`）：`div.section` 年份分组 → `h2` 去「年」；顶层事件 = `ul.find_all('li', recursive=False)` 且带 `data-brand-ids`（fixture 实测仅两种形态：单页 34 / 多日 91，无第三种）。
- **单页事件**：直接子 `<a>` → title/href（`urljoin(base_url, href)`，base 必须以 `/` 结尾的目录语义）。
- **多日事件**：标题 = 「序列化→重解析」独立树去掉嵌套 `ul` / `span.badge` / `span.visually-hidden` / `small.date` 后 `get_text(' ', strip=True)`；子公演取 `<a>` 文本 / `title` 属性 / `small.date`（`lstrip('- ')`）。
- **品牌徽章**：`badge` 的 `title` 优先（如 `シンデレラガールズ`），缺则取文本。
- **防御**：缺 `date`/`href`/`title` 给空串；坏条目（无 `<a>` 也无嵌套 `ul`、无 `<h2>` 的 section、无 `<ul>` 的 section、无 `data-brand-ids` 的 li）记日志跳过，不抛异常。
- **vendor 兜底 + UTF-8**：`import httpx`/`bs4` 失败回退 `../vendor`；一律 `resp.content.decode('utf-8')`。

## 4. bs4 4.15 三个坑（已规避，供 S2–S7 参考）

1. **`copy.deepcopy(Tag)` 直接返回自身**（非真深拷贝）——decompose 会破坏原始树。
   → 用 `BeautifulSoup(str(li), 'html.parser')` 序列化重解析得到独立树。
2. **`decompose()` 递归清空目标及全部后代**（`__dict__.clear()`：attrs→None、name→""）——
   「先 find_all 快照、边遍历边 decompose」会在遍历到被连带清除的后代时炸 `tag.get()`。
   → 遍历时用官方 `PageElement.decomposed` 属性跳过已清除元素。
3. **`get_text()` 默认排除 `rt`/`rp`（ruby 注音）**（bs4≥4.13 变更）——`<ruby>` 标题得到
   rb 优先效果（"H.I.F 選抜試験"），比计划预期更干净；子公演 `full_title` 取自 `<a title>` 仍含
   "…選抜試験(セレクション) DAY1"（S3 匹配两路均可命中）。

## 5. 测试与验收

- 单测：`python -m unittest tests.test_s1_fetch_events -v` → **25/25 通过**（全部离线）：
  - 总数 125；年份 2013–2026 全覆盖；顺序年份降序；
  - 13thLIVE：2 个子事件，`DAY1 全力援走` / `2026/05/05(火祝)` / URL 以 `million_13th_day1.html` 结尾；
  - IWSF 2026：3 个子公演 + full_title 完整 + 8 品牌徽章 + MR 徽章 title；
  - 单页 DERE of the DEAD：url 非空、`sub_events == []`、徽章取 title；
  - 防御路径 8 项（缺 date/href/title、坏 li、坏 section、空 HTML、非顶层 li）+ 抓取层 5 项
    （MockTransport 注入、UTF-8 显式解码、404、连接失败重试耗尽、5xx 重试耗尽）。
- 探针验收（S1 验收清单）：
  - `python scripts/probe_song_event.py --local fixtures/imas_db_song_event.html` → 125 事件 ✓
  - `python scripts/probe_song_event.py`（live `http://imas-db.jp/song/event`）→ 125 事件，
    与 2026-08-26 抓取的 fixture 完全一致（站点未改版），多日 DAY 名称/日期/URL 与网页一致 ✓

## 6. 已知项 / 后续

- `<ruby>` 标题为 rb 优先（见上）；若 S3 需要「選抜試験(セレクション)」全名，子公演 `full_title` 已含。
- `scripts/probe_song_event.py --setlist <url>` **已于 S2 实现并启用**（2026-08-27，见 S2 工作日志）。
- 下一步：S3 模糊匹配（`s3_match.py` + `tests/test_s3_match.py`，依赖 S1 `Event` 结构）。
