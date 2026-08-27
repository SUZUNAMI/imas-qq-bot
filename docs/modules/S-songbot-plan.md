# 子项目：歌曲列表 bot（songbot）— 实施计划

> 所属项目：爱马仕官方新闻 QQ 转发机器人（同一仓库下的**子项目**）。
> 目标：基于 https://imas-db.jp/song/event 的歌曲列表，实现群内 `@bot` 回复 Live 名字 → 给出子列表（day1/day2…）→ 用户再次确认名字 → 给出该公演的歌曲列表，并按**原网页格式渲染成图片**发送。
> 创建：2026-08-26；状态：计划已拍板，待执行 S1。

---

## 0. 已拍板决策（2026-08-26）

| 项 | 决策 |
|---|---|
| 代码放置 | **独立子目录 `songbot/`**（与 `src/` 平级），复用 `vendor/` 与配置模式 |
| 图片渲染 | **无头浏览器截图（高保真）**：驱动系统 Edge（`channel="msedge"`） |
| @bot 事件接收 | **OneBot 11 HTTP POST 上报 + 本地 HTTP 接收器**（NapCat 追加 `postUrls`） |
| 运行方式 | **独立进程**，与现有新闻推送 bot（`src/main.py`）并存 |
| 数据源 | `imas-db.jp/song/event`（服务端渲染 HTML，无 JSON API） |

---

## 1. 目标与范围

- **输入**：群内 `@bot <Live 名字>`（日文/英文/缩写皆可）。
- **处理**：模糊匹配事件索引 → 返回子列表（多日）或候选 → 用户二次确认公演名 → 抓详情页 → 渲染 setlist 图片。
- **输出**：以**图片**形式发送该公演的「セットリスト」（No. / 楽曲 / 演者 表格，还原原网页版式）。
- **非目标**：不做歌曲详情页、不做增量推送、不做多账号、不翻译歌词/歌名（专名保持原文）。

## 2. 现状调研结论（逆向，2026-08-26）

| 项 | 结论 |
|---|---|
| 协议 | 纯 **HTTP**（`https://` 连接失败）；服务端渲染，**无 JSON API**（`index.js` 仅品牌筛选 UI） |
| 列表页 `/song/event` | 单页 **125 个顶层事件**，按 `<h2>YYYY年</h2>` 分组（2013–2026，14 年）；事件 `<li data-brand-ids>`：单页事件直接一个 `<a>`，多日事件嵌套 `<ul><li>`（DAY1/DAY2… + 日期 `<small class="date">`） |
| 详情页 `xxx.html` | `h1#page_title` 标题、`div.m-2` 日期/场馆、出演者 `<ul>`、核心 `<table class="tracklist">`（表头 No./楽曲/演者；行内歌名 + 品牌徽章 `<small class="badge">`、演者 `<span class="idol-name">`） |
| 编码坑 | `Content-Type` 无 charset，PowerShell 默认解码乱码；须按**字节 UTF-8** 解码（Python httpx 显式 `.content.decode('utf-8')`） |

## 3. 交互流程

```mermaid
flowchart LR
  A[群内 @bot + Live名字] --> B[S3 模糊匹配事件索引]
  B -->|命中唯一| C[回: 事件名 + 子列表 DAY1/DAY2… + 日期]
  B -->|多个候选| C2[列出候选 让用户按序号选]
  C --> D[用户二次回复 DAY1 / 子标题 / 序号]
  C2 --> D
  D --> E[S2 抓详情页 → Setlist 结构化]
  E --> F[S4 无头浏览器截图渲染表格图片]
  F --> G[发图片到群]
```

- 多日事件走**两段式**（子列表 → 确认）；单页事件（无 day 拆分）直接渲染发送。
- 会话状态：`(group_id, user_id) → 待确认事件`，默认 5 分钟超时失效。

## 4. 目录结构

