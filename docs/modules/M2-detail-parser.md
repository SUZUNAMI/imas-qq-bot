# M2 详情解析（DetailParser）— 交接规格

> 项目：爱马仕官方新闻 QQ 转发机器人。追踪 https://idolmaster-official.jp/news，新新闻发布后推送「原文 + AI 日译中」到 QQ 群。
> 技术栈：Python 3.11+。
> 本文件**自包含**：只读本文件即可实现本模块，无需读其他模块文档。
> 契约冻结：输入/输出结构必须严格符合本文件定义；如需改动，回改 `docs/module-specs.md` §1。

## 1. 本模块在流水线中的位置

```
M1 列表抓取 ──► M3 增量检测 ──► M2 详情解析 ──NewsDetail──► M4 翻译 ──► M5 组装 ──► M6 推送
```

本模块：拿到一条新闻的 URL，抓详情页，提取标题/日期/正文/配图，供后续翻译。

## 2. 输入契约：`NewsItem`

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class NewsItem:
    id: str                 # 唯一键，如 "01_17821"
    url: str                # 详情页完整 URL
    title: str
    date: str               # "YYYY-MM-DD"
    thumbnail: Optional[str] = None
```

本模块只用到 `url`（也可用 `title`/`date` 做校验兜底）。

## 3. 输出契约：`NewsDetail`

```python
from dataclasses import dataclass

@dataclass
class NewsDetail:
    id: str          # 与 NewsItem.id 一致
    url: str
    title: str       # 标题（原文日文）
    date: str        # "YYYY-MM-DD"
    body_text: str   # 正文纯文本，保留段落换行（用 "\n\n" 分隔段落）
    images: list[str]  # 正文配图 URL 数组，可为空 []；建议上限 4 张
```

示例：

```json
{
  "id": "01_17821",
  "url": "https://idolmaster-official.jp/news/01_17821",
  "title": "【イベント】◯◯開催決定！",
  "date": "2026-08-06",
  "body_text": "第一段内容…\n\n第二段内容…",
  "images": ["https://idolmaster-official.jp/.../a.jpg"]
}
```

## 4. 已知信息（探针结论，2026-08-26 实测固化）★

- 详情页是 Next.js SSR 页面，`GET https://idolmaster-official.jp/news/01_17821` 返回的 HTML 内 `<script id="__NEXT_DATA__" type="application/json">…</script>` **包含文章数据（约 5–7KB）**。
- 因此**无需无头浏览器**：直接从 `__NEXT_DATA__` 的 JSON 里取标题/日期/正文即可。
- **数据路径**：`__NEXT_DATA__` JSON → `props.pageProps.data`（dict，46 个 key）：
  - 标题 `data.title`；日期 `data.startdate`（Unix 秒 JST → `YYYY-MM-DD`，兜底 `data.dspdate` "YYYY/MM/DD HH:mm"）；id `data.path`。
  - 正文 `data.content`（HTML，约 2–6KB）：文本在 `.c-txt` 内、段落由 `<br><br>` 分隔、配图在 `data-type="component-photo"` 内。
  - 配图清单 `data.use_image`：`[{path, filename}, …]`（可达 14+ 张，须截 4 张）。
- 正文在 JSON 里带 HTML 标签，需转纯文本并保留段落换行（方案 B：`<br>`→`\n` + 块级标签边界换行 + 去标签 + 空行压缩为 `\n\n`）。
- **配图 URL**：content 内 `<img src>` 为相对路径，**直连 `idolmaster-official.jp` 同路径 404**，须转 `https://cmsapi-frontend.idolmaster-official.jp/sitern/api/idolmaster/Image/get?path=<相对路径>`（与 M1 缩略图同法，200 image/jpeg）。
- **fallback**：`__NEXT_DATA__` 缺失/损坏时回退直接解析 HTML 正文容器（选择器 `.c-gallery` → `.c-txt`）；标题兜底链 `meta[og:title]` → `<title>`（去站点后缀）→ `NewsItem.title`；日期兜底 `NewsItem.date`。

## 5. 技术要点

1. `httpx` 抓详情页（带 `User-Agent`，重试 3 次）。
2. 用正则提取 `<script id="__NEXT_DATA__" type="application/json">(.*?)</script>`，`json.loads` 解析。
3. 在解析出的 JSON 结构里定位：标题、发布日期、正文、正文图片 URL。字段名以实际 JSON 为准（实现时先打印 JSON 结构再定位）。
4. 正文 HTML → 纯文本：
   - 可用 `BeautifulSoup` 的 `.get_text()`，或手动把 `<br>`/`</p>` 换成 `\n`，再去掉所有标签。
   - 保留段落间隔（连续段落用 `\n\n`）。
5. 图片：提取正文中 `<img src>`（或 JSON 里的图片字段），绝对化 URL（相对路径补 `https://idolmaster-official.jp` 前缀），去重，上限 4 张。

## 6. 实现约定

- 文件：`src/m2_parser.py`
- 入口函数签名：`def parse_detail(item: NewsItem) -> NewsDetail:`
- 依赖：`httpx`、`beautifulsoup4`。
- 类型：`NewsItem` / `NewsDetail` 一律从 `src/models.py` 导入（契约单一事实源，禁止模块内重复定义）。

## 7. 验收标准

1. 给定 3 个真实详情 URL（如 `/news/01_17821` 等），输出 `NewsDetail` 的标题/日期/正文与页面一致。
2. `body_text` 为纯文本、无 HTML 标签、段落间有 `\n\n`。
3. `images` 数组 URL 可直接访问（绝对路径）。
4. 详情页结构异常时抛明确错误，不返回半截数据。

## 8. 边界与注意事项

- 部分详情页可能正文为空（如纯图新闻），此时 `body_text` 可为空串，但 `title`/`date` 必须正确。
- 图片 URL 可能是懒加载（`data-src` 而非 `src`），注意兼容。
- 若 `__NEXT_DATA__` 里找不到正文，回退到直接解析 HTML 的正文容器（`BeautifulSoup`）。
