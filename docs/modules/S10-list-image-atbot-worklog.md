# S10 工作日志：列表图片渲染 + @only 门控

> 所属：songbot 子项目（S1–S9 已完成并 live 验收通过）。
> 计划：`docs/S10-list-image-atbot-plan.md`（2026-08-27 拍板）。
> 完成：2026-08-27。验收：S4 33/33（含 S10 新增 15 项）、S6 105/105（含 S10 新增 14 项）、
> 全仓 361/361 单测全绿；`acceptance_song.py` 离线全链路 ALL PASS（mock 渲染 + **真实 Edge 渲染**双模式）；
> 追加修复：列表分页改先量高（长标题发送慢，见 §8）。

---

## 1. 目标回顾（计划 §0 已拍板）

1. **列表图片化**：候选 / 子列表 / 时间筛选 / 歌曲出现 / bindings 等「序号 + 名称 + 日期」列表
   统一走 `render_list` 发图，图内附「回复序号」footer。
2. **@only 门控**：未 `at_bot` 的消息**一律忽略**（不做二次确认）；会话二次确认也要求 @bot。

## 2. S10.1 列表渲染（`songbot/s4_render.py` 泛化）

- **共享管线 `render_html_pages(pages_html, out_dir, *, slug="page", est_heights=None)`**：
  原 `_render_playwright_pages` / `_render_cli_pages` 的「逐页截图 + 裁白边」核心抽出，
  改为接受**已预分页的 HTML 字符串列表**（每页须含 `#render-root` 容器）；
  playwright 首选（单次浏览器会话逐页元素级截图），失败回退 Edge CLI + Pillow 裁白边；
  `est_heights` 供 CLI 兜底窗口高度（缺省宽松默认 `MAX_PAGE_HEIGHT+800`）。
- **`render_setlist` 重构**：`_build_setlist_pages`（playwright 量高或估算行高 → `_chunk_tracks`
  → `build_html` 分页）→ `render_html_pages`，**行为不变**（S4 既有 18/18 单测回归全绿，
  文件名仍 `{标题 slug}_{NN}.png`）。
- **`build_list_html(title, rows, *, hint="回复序号")`**：列表自包含 HTML——标题 + 序号行
  「主文本 + 副文本（日期/品牌，弱化色 `.sub`）」+ footer 提示；样式沿用 setlist 模板
  （同字体/标题/宽度/白底，`_LIST_CSS` 独立常量，深色/浅色指徽章文字对比度语义，无暗色主题）。
- **`render_list(title, rows, *, out_dir=None, hint="回复序号", slug=None)`**：
  估算行高（`HEADER_HEIGHT_EST + n*ROW_HEIGHT_EST`）→ `_chunk_rows` 分页 → `build_list_html`
  → `render_html_pages`；`rows` 空返回 `[]`；文件名 `标题 slug + 内容短哈希`（`_content_hash`，
  防「找到多个匹配，请选择」等同标题列表互相覆盖）。

## 3. S10.2 @only 门控（`songbot/bot.py` `_handle`）

- `_handle` 首行 `if not text or not inc.at_bot: return`——未 @bot 的消息一律忽略；
  **删除**原「有会话但无 @ → 二次确认/提示」路径（`_try_confirm` 现在只在 at_bot 时被调用）。
- `quit` 分支简化：门控已保证 at_bot；「有会话 → 已取消 / 无会话 → 当前没有进行中的查询」。
- 会话 TTL、`split_command` 分流、回落第一段等其余逻辑不变。

## 4. S10.3 bot 集成列表图片化（`songbot/bot.py`）

- 新增可注入依赖 `list_renderer`（默认 `render_list`，与既有 DI 风格一致）；
  新增 `_send_list(group, user, title, rows, text_fallback, *, hint="回复序号")`：
  `render_list` → 发图（带 @ 归属），渲染失败 / 空结果 / 发送失败（含 NapCat 假失败送达确认）
  → 回退纯文本 `text_fallback`（`format_*` 产物，保留确认提示）。
- 八处发送点改造（六类场景 + 两处会话内重列）：

  | 场景 | 标题 | rows（主文本, 副文本） |
  |---|---|---|
  | 时间筛选月列表 | `{年月} 的 LIVE（共 N 场）` | (事件名, 日期/多日子项) `_event_list_rows` |
  | 多候选列表 | `找到多个匹配，请选择` | 同上 |
  | 多日子列表 | 事件名 | (子公演标题, 日期) |
  | 歌曲候选列表（S8） | `找到多首候选歌曲，请选择` | (歌名, N 场 LIVE) |
  | 歌曲出现列表（S8） | `「歌名」出现在 N 场 LIVE` | (事件名+子公演, 日期) |
  | bindings 列表（S9） | `全部绑定（N 条）` | (略缩, 事件名) |
  | 候选内仍多义重列（`_try_confirm`） | `还是没唯一确定，这些候选` | 同候选场景 |
  | 候选歌内仍多义重列（`_try_confirm`） | `还是没唯一确定，这些候选` | 同候选歌场景 |

