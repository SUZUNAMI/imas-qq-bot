# S3 工作日志：查询判别 + 模糊匹配 + 时间筛选（s3_match）

> 所属：子项目 songbot（歌曲列表 bot） · 施工图：`docs/S1-S7-taskplan.md` §S3
> 日期：2026-08-27 · 状态：✅ 完成（单测 50/50 通过；样本查询/时间筛选离线验收全部命中正确；
> 全仓回归 S1+S2+S3 = 118/118）

## 1. 目标

把用户输入映射到查询类型（时间 / 名称）：
- 时间 → 按年/月筛选事件列表（`filter_by_time`）；
- 名称 → 唯一事件或候选列表（`match_events`），二次确认定位子公演（`match_sub`）。
纯函数、零网络，依赖 S1 的 `Event` / `SubEvent` 结构。契约为 `songbot/models_song.py`。

## 2. 交付物

| 文件 | 说明 |
|---|---|
| `songbot/s3_match.py` | 8 个纯函数：`normalize` / `normalize_light` / `classify_query` / `parse_time_query` / `parse_month` / `filter_by_time` / `match_events` / `match_sub`；附带 `python -m songbot.s3_match <query>` 命令行自测 |
| `tests/test_s3_match.py` | 50 个单测（unittest，全离线）：规范化/判别/解析 + fixture 时间筛选 + 名称匹配 + 子公演匹配 + 防御路径 |
| `songbot/models_song.py` / `songbot/s1_fetch_events.py` | **契约补漏**：`Event.date` 字段（施工图 S1 契约本有，S1 实现漏写；S3 时间筛选依赖）——单页事件解析 `small.date`（去 `- ` 前缀），多日事件为空串（日期在子事件） |
| `tests/test_s1_fetch_events.py` | 补 3 个 date 断言（单页 date / 多日 date 空 / 去 `- ` 前缀）→ S1 25→28 |

## 3. 关键设计（匹配策略）

- **两种规范化**：`normalize`（名称匹配）NFKC + casefold + 去空白与**所有非字母/数字/日文字符**
  （含 `・` 中点——注意 `・` U+30FB 落在片假名区 `\u3040-\u30ff` 内，须把保留范围拆成
  `\u3040-\u30fa` + `\u30fc-\u30ff` 两段才能剔除）；`normalize_light`（时间判别）只去首尾空白，
  保留 `/` `-` 分隔符，否则 `2026-07` 会变 `202607` 无法判别。
- **时间判别正则**（`normalize_light` 后）：`^(20\d{2})\s*年?\s*(\d{1,2})?\s*月?$`（`2026年7月`/`2026年`/`2026`）、
  `^(20\d{2})[/\-.](\d{1,2})$`（`2026-07` 等）、`^(\d{1,2})月$`（`7月`，年份用索引最大年份兜底）。
  `13thLIVE` / `IWSF2026` 不误判（不以 `20\d{2}` 开头 / 不含年月经）。
- **打分**：完全相等 100 > 子串包含 80（候选⊇query 或 query⊇候选）> 词元/缩写覆盖 60 >
  `difflib` ratio 兜底（**ratio ≥ 0.8 才给分**）；低于阈值 60 不算候选。
- **词元覆盖**：query 每个词元（连续字母/数字段）命中候选某词元——**相等**，或**字母词元为候选词元子串**。
  **数字词元只允许相等**（`DAY3` 的 `3` 不得因子串匹配中 `13thLIVE` 的 `13`）。
- **缩写匹配**：query 的纯字母词元全部命中候选**首字母缩写**（`IWSF` ~ `IDOL WORLD SUPER FESTIVAL`），
  只认「query ⊆ 候选缩写」方向（候选缩写 ⊂ query 会误配——候选只含一个字母词 `in` 时缩写为 `i`，
  `i in 'iwsf'` 导致误命中）。

## 4. 开发中的三个误命中教训（实现要点）

| 现象 | 根因 | 处置 |
|---|---|---|
| `IWSF2026` 误中 MIKI HOSHII SHOWCASE 等长英文标题 | 词元**子序列**匹配太宽松（`iwsf` 在任意长英文串里按序出现） | 子序列 → **首字母缩写**匹配（语义精确） |
| `13thLIVE` 误中一批 `XthLIVE`（11th/12th/SHINY COLORS 7th） | ① 候选 token ⊂ query token（`DAY1` 的 `1` ⊆ `13`）；② ratio 兜底 `13thlive` vs `11thlive` ≈ 0.75 | ① 词元匹配方向单向 + 数字词元只相等；② 兜底 ratio ≥ 0.8 |
| `match_sub('DAY3')` 命中 `DAY1` | `3` 因子串匹配中 `13thLIVE` 的 `13` | 同上（数字词元只相等） |

