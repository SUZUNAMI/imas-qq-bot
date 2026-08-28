# 子项目：歌曲列表 bot（songbot）— S11 早期演者颜色兼容修复计划

> 所属：songbot 子项目（S1–S9 + S7 收尾已完成；S10 列表图片/@only 门控待实现）。
> 目标：修复早期公演（2022 及更早）渲染**无颜色**问题——S2 识别早期类名式演者标记，颜色提取覆盖 `.idol_*{color:…}` 文本色方案。
> 创建：2026-08-27；状态：✅ **已完成（2026-08-27）**，工作日志见 `docs/modules/S11-legacy-color-fix-worklog.md`。

---

## 0. 背景与根因（缺陷，2026-08-27 实测确认）

站点演者颜色存在**两代方案**：

| 年代 | 演者标记 | 颜色定义方式 |
|---|---|---|
| 近期（2025–2026） | `<span class="idol-name" data-character-id="233" …>` | `.idol-name[data-*]{border-color:…}` → 下划线 |
| 早期（2022 及更早） | `<span class="idol_sc_sakuya">`、`<span class="idol_sc_unit02">`、`<span class="idol_ml_mirai">`、`<span class="idol_har">` 等 | `.idol_sc_sakuya{color:#006047!important}` → **文字颜色** |

- `s2_fetch_setlist.py` 只 `find_all(span, class_="idol-name")`（`SELECTOR_IDOL_NAME`），早期页 **0 个匹配** → 演者落空、`performer_colors` 全 `None`、多演者被合并成一串文本。
- `refresh_site_colors.py` 只提取 `.idol-name[data-*]{border-color}` 规则，漏掉 `.idol_*{color}` 文本色规则。
- 实测样本：MUGEN BEAT day1（2022/10/22）`0` 个 `idol-name`、`0` 个 `badge`；CSS `imas.min.css` 内 **384 个 `idol_*` 类**（`idol_sc_*` 38、`idol_ml_*` 42，另有 765AS `idol_har/chi/yuk…`、`idol_961_*`、`idol_cute/cool/passion`、`idol_intelli/mental/physical` 等）。

## 1. 影响范围

- 所有早期公演：Shiny Colors 283 UNIT LIVE（2022）、早期 Million Live / 765AS / SideM / CG 等。
- 症状：渲染图无演者颜色、品牌徽章色缺失、多个演者合并为一串。

## 2. 修复方案

### 2.1 `scripts/refresh_site_colors.py`

- 增提取 `.idol_<class>{color:…}` → `data/songbot_site_colors.json` 新增 `idol_class_colors: {类名: hex}`（约 384 条）。
- 正则（块级解析、后定义覆盖，与现有 `_extract_rule_colors` 一致）：`\.(idol_[a-z0-9_]+)\{color:\s*([^;!]+)`。

### 2.2 `songbot/s2_fetch_setlist.py`

- 演者 span 识别泛化：`span.idol-name`（近期）**或** `span[class^="idol_"]`（早期）。
- 名字提取（早期）：`title` 去 `(CV:…)` 优先；无 `title` 时取 span 文本并去尾部 `(…)`；单元名（如 `idol_sc_unit02` → アンティーカ）无 title、直接取 span 文本。
- 颜色键：早期 = 类名（经 `idol_class_colors` 解析）；近期 = `data-*`（现有 `_idol_color` 逻辑不变）。
- `_read_idol_color_tables()` 多返回一张 `idol_class_colors` 表。

### 2.3 `songbot/s4_render.py`

- `performer_colors` 由 S2 解析为 hex 后，现有下划线渲染即可生效（预计无需改动）；
- 若需早期页「文字色」保真，加一个渲染分支（underline vs text color），默认仍用下划线保持一致。

## 3. 验收

- MUGEN BEAT day1/day2、SETSUNA BEAT day1/day2 渲染有颜色、演者逐个拆分（非合并串）。
- 近期 fixture（IWSF / 13thLIVE / DERE 三版式）回归无变化。
- `refresh_site_colors.py` 产出的 `idol_class_colors` 覆盖 sc / ml / 765AS / … 全部类（约 384 条）。

## 4. 交付物

