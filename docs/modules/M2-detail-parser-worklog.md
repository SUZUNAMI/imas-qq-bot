# M2 详情解析（DetailParser）— 执行工作日志

> 线程：M2（本线程）；项目：爱马仕官方新闻 QQ 转发机器人
> 契约：以 `docs/modules/M2-detail-parser.md` 与 `docs/module-specs.md` §1 为准（冻结）。
> 创建：2026-08-26；状态：✅ 已完成（`__NEXT_DATA__` 路径打通并验收）

---

## 1. 执行计划（2026-08-26）

| 步骤 | 内容 | 验收 | 状态 |
|---|---|---|---|
| S1 探针 | 抓真实详情页 dump `__NEXT_DATA__` 结构；对比正文→纯文本方案；实测图片 URL 形态；定位 fallback 容器 | 字段路径 / 正文方案 / 图片形态 / fallback 容器全部固化 | ✅ |
| S2 实现 | `src/m2_parser.py`：`NewsDetail` dataclass + `parse_detail()`（httpx、UA、超时、重试 3 次；`__NEXT_DATA__` 主路径 + HTML fallback；正文纯文本保留段落；图片去重上限 4；异常明确报错） | 入口签名符合契约 | ✅ |
| S3 单测 | `tests/test_m2_parser.py` 纯逻辑单测（无网络） | **30/30 通过** | ✅ |
| S4 验收 | `scripts/acceptance_m2.py`：3 个真实 URL 输出与页面一致；正文无标签且段落 `\n\n`；图片 URL 全部 200；断网抛 `ParseError` | **全部通过（[ALL PASS]）** | ✅ |
| S5 文档回写 | 本日志 + `docs/index.md` + `module-specs.md` §1.2 注明 + M2 规格探针结论固化 | 本文档 + 索引已更新 | ✅ |

## 2. 探针结论（S1，2026-08-26 实测固化）★

详情页是 Next.js SSR 页面，HTML 内 `<script id="__NEXT_DATA__" type="application/json">`（约 5–7KB）含文章数据，**免无头浏览器**：

| 项 | 值 |
|---|---|
| 数据路径 | `__NEXT_DATA__` JSON → `props.pageProps.data`（dict，46 个 key） |
| 标题 | `data.title`（原文日文） |
| 日期 | `data.startdate`（Unix 秒，JST）→ `YYYY-MM-DD`；兜底 `data.dspdate`（"YYYY/MM/DD HH:mm"） |
| id | `data.path`（如 `01_19692`，与 `NewsItem.id` 一致） |
| 正文 | `data.content`（HTML，约 2–6KB）：文本在 `.c-txt` 内、段落由 `<br><br>` 分隔、配图在 `data-type="component-photo"` 内 |
| 配图清单 | `data.use_image`：`[{path, filename}, …]`（**可达 14+ 张**，需按契约截 4 张） |
| 配图 URL | content 内 `<img src>` 为相对路径（如 `/idolmaster/jp/article/…`）；**直连 `idolmaster-official.jp` 同路径 404**，必须转 `https://cmsapi-frontend.idolmaster-official.jp/sitern/api/idolmaster/Image/get?path=<相对路径>`（200 image/jpeg，与 M1 缩略图同法） |

**正文 → 纯文本（方案 B 胜出）**：`<br>`→`\n`、块级标签（div/p/h1-h6/li/…）边界插 `\n`、`get_text()` 剥离标签、`&nbsp;`/全角空格归一、行 strip、连续空行压缩为段落分隔 `\n\n`。对比方案 A（仅 `get_text()`）会把块级标题（如「商品情報」）与下一段拼接，故弃用。

**fallback（规格 §8）**：`__NEXT_DATA__` 缺失/损坏时直接解析 HTML 正文容器，选择器 `.c-gallery` → `.c-txt`（实测前者为正文区，文本 660 字符）；标题兜底链 `meta[og:title]` → `<title>`（去「 | 站点」后缀）→ `NewsItem.title`；日期兜底 `NewsItem.date`（与列表同一 startdate 数据源）。

> 探针脚本：`.tmp/probe_m2.py` / `probe_m2_content.py` / `probe_m2_text.py` / `probe_m2_fallback.py`；完整 JSON 存档 `.tmp/next_data_dump.json`。

## 3. 验收记录（S4，2026-08-26）

- 3 个真实详情 URL（`01_19692` / `01_19693` / `01_19723`）：标题/日期与列表一致（`assert title == item.title`、`date == item.date` 全过）。
- `body_text`：673 / 695 / 481 字符，无 `<`/`>` 标签，段落 `\n\n` 分隔，首段正确。
- `images`：3 / 2 / 4 张（上限 4），每张 GET 200 `image/jpeg`（CMS Image/get 形态）。
- 断网/无效域名：重试 3 次后抛 `ParseError`（明确报错，不静默空数据）。
- `python -m unittest discover -s tests -p "test_m2_parser.py" -v`：**30/30 通过**（纯逻辑无网络）。

## 4. 可拓展性设计（站点改版应对）

1. **集中常量**：`__NEXT_DATA__` 数据路径 `NEXT_DATA_DATA_PATH`、字段名 `DATA_FIELD_*`、fallback 选择器 `FALLBACK_BODY_SELECTORS`、正文块级标签集 `_HTML_BLOCK_TAGS` 全部为模块顶部常量——站点改版先改常量，不动解析逻辑。
2. **分层纯函数**：网络（`_fetch_html`）→ 提取（`_extract_next_data` / `_get_data_node`）→ 映射（`_build_detail`）→ 回退（`_fallback_detail`）彼此独立，可单测、可替换。
3. **双路径容错**：`__NEXT_DATA__` 主路径 + HTML fallback；标题四级兜底（页面 → og:title → `<title>` → NewsItem.title）；正文空允许（纯图新闻）。
4. **契约不变**：只实现 `parse_detail(item: NewsItem) -> NewsDetail`；`images` 为可访问的绝对 URL（CMS Image/get 形态），符合验收标准 3。
5. **共享契约（并发约定）**：M2 开发期间 M3/M4 线程并行完成并新增 `src/models.py`（§1.1–§1.5 契约类型单一事实源）。M2 已改为从 `models.py` 导入 `NewsItem` / `NewsDetail`（删除模块内重复定义），与 M1/M3/M4 一致，集成时 `isinstance` / `==` 互通。

## 5. 结论

- **M2 完成**：`__NEXT_DATA__` 直解析（免浏览器），轻量稳定，适合 Windows Server 常驻。
- 交付物：`src/m2_parser.py`（契约签名 `parse_detail(item) -> NewsDetail`）、`tests/test_m2_parser.py`（30/30）、`scripts/acceptance_m2.py`。
- 后续交接：M4 翻译直接消费 `NewsDetail`（title/date/body_text/images）。
- 环境备注：本沙箱**允许 mkdir 但拒绝删除**（rmdir WinError 5）——M3 线程的 tempfile 清理失败源于此，与本模块无关；M2 无临时目录依赖。