## 5. 时间筛选语义决策

- `filter_by_time(events, 2026)` 返回 **14 个**（全部命中，施工图单测要点）；「单次回复上限 10 条 +
  「还有 N 场…」提示」**由调用方（S6 主控）负责截断**——筛选函数保持语义完整。
- 日期文本无 `YYYY/MM`（`parse_month` 返回 None）的事件**仅按年保留**（防御：不因日期形态异常丢事件）；
  跨月以起始月为准（`2026/07/24(金)・26(日)` → 7）。
- 多日事件：任一子公演月份命中即算；全部子项无日期则仅按年保留。

## 6. 测试与验收

- 单测：`python -m unittest tests.test_s3_match -v` → **50/50 通过**（全离线）：
  - normalize（全角/分隔符/casefold/日文保留/符号剔除/`・` 中点剔除/空串）；normalize_light（保留分隔符）。
  - classify_query：6 种时间格式 → time；`13thLIVE`/`IWSF2026`/`シャニ`/`学園`/`DERE of the DEAD`/
    `2026年7月14日` → name；全角 `２０２６年７月` → time。
  - parse_time_query：`7月`→(latest,7)、`2026年`→(2026,None)、`2026-07` 等 → (2026,7)、非时间 → None。
  - parse_month：首个 `YYYY/MM`；跨月取起始月；`(DAY1夜・DAY2昼)` → None。
  - filter_by_time（fixture 真实 125 事件）：2026 全年 14；2026-07 → 恰好 2（IWSF 多日 + DERE 单页，
    顺序保持）；5 月含 13thLIVE/H.I.F 選抜試験；3 月含 11thLIVE；13 月/1999 年 → []；防御 4 项（无
    YYYY/MM 仅按年保留、子项部分无日期、空事件列表）。
  - match_events：`IWSF2026`/`IWSF` 唯一命中 IWSF（缩写）；`13thLIVE` 唯一命中 MILLION 13thLIVE
    （**不误中** 11th/12th/SHINY COLORS 7th 等近似串）；`シャニ` 命中 シャニマス大感謝祭 等 SHINY
    COLORS 相关；`学園` top 5 候选全为学園事件且 2026 排前；`DERE of the DEAD` 唯一命中；全角
    `１３ｔｈＬＩＶＥ` 命中；无命中/空 query/空列表 → []。
  - match_sub：`DAY1`/`day1`/`全力援走`/`1` → DAY1 全力援走；`2` → DAY2；`DAY3`/`0`/`3`/无 → None；
    None 事件 → None；单页事件无子公演 → None；IWSF 按 `1`/`YAKUDOU` 定位子公演。
- 探针验收（S3 验收清单，`python -m songbot.s3_match <query>`）：
  - `IWSF2026` → 唯一命中 IWSF 2026 ✓；`13thLIVE` → 唯一命中 MILLION 13thLIVE ✓；
  - `2026年7月` / `7月` / `2026-07` → 均为 IWSF + DERE 2 场 ✓；`5月` → 4 场（13thLIVE 等）✓；
  - `シャニ` → 2 候选 ✓；`学園` → 5 候选 ✓；`不存在的演出xyz` → 无命中 ✓。
- 全仓回归：`python -m unittest tests.test_s1_fetch_events tests.test_s2_fetch_setlist tests.test_s3_match`
  → **118/118 通过**（S1 28 + S2 40 + S3 50）。注：`test_s4_render` / `test_s5_receiver` 由并行
  会话负责（S4 渲染测试在沙箱内因 Edge/Playwright 权限失败，与本阶段无关）。

## 7. 已知项 / 后续

- 名称匹配候选文本 = `title` + 子公演 `title`/`full_title`，**不含品牌徽章**（`brands`）。
  `ミリオン` 等品牌名查询目前不命中（fixture 无品牌名进标题的事件）；若 S6 需要可按品牌名扩候选。
- `2026年7月14日` 这类带「日」的查询判为 name（不支持的粒度），可接受。
- 拼写容错有限：兜底 ratio ≥ 0.8 只覆盖高度相似串（如 `13thLIVE` vs `11thLIVE` 被正确排除）。
- 下一步：S4 图片渲染（并行进行中）/ S5 事件接收（并行进行中）→ S6 主控串联消费 `filter_by_time`
  的 10 条截断 + 「还有 N 场…」提示。