- 每张列表图 footer 统一「回复序号」（计划拍板）；`format_*` 纯文本函数**保留**
  （dry-run / 图片失败回退文本用）。
- **决策记录**：时间筛选/候选列表**全部事件进图**（不再按 `reply_limit` 截断 10 条）——
  图片可分页，且会话确认序号（`len(cands)`）与图内序号一致，避免「图里只有 10 条、
  回 11 却提示超出范围」的矛盾；截断仅保留在纯文本 fallback（与旧行为一致）。

## 5. 测试与验收

- `tests/test_s4_render.py`：新增 `BuildListHtmlTest`（结构/序号自增/转义/空行/footer/
  自定义 hint/无副文本）、`RenderListTest`（空行返回 []/长列表分页不超阈值/量高失败回退估算/
  内容哈希/共享管线空输入）、`RenderListBrowserTest`（真实渲染：单页/分页/输出目录/非空白 PNG/
  文件名 slug+哈希）；S4 33/33。
- `tests/test_s6_bot.py`：`_make_bot`/`_song_bot` 注入 mock `list_renderer`
  （`bot.list_render_calls` 观测 title/rows/hint）；全部二次确认用例改 at_bot=True；
  新增 `TestAtOnlyGating`（未 @ 闲聊/序号/DAY1/歌曲序号/quit 一律忽略且会话保留、
  @ 后确认正常）、`TestListImageS10`（列表渲染失败/发送失败/假失败已送达/发送异常兜底、
  `_event_list_rows` 全量不截断）；S6 105/105。
- `scripts/acceptance_song.py`：对齐 S10——列表断言改走 `list_render_calls`、二次确认改 @bot、
  HTTP 端到端两条 POST 均带 @；`--real-render` 用真实 `render_list` 出图。
  离线全链路 **ALL PASS**（mock 与真实 Edge 双模式）。
- 全仓 `python -m unittest discover -s tests -p "test_*.py"` → **361/361**。

## 6. 文档同步

- `docs/songbot-usage.md`：每轮都需 @bot（含二次确认/quit）、列表回复均为图片、
  footer「回复序号」、全部事件进图不截断、失败回退纯文本；典型对话示例更新。
- `docs/index.md` §6：S10 状态 ✅、新增本工作日志与交付物清单。
- 契约无变更（S10 只新增 `render_list`/`build_list_html`/`render_html_pages` 三个渲染函数
  与 `SongBot.list_renderer` 注入点；`_LIST_CSS` 样式常量与 S4 现有常量同文件维护）。

## 7. 遗留说明

- 列表渲染延迟：与 setlist 共用 `render_html_pages`（playwright 单会话复用），
  每次列表回复约 1–3s（浏览器启动）；短列表（如 bindings 很少时）仍走图片
  （计划拍板统一），如需回退文本可调 `_send_list` 的阈值策略（未做，当前无此需求）。
- live 验收：本机 NapCat 当前未常驻，`--live` 待用户下次群内联调时执行
  （验收步骤已更新：每轮回复都需 @bot）。

## 8. 追加修复：列表图片特别慢（2026-08-27 live 反馈）

- **现象**：列表图片能发出但「特别慢」（用户先报「发不出去」，实为发送耗时过长）。
- **根因**：`render_list` 分页原本只用**估算行高**（`ROW_HEIGHT_EST=36px`/行），但列表主文本
  是长事件名（如 IWSF 全名），flex 容器内**换行成 2–3 行**，实际行高约为估算的 **1.4x**——
  实测 120 行长标题：估算 2 页、每页实际高 **6422px**（2x 缩放后 PNG 高 **12844px**、数 MB），
  截图（整页元素截图）+ base64 编码 + NapCat 上传全部变慢。setlist 无此问题（先量高再分页）。
- **修复**：`_build_list_pages` 改为**优先 playwright 量高**（`_measure_html_height`，与
  `_build_setlist_pages` 一致）再按行比例分页；量高失败（playwright 不可用）才回退估算行高。
  新增 `measured_height` 参数便于离线单测注入。
- **验证**：120 行长标题修复后 3 页、每页 PNG 高 ≈6114px（≈3000px 阈值 ×2，旧值 12844px）、
  渲染 3.1s；`tests/test_s4_render.py` 33/33（+`test_measure_fallback_estimates_when_unavailable`）、
  S6 105/105、**全仓 361/361**；`acceptance_song.py` mock + 真实 Edge 双模式 ALL PASS。
