# 子项目：歌曲列表 bot（songbot）— S10 列表图片渲染 + @only 门控计划

> 所属：songbot 子项目（S1–S9 已完成并 live 验收通过）。
> 目标：① 列表类回复渲染成图片（`render_list`，S4 泛化），避免长文本刷屏；② 未 @bot 的消息一律忽略（二次确认也要求 @bot）。
> 创建：2026-08-27；状态：**✅ 已执行完成（2026-08-27）**——S4 32/32、S6 105/105、全仓 360/360；
> `acceptance_song.py` 离线全链路 ALL PASS（mock + 真实 Edge 双模式）；live 验收待 NapCat 常驻后执行。
> 执行日志：`docs/modules/S10-list-image-atbot-worklog.md`。

---

## 0. 已拍板决策（2026-08-27）

| 项 | 决策 |
|---|---|
| 列表图片化 | 候选 / 子列表 / 时间筛选 / 歌曲出现 / bindings 等「序号 + 名称 + 日期」列表统一走 `render_list` 发图，图内附「回复序号」footer |
| render_list | S4 泛化：抽出共享管线 `render_html_pages`，新增 `build_list_html` + `render_list`；`render_setlist` 改为复用共享管线（行为不变） |
| @only 门控 | 未 `at_bot` 的消息**一律忽略**（不做二次确认）；会话二次确认（序号/DAY1/歌名）也要求 @bot |
| 回执文本 | `binding` / `unbind` / `update live` 等非列表回执保持短文本；仅列表类走图片 |

---

## 1. 目标与范围

- **输入**：命令集不变（`live` / `song` / `binding` / `unbind` / `bindings` / `update live`，强制前缀）。
- **处理**：列表类回复 → `render_list` 图片；消息门控 → 未 @ 忽略。
- **非目标**：不改命令语法、不改数据契约、不改 setlist 渲染版式。

## 2. 设计

### 2.1 S10.1 列表渲染（`songbot/s4_render.py` 泛化）

1. 抽出共享管线 `render_html_pages(pages_html: list[str], out_dir: Path) -> list[Path]`：
   现有 `_render_playwright_pages` / `_render_cli_pages` 的「逐页截图 + 裁白边」核心，改为接受**已预分页的 HTML 字符串列表**。
2. `render_setlist` 重构为「`build_html` 分页 → `render_html_pages`」，**行为不变**（S4 既有 18/18 单测须回归全绿）。
3. 新增 `build_list_html(title: str, rows: list[tuple[str, str]], *, hint: str = "回复序号") -> str`：
   列表样式自包含 HTML——标题 + 序号行「主文本 + 副文本（日期/品牌，弱化色）」+ footer 提示；样式沿用 setlist 的自包含模板与深色/浅色主题。
4. 新增 `render_list(title: str, rows: list[tuple[str, str]], *, out_dir=None, hint="回复序号") -> list[Path]`：
   `build_list_html` → 分页（复用 `MAX_PAGE_HEIGHT` / 估算行高）→ `render_html_pages`。`rows` 每项 = (主文本, 副文本)，序号自动 1 起；空 rows 返回 `[]`。

### 2.2 S10.2 @only 门控（`songbot/bot.py` `_handle` 收紧）

1. `_handle` 首行判断 `if not inc.at_bot: return`（**删除**现有「有会话但无 @ → 二次确认/提示」路径）。
2. `_try_confirm` 仅在 `at_bot=True` 时调用；语义不变（`CTX_EVENT` / `CTX_CANDIDATES` / 歌曲候选）。
3. `split_command` 分流、会话 TTL、`quit` 取消等其余逻辑不变。

### 2.3 S10.3 bot 集成列表图片化（`songbot/bot.py`）

把以下文本排版函数对应的发送点改为 `render_list` 发图（图片用现有 `base64://` 发送层）：

| 场景 | 现函数 | 改造后 |
|---|---|---|
| 时间筛选月列表 | `format_event_list` | `render_list(标题, [(事件名, 日期/子项)])` |
| 多候选列表 | `format_event_list` | 同上 |
| 多日子列表 | `format_sub_list` | `render_list(事件名, [(DAY 标题, 日期)])` |
| 歌曲候选列表（S8） | `format_song_candidates` | `render_list(...)` |
| 歌曲出现列表（S8） | `format_song_lives` | `render_list(...)` |
| bindings 列表（S9） | 文本 | `render_list(绑定略缩, [(略缩, 事件名)])` |

- 每个列表图 footer 统一「回复序号」；会话 context 记录内容不变（用户仍按图内序号回复）。
- `format_*` 纯文本函数保留（dry-run / 回退文本歌单仍用），仅 live 发送路径改用图片。

## 3. 模块与验收

| 模块 | 职责 | 验收 |
|---|---|---|
| **S10.1 render_list** | `render_html_pages` / `build_list_html` / `render_list`；`render_setlist` 重构回归 | 列表 PNG 非空、序号 1..N、含 footer；长列表分页；`render_setlist` 既有单测全绿 |
| **S10.2 @only 门控** | `_handle` 未 @ 直接忽略；`_try_confirm` 仅 @ 调用 | 未 @ 消息（含带会话的普通闲聊）不触发任何动作；@ 了二次确认正常 |
| **S10.3 bot 集成** | 六类列表回复走 `render_list` 发图 | 测试群两段交互走通，列表为图片；失败回退纯文本歌单 |

## 4. 风险与对策

| 风险 | 对策 |
|---|---|
| 列表渲染延迟（Playwright 每次启动） | 复用 S4 管线/浏览器实例；短列表（如 bindings 很少）可回退文本 |
| @only 破坏两段式 | 二次确认同样要求 @bot（已含在门控设计内）；`songbot-usage.md` 写清「每轮都 @bot」 |
| setlist 渲染回归 | `render_setlist` 改共享管线后跑 S4 既有单测回归，不引入版式变化 |

## 5. 交付物

```
songbot/s4_render.py         # 泛化 + render_list + build_list_html + render_html_pages
songbot/bot.py               # _handle @only 门控 + 列表走 render_list
tests/test_s4_render.py      # 补 render_list 单测（序号/footer/分页/空行）
tests/test_s6_bot.py         # 补 @only 门控单测 + 列表图片化分支
docs/songbot-usage.md        # 更新：每轮都 @bot + 列表为图片
docs/modules/S10-list-image-atbot-worklog.md
```

## 6. 维护约定

- 完成后同步 `docs/index.md` §6 与 `docs/songbot-usage.md`。
- 契约无变更；若 `render_list` 需要新样式常量，与 S4 现有常量同文件维护。