```
scripts/refresh_site_colors.py        # +idol_class_colors 提取
songbot/s2_fetch_setlist.py           # 演者 span 泛化 + 颜色键解析
songbot/s4_render.py                  # 如需要（文字色保真分支）
data/songbot_site_colors.json         # +idol_class_colors 表
fixtures/imas_db_mugenbeat_day1.html  # 新抓早期样本（离线单测用）
tests/test_s2_fetch_setlist.py        # 补早期版式用例
docs/modules/S11-legacy-color-fix-worklog.md
```

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 早期版式多样（类名前缀不一） | `span[class^="idol_"]` 宽匹配，无法解析的类名记日志跳过 |
| 部分类无颜色定义 | 无匹配颜色给 `None`（保持现状，不崩） |
| 近期版式回归 | 跑 S2 既有 46/46 单测 + 三 fixture 渲染回归 |

---

## 6. 补充发现（2026-08-27）— 近期版式 `character_colors` 被 `color-overwrite` 规则污染 ✅ 已修复

**症状**：283 Production LIVE Performance Uka（`shinycolors_283_uka.html`，近期版式）渲染应援色不正确。

**根因**：站点 CSS 对 `data-character-id` 有两套规则，且「后定义覆盖」：

| 规则 | 含义 |
|---|---|
| `.idol-name[data-character-id="N"]{border-color:#个人色}` | 纯个人应援色 |
| `.idol-name-color-overwrite-group .idol-name[data-character-id="N"]{border-color:#组合色}` | 单元公演时整团用组合色覆盖个人色 |

`scripts/refresh_site_colors.py` 的 `_extract_rule_colors` 把两套规则**都**提取进 `character_colors`，按 CSS 顺序「后定义覆盖」→ 组合色把个人色盖掉（实测污染 **77 条** character-id）。

实测：`character_colors["344"]`（小宮果穂）被污染成 `#fa8333`（组合色），正确个人色应为 `#e5461c`；该页 HTML **不含** `color-overwrite-group` 类，本应显示个人色 → 渲染错误。

**修复（2026-08-27 已完成）**：
1. ✅ `scripts/refresh_site_colors.py`：`_extract_rule_colors` **跳过选择器含 `color-overwrite` 的规则**（个人色只取纯 `.idol-name[data-character-id="N"]` 规则；src/ref 两副本同步）。
2. ✅ 重跑 `scripts/refresh_site_colors.py` 重新生成 `data/songbot_site_colors.json`（`character_colors["344"]` 恢复 `#e5461c`，其余表不变；附带确认 300 号角色个人色本就等于组合色 `#008e74`，非污染）。
3. ⏳ 服务器部署后重跑索引/`@bot refresh` 让 setlist 缓存带上正确颜色（本地已由 M9 打包时随代码分发新 JSON）。

**连带契约同步（2026-08-27 修复）**：全量单测暴露 `src/models.py` 的 `PushMessage` 缺 `ats` 字段（songbot 已用 `ref/models.py` 版本，M 模块测试先 import 缓存旧契约 → `PushMessage(ats=…)` TypeError，5 项测试失败）；已同步：
- `src/models.py` PushMessage 补 `ats: list[str] = field(default_factory=list)`（与 ref 对齐，契约文档 module-specs §1.4 本已含 `ats`）；
- `src/m6_notifier.py` 同步 ats 支持（`_coerce_message` / 普通推送首段 / 合并转发首 node 拼 at 段，与 ref 一致）；
- `_MESSAGE_FIELDS` 修正：`ats` 为**可选**字段，不进必填校验（src/ref 对齐）；
- 全量 566/566 单测全绿（主仓库根 cwd）。

## 7. 两个关联观察的审查结论（2026-08-27 复查）

**观察 A：CSS 有 `.idol-name[data-person-id="N"]` 规则，但提取与 `_idol_color` 未处理 person-id。**
- 实测：CSS 有 **360 条** `data-person-id` 规则；765AS 页演者 span **同时**带 `data-character-id` 与 `data-person-id`（春香 character-id=1 且 person-id=1）。
- 结论：**非当前缺陷**。`data-character-id` 已覆盖同一批偶像（`character_colors["1"]=#e22b30`、`["2"]=#2743d2` 正确），person-id 是一套**并行冗余方案**，被忽略不产生可见错误。
- 处置：**暂不改**；若后续发现某品牌只带 person-id 不带 character-id 而掉色，再补 `person_colors` 表与 `_idol_color` 兜底。

**观察 B：`.idol-name-color-overwrite-group` 模式未识别。**
- 实测：CSS 有该覆盖规则（单元公演整团同色）；但抽样 8 场 live（IWSF / CG 音乐剧 / 学园等）**均未启用** `color-overwrite-group`。
- 结论：**潜在风险，当前无页面触发**；若未来某页启用，现有 `character > group` 优先级会错误给个人色（应为组合色）。
- 处置：**记录在案，不提前实现**；待出现实际页面时再在 S2 解析容器类并调整优先级。
