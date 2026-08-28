# S11 工作日志：早期演者颜色兼容修复（idol_* 类名版式）

> 所属：songbot 子项目；计划：`docs/S11-legacy-color-fix-plan.md`。
> 日期：2026-08-27；状态：✅ 完成（单测 + 解析/HTML/PNG 三层验收通过）。
> 范围：仅 S11（S10 列表图片/@only 门控不在本次，另行执行）。

---

## 1. 执行范围与决策

| 项 | 决策 |
|---|---|
| 演者识别 | `span.idol-name`（近期）**或** `span[class^="idol_"]`（早期）统一由 `_is_idol_span` / `_idol_spans_of` 处理 |
| 名字提取（早期） | `title` 去 `(CV:…)` 优先（角色名，如 `title="櫻木真乃(CV:関根瞳)"` → 櫻木真乃）；无 `title` 取 span 文本并去尾部 `(…)`（单元名如 `idol_sc_unit02` → アンティーカ） |
| 颜色键（早期） | 类名查 `idol_class_colors`（`.idol_*{color:…}` 文字色表），data-* 现有逻辑优先级不变 |
| 渲染分支 | **不加**：`performer_colors` 解析为 hex 后，S4 现有下划线色带（`border-color`）直接生效，与原网页 `.idol-name` 一致；未引入「文字色保真」分支（计划 §2.3 默认项） |
| fixture | 新增 MUGEN BEAT day1/day2（2022/10）、SETSUNA BEAT day1（2022/08）三份早期样本（真实抓取，0 个 idol-name） |

## 2. 实现要点

### 2.1 `scripts/refresh_site_colors.py` — `extract_idol_class_colors`

- 块级解析 `.idol_<class>{color:…}`（与 `_extract_rule_colors` 一致的 `([^{}]+)\{([^}]*)\}` 遍历，
  逗号分隔共享块的每个类都记录，后定义覆盖先定义）；
- `color:` 捕获组 `[^;!}]+` **排除 `!important`**（早期规则普遍带 `!important`，如
  `#fff68d!important`，初版正则漏排除、已修复）；
- 输出写入 `data/songbot_site_colors.json` 新键 `idol_class_colors`（**174 条**，覆盖
  sc 37 / ml 76 / gk 15 / 765AS `idol_har|chi|yuk…` / `idol_961_*` / cute/cool/passion /
  intelli/mental/physical / valiv 等；计划预估 384 条为「全部 idol_* 类」口径，含无 color 定义者）。

### 2.2 `songbot/s2_fetch_setlist.py` — 演者 span 泛化（S11 核心）

- `_is_idol_span(span)`：class 含 `idol-name` **或** 任一 class 以 `idol_` 开头（近期/早期统一判定）；
- `_idol_spans_of(el)`：el 内全部演者 span（文档序），替换原 `find_all(…, class_="idol-name")`；
- `_legacy_idol_name(span)`：早期名字提取（title 去 `(CV:` → 角色名；无 title 去尾括号；单元名取文本）；
- `_performer_name(span)`：近期 idol-name 取 span 文本（忠实原网页显示，行为不变）；
- `_idol_color(span, tables)`：解包 6 元组；data-* 优先级（character > group > attr > brand）不变，
  末尾新增「class 以 `idol_` 开头 → `idol_class_colors`」兜底，无匹配给 `None`（不崩）；
- `_read_idol_color_tables()` 返回 6 元组（新增末位 `idol_class_colors`），JSON 缺失/损坏回退空表；
- `_find_performers` / `_parse_performers_cell` 改用 `_idol_spans_of` + `_performer_name`；
- 模块 docstring 增补「版式 D（早期）」说明。

### 2.3 `data/songbot_site_colors.json` — 重新生成

- 运行 `python scripts/refresh_site_colors.py`（vendor PYTHONPATH）：
  14 品牌色 / 14 brand-id / 11 属性色 / 48 组合色 / 348 角色个人色 / **174 早期类色** / 6 版式色；
- 其余表与旧 JSON 一致（字符集/格式不变），向后兼容。

### 2.4 `songbot/s4_render.py` — 无需改动

- `performer_colors` 已是 hex → `_idol_spans` 的 `border-color` 下划线色带自动生效；
- 决策：不加「早期文字色保真」分支（默认下划线，与近期版式渲染一致；契约零变更）。

## 3. 测试与验收

### 3.1 单测（`tests/test_s2_fetch_setlist.py`，46 → 55）

- `_TABLES` 扩为 6 元组（新增 `idol_class_colors` 测试表）；
- 新增 `TestLegacyIdolClassLayoutD` 9 项：
  出演块拆分（单元名+成员、非合并串）、title 去 CV 角色名、tracklist 单元 span 解析、
  `idol_class_colors` 颜色命中（MUGEN/SETSUNA）、「全員」无色 None、无颜色定义的类回退 None 不崩。

### 3.2 回归

| 范围 | 结果 |
|---|---|
| S2（含新用例） | 55/55 ✅ |
| S1 / S3 / S5 / S6 / S8 / S9 + S4 纯函数 | 293/293 ✅ |
| S4 渲染（Playwright/Edge CLI） | 5 项为受限沙箱既有限制（子进程管道 PermissionError），与 S11 无关（s4_render 未改动） |
| 近期三 fixture（IWSF / 13thLIVE / DERE） | 解析结果不变（既有用例覆盖） |

