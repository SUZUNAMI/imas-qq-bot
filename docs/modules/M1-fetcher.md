# M1 列表抓取（Fetcher）— 交接规格

> 项目：爱马仕官方新闻 QQ 转发机器人。追踪 https://idolmaster-official.jp/news，新新闻发布后推送「原文 + AI 日译中」到 QQ 群。
> 技术栈：Python 3.11+。
> 本文件**自包含**：只读本文件即可实现本模块，无需读其他模块文档。
> 契约冻结：输出结构必须严格符合本文件定义；如需改动，回改 `docs/module-specs.md` §1。

## 1. 本模块在流水线中的位置

```
M1 列表抓取 ──NewsItem[]──► M3 增量检测 ──► M2 详情解析 ──► M4 翻译 ──► M5 组装 ──► M6 推送 ──► QQ群
```

本模块是**全链路唯一阻塞点**，也是唯一需要探索的模块：负责从新闻列表页拿到「最新新闻列表」。

## 2. 输入契约

无（本模块不依赖上游数据，只依赖配置：轮询间隔等，可写死在默认值）。

## 3. 输出契约：`NewsItem[]`

输出为 `NewsItem` 列表，**最新在前**。字段名严格一致：

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class NewsItem:
    id: str                 # 唯一键，从 URL 末段提取，如 "01_17821"
    url: str                # 详情页完整 URL
    title: str              # 新闻标题（原文，日文）
    date: str               # "YYYY-MM-DD"
    thumbnail: Optional[str] = None  # 缩略图 URL，没有则 None
```

示例：

```json
{
  "id": "01_17821",
  "url": "https://idolmaster-official.jp/news/01_17821",
  "title": "【イベント】◯◯開催決定！",
  "date": "2026-08-06",
  "thumbnail": "https://idolmaster-official.jp/.../xxx.jpg"
}
```

## 4. 已知信息（探针结论，2026-08 实测）

- 页面是 **Next.js SPA**。`GET https://idolmaster-official.jp/news` 返回 HTML，但其中的 `<script id="__NEXT_DATA__">` 仅 **178 字节**，说明**列表数据不在服务端渲染**，由前端 JS 客户端拉取。
- **无 RSS**：`/news/feed` 返回 404。**无 sitemap**：`/sitemap.xml` 返回 404。
- 列表渲染组件在前端 chunk 中：`pages/news-*.js` 里用 `<NewsList category="NEWS">` 组件（React），数据接口待定位。
- 详情页（如 `/news/01_17821`）的 `__NEXT_DATA__` **有内容（约 6.7KB）**——这是 M2 模块的事，与本模块无关，仅佐证"数据源在别处"。

## 5. 两条实现路径（先 A，失败再 B）

### 路径 A：直连 JSON API（优先，最轻最稳）

> ✅ **已探明（2026-08-26 实测）**：路径 A 成功，无需再走 B。接口与请求方式（固化为代码注释 + `docs/modules/M1-fetcher-worklog.md` §3）：
> - Base：`https://cmsapi-frontend.idolmaster-official.jp/sitern/api/`
> - 先取 Token：`GET {base}cmsbase/Token/get` → `data.token`
> - 列表：`GET {base}idolmaster/Article/list?site=jp&ip=idolmaster&token=<t>&data=<urlencoded JSON>&limit=N&start=0`，`data={"category":["NEWS"],"subcategory":[],"brand":null}` → `data.article_list[]`（最新在前）
> - 配图：`GET {base}idolmaster/Image/get?path=<相对路径>`（须剥离 thumbnail 中的 `?_=` 缓存参数）
> - 映射：`id=path`、`url=https://idolmaster-official.jp/news/{path}`、`date=startdate(JST)→YYYY-MM-DD`、`thumbnail` 同上

1. 抓前端 JS chunk，找列表数据的请求地址：
   - 从 `/news` 页 HTML 拿到 `_next/static/chunks/pages/news-*.js` 及引用的 chunk（`6223-*.js`、`3391-*.js`、`9780-*.js` 等）。
   - 下载这些 chunk，正则搜 `https://`、`/api/`、`fetch(`、`axios` 等关键字，定位真实接口 URL（可能形如 `/api/news` 或第三方 CMS 域名）。
2. 直接 `httpx` 调该 JSON 接口，解析出条目列表，映射为 `NewsItem`。
3. 若接口需要特定 header（如 `Referer`、`Authorization`、`x-api-key`），在 chunk 里找对应值。

> 提示：若手动搜 chunk 慢，可用浏览器打开页面 → F12 → Network 面板，刷新后看 XHR/fetch 请求，最快定位接口。

### 路径 B：无头渲染（兜底，稳但重）

- 用 Playwright 打开 `https://idolmaster-official.jp/news`，`wait_for_selector` 等列表容器渲染完成，再从 DOM 提取条目（标题/链接/日期/缩略图）。
- 需要安装 Playwright + Chromium（约 300MB），比 A 慢、更耗资源，但抗 API 改版。

## 6. 实现约定

- 文件：`src/m1_fetcher.py`
- 入口函数签名：`def fetch_news_list() -> list[NewsItem]:`（扩展：`fetch_news_list(limit=20, brands=None)`，`brands` 为可选分企划白名单，默认不过滤，向后兼容）
- 分企划筛选（2026-08 追加）：官方 7 个 brand code 见 `m1_fetcher.BRAND_CODES`（IDOLMASTER/CINDERELLAGIRLS/MILLIONLIVE/SIDEM/SHINYCOLORS/GAKUEN/OTHER）。**服务端 data.brand 不真正过滤**，须客户端筛选；匹配语义「任一 brand ∈ 白名单即保留」（跨企划合作新闻多 brand）。
- 请求库：`httpx`（带 `User-Agent`、超时、连接失败重试 3 次指数退避）；路径 B 用 `playwright`。
- 列表解析：`BeautifulSoup` 或直接 JSON 解析。
- 只抓首页即可（如首页不完整，抓前 2 页合并去重）。

## 7. 验收标准

1. 运行入口函数，能打印出**最近 10 条** `NewsItem`，标题/URL/日期与页面一致。
2. `id` 能从 URL 正确提取（末段，如 `01_17821`）。
3. 连续跑两次，结果稳定（无随机乱序、无重复）。
4. 网络异常时能重试并给出明确报错，不静默返回空列表。

## 8. 边界与注意事项

- 本站可能要求特定 `User-Agent` 或 `Accept-Language: ja`，必要时设置。
- 不要抓历史全部（只最新 10–20 条即可），避免被限流。
- 若 A、B 都行，**优先 A**（直连 JSON API 更省资源、更适合 Windows Server 常驻）。
- 定位到的接口 URL 和请求方式，务必在代码注释里写清楚，供后续维护。