```
songbot/
  __init__.py
  models_song.py     # 数据契约 dataclass（Event/SubEvent/Track/Setlist，单一事实源）
  s1_fetch_events.py # 列表抓取+解析：/song/event → Event[]
  s2_fetch_setlist.py# 详情抓取+解析：xxx.html → Setlist
  s3_match.py        # 模糊匹配：Live 名字 → 候选 Event[]
  s4_render.py       # 无头浏览器截图：Setlist → PNG
  s5_receiver.py     # OneBot 事件接收（本地 HTTP）+ 会话状态
  bot.py             # 主控：接收 → 匹配 → 确认 → 渲染 → 发图（独立进程）
tests/
  test_s1_fetch_events.py
  test_s2_fetch_setlist.py
  test_s3_match.py
  test_s4_render.py
  test_s5_session.py
scripts/
  probe_song_event.py    # 探针：打印 125 事件 + 3 个 setlist
  acceptance_song.py     # 验收：两段交互 live 走通
docs/modules/
  S-songbot-plan.md      # 本文档
  S1-...-worklog.md …    # 各阶段工作日志
```

## 5. 模块与数据契约

### 5.1 数据契约（`songbot/models_song.py`）

```python
@dataclass SubEvent:          # 子公演（day1/day2…）
    title: str                # 显示名，如 "DAY1 -YAKUDOU-"
    full_title: str           # <a title> 完整标题
    url: str                  # 详情页 URL
    date: str                 # 日期文本，如 "2026/07/24(金)"

@dataclass Event:             # 顶层事件
    title: str                # 事件名
    year: str                 # "2026"
    brands: list[str]         # 品牌徽章名列表（可空）
    url: str                  # 单页事件详情 URL；多日事件为 "" 
    sub_events: list[SubEvent]  # 多日事件的子列表；单页为空 []

@dataclass Track:             # 歌曲行
    no: int
    title: str                # 歌名
    brand: Optional[str]      # 品牌徽章（无则 None）
    performers: list[str]     # 演者名列表
    link: Optional[str]       # /song/detail/N.html（无则 None）

@dataclass Setlist:           # 公演详情
    title: str                # h1 标题
    date_venue: str           # 日期/场馆行
    performers: list[str]     # 出演者列表
    tracks: list[Track]
    url: str
```

### 5.2 各模块职责与验收

| 阶段 | 职责 | 验收标准 |
|---|---|---|
| **S1 列表抓取+解析** | 抓 `/song/event`（UTF-8），BS4 解析出 `Event[]` | `scripts/probe_song_event.py` 打印 125 事件，多日事件 day 子项/日期/URL 正确 |
| **S2 详情抓取+解析** | 抓公演 `.html`，解析 `h1`/日期场馆/出演者/`table.tracklist` → `Setlist` | 3 个真实 URL 输出结构化 setlist 正确 |
| **S3 模糊匹配** | 归一化（去空白/大小写/全角→半角）后 substring + 模糊打分；返回唯一命中或候选列表 | 用 IWSF2026 / 13thLIVE / シャニ / 学園 等样本验证 |
| **S4 图片渲染** | 无头浏览器（Edge）截图还原表格版式，长表分页 | 生成 PNG 与网页版式一致、日文无缺字 |
| **S5 事件接收+会话** | 本地 HTTP 服务收 OneBot 群消息；会话状态（group+user → 待确认，超时失效） | 收到 @bot 消息并能回显 |
| **S6 主控串联+验收** | 串联 S1–S5；配置 NapCat `postUrls`；live 两段交互走通 | 测试群 @bot 完整走通 |
| **S7 文档/单测/挂载** | 补单测、工作日志、`docs/index.md`、后台挂载脚本 | 全仓测试通过，可常驻 |

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| Playwright 依赖（pip 被拦截） | 复用 `scripts/fetch_vendor_deps.py` 思路 vendor 化 wheel；用 `channel="msedge"` 驱动系统 Edge，免下载 Chromium |
| 站点仅 HTTP | 抓取用 httpx（明确 `http://`）；截图加载 `http://` 页面 |
| 编码（无 charset） | 一律 `bytes.decode('utf-8')`，不信任默认解码 |
| NapCat 配置改动 | 只追加 `postUrls`（事件上报），不动 3000 发送通道；与现有新闻 bot 并存 |
| 模糊匹配误命中 | 多候选时列候选让用户选，不静默猜 |
| 长 setlist 超图片高度 | 分页/加高截图高度，Pillow 自动裁白边 |

## 7. 维护约定

- 每阶段完成同步更新 `docs/index.md`（新增「子项目 songbot」小节）与本计划状态。
- 契约改动回改本文档 §5.1；模块统一 `from songbot.models_song import ...`，禁止重复定义同名 dataclass。