### 3.3 渲染验收（危险全权限，一次性）

- `render_setlist` 真实渲染 MUGEN BEAT day1 → PNG（1760×1770，410KB）；
- **像素级验证：16/16 个 MUGEN 应援色全部命中**（#fff68d イルミネーションスターズ、
  #853998 アンティーカ、#af011c ストレイライト、#008e74 SHHis、#006047 白瀬咲耶 等），
  早期公演渲染「有颜色、演者逐个拆分」验收通过；
- 出演头部/曲目行演者均带色带；「全員」行无色（与原网页一致）。

## 4. 交付物

```
scripts/refresh_site_colors.py        # +extract_idol_class_colors（块级、去 !important）
songbot/s2_fetch_setlist.py           # 演者 span 泛化 + 早期名字/颜色键解析（6 元组色表）
data/songbot_site_colors.json         # +idol_class_colors（174 条）
fixtures/imas_db_mugenbeat_day1.html  # 早期样本（2022/10/22，0 idol-name）
fixtures/imas_db_mugenbeat_day2.html  # 早期样本（2022/10/23）
fixtures/imas_db_setsunabeat_day1.html# 早期样本（2022/08/13）
tests/test_s2_fetch_setlist.py        # +TestLegacyIdolClassLayoutD（9 项）
docs/modules/S11-legacy-color-fix-worklog.md
```

## 5. 备注 / 移交说明

- **名字口径**：早期页原网页显示声优名（span 文本），S11 按计划取 **title 角色名**
  （`(CV:…)` 之前）——与近期 IWSF 出演块的角色名口径一致；如需忠实显示声优名可改
  `_legacy_idol_name` 为取 span 文本（一行改动，颜色逻辑不受影响）。
- **idol_class_colors 数量**：174 条为「有 color 定义」口径；CSS 内全部 `idol_*` 类约 384 个
  （含无颜色/非颜色规则），刷新脚本已全部扫描。
- S10（列表图片渲染 + @only 门控）仍待执行，S11 未触碰 bot.py 命令流。

---

## 6. 追加修复（2026-08-27 深夜复查，见计划 §6/§7）

### 6.1 `character_colors` 被 `color-overwrite` 规则污染（计划 §6，✅ 已修复）

- **症状**：近期版式（283 UNIT LIVE Performance Uka 等）个人应援色被组合色覆盖——
  `character_colors["344"]`（小宮果穂）=`#fa8333`（组合色），正确 `#e5461c`；
  CSS 实测 **77 条** character-id 受影响。
- **根因**：站点 CSS 对 `data-character-id` 有两套规则（纯个人色 / `color-overwrite-group`
  组合色覆盖），`_extract_rule_colors` 按「后定义覆盖」把覆盖规则也收进 `character_colors`。
- **修复**：`_extract_rule_colors` 跳过选择器含 `color-overwrite` 的规则；
  重跑 `refresh_site_colors.py` 重新生成 JSON —— `character_colors["344"]=#e5461c` 恢复，
  组合色值不再出现在 character 表（300 号 `#008e74` 为本人个人色，非污染）；
  `idol_class_colors` 174 / group 48 / attr 11 / brand 14 不变。
- **验证**：`python scripts/refresh_site_colors.py` → 输出 14/14/11/48/348/174/6，
  character 表无 `#fff68d/#853998/#fa8333/#ff699e/#af011c/#384d98/#008e74/#333` 泄漏
  （300 除外，其纯个人色规则即 `#008e74`）。

### 6.2 连带：`PushMessage` 契约 `ats` 不同步（全量单测 5 项失败，✅ 已修复）

- **症状**：主仓库全量单测（含 M1–M7 模块测试）5 项 ERROR：
  `PushMessage.__init__() got an unexpected keyword argument 'ats'`
  （`tests/test_s6_bot.py::TestReplyAttribution` 5 用例）。
- **根因**：M 模块测试先 `sys.path.insert(0, src)` 并 import 了 **`src/models.py`**（旧契约，
  PushMessage 无 `ats`）→ `sys.modules["models"]` 缓存旧类；随后 songbot 的 bot.py
  `from models import PushMessage` 拿到缓存旧契约，而 `_default_sender` 传 `ats=…` → TypeError。
  songbot 用 `ref/models.py`（有 ats）与主契约 `src/models.py`（无 ats）**不同步**。
- **修复**：
  - `src/models.py` PushMessage 补 `ats: list[str] = field(default_factory=list)`
    （module-specs §1.4 契约文档本已含 `ats`，代码未跟上）；
  - `src/m6_notifier.py` 同步 ats 支持（`_coerce_message` 解析 / 普通推送首段拼 at 段 /
    合并转发首 node 拼 at 段，对齐 ref 版）；
  - `_MESSAGE_FIELDS` 修正：`ats` 为**可选**字段，不进必填校验（src/ref 两副本对齐）。
- **验证**：主仓库根 cwd 全量 `python -m unittest discover -s tests` → **566/566 OK**
  （含此前失败的 TestReplyAttribution 5 项；S4 渲染 33 项亦全绿）。
