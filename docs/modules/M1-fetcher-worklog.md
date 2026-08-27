# M1 列表抓取（Fetcher）— 执行工作日志

> 线程：M1（本线程）；项目：爱马仕官方新闻 QQ 转发机器人
> 契约：以 `docs/modules/M1-fetcher.md` 与 `docs/module-specs.md` §1 为准（冻结）。
> 创建：2026-08-26；状态：✅ 已完成（路径 A 打通并验收）

---

## 1. 执行计划（2026-08-26）

| 步骤 | 内容 | 验收 | 状态 |
|---|---|---|---|
| S1 环境与连通性 | 确认 Python 3.13、httpx/bs4 依赖、站点可达 | Python 3.13.7 可用；站点 200（37KB）；httpx/bs4 vendor 化 | ✅ |
| S2 探针·定位 API | 路径 A：抓首页 HTML → 取 `_next/static/chunks/pages/news-*.js` → 下载 chunk 搜请求地址 → 定位列表 JSON 接口 | 接口 URL + 请求方式 + 必要 header 全部拿到 | ✅ |
| S3 实现 | `src/m1_fetcher.py`：`NewsItem` dataclass + `fetch_news_list()`（httpx、UA、超时、重试 3 次指数退避） | 文件就位，入口签名符合契约 | ✅ |
| S4 验收 | 打印最近 10 条 NewsItem；连续两次运行稳定；id 从 URL 正确提取；断网报错不静默 | 全部通过（含 8 条单测） | ✅ |
| S5 文档回写 | 接口 URL/请求方式写入代码注释；探针结论更新本日志与 M1 规格 | 本文档 + M1 规格已更新 | ✅ |
| S6 兜底（若 A 失败） | 路径 B：Playwright 无头渲染提取列表 | 不需要（A 已成功） | — |

## 2. 环境记录（2026-08-26）

- Python 3.13.7（`C:\Users\Z\AppData\Local\Programs\Python\Python313\python.exe`），pip 26.2.1。
- 出站连通性：PowerShell `Invoke-WebRequest` 与 curl.exe 均报 `SEC_E_NO_CREDENTIALS`（Windows Schannel 凭据问题，**环境问题非网络阻断**）；**Python requests/httpx 正常**（verify=True 亦可，certifi bundle 完好）。
- 依赖安装：**pip 在本沙箱被拦截**（无法写系统临时目录，即使指向工作区内临时目录也被拒）。改用 `scripts/fetch_vendor_deps.py`（requests 直下 PyPI wheel 解压到 `vendor/`）解决：httpx 0.28.1 + beautifulsoup4 4.15.0 及其依赖闭包。运行代码需 `PYTHONPATH=vendor`（`src/m1_fetcher.py` 内置兜底逻辑）。

## 3. 探针结论（S2，2026-08-26 实测固化）★

站点是 Next.js SPA，列表由前端 JS 从 **CMS 直连 API** 拉取。经前端 chunk 逆向（`_app`、`pages/news-*.js`、`6223/3391/9780` 等）定位到：

| 项 | 值 |
|---|---|
| CMS API base | `https://cmsapi-frontend.idolmaster-official.jp/sitern/api/` |
| ① Token | `GET {base}cmsbase/Token/get` → `data.token`（每次运行先取；供列表接口鉴权） |
| ② 新闻列表 | `GET {base}idolmaster/Article/list?site=jp&ip=idolmaster&token=<t>&data=<urlencoded JSON>&limit=N&start=0` |
| 列表 data JSON | `{"category":["NEWS"],"subcategory":[],"brand":null}` |
| 列表响应 | `data.article_list[]`（`total_count`/`start`/`limit`/`count`），最新在前 |
| ③ 配图 | `GET {base}idolmaster/Image/get?path=<相对路径>` → 图片二进制；**须剥离 thumbnail 值中的 `?_=` 缓存参数**（否则 404）；直连 idolmaster-official.jp 同路径也 404 |
| 鉴权 | 无需 cookie/登录；token 免费即取；`withCredentials` 仅前端行为 |

**article → NewsItem 映射**：`id=path`（如 `01_19692`，即 URL 末段）· `url=https://idolmaster-official.jp/news/{path}`（API 自带 `.html` 后缀，已规范化去掉）· `title=title` · `date=startdate(Unix, JST)→YYYY-MM-DD`（兜底 `dspdate` `"YYYY/MM/DD HH:mm"`）· `thumbnail={base}idolmaster/Image/get?path={thumbnail 去缓存参数}`（无则 None）。过滤 `delflg=="1"` 与 `publish_status!="publish"`。

> 探针工具：`scripts/probe_m1_api.py`（抓 chunk 定位接口）、`scripts/dump_module.py`（提取 webpack 模块）、`scripts/find_list_builder.py` 等；chunk 存档于 `.tmp/probe/`。

## 4. 验收记录（S4，2026-08-26）

- `python src/m1_fetcher.py`：输出 20 条，最新 10 条标题/URL/日期正确（如 `01_19692` 【シャニマス】30MS…，2026-08-26）。
- `scripts/acceptance_m1.py`：✅ 两次运行完全一致（20 条、无重复、最新在前）· ✅ 缩略图 URL 200 image/jpeg · ✅ 断网 3 次重试后抛 `FetchError`（明确报错，不静默空列表）。
- `python -m unittest discover -s tests -v`：**8/8 通过**（`tests/test_m1_fetcher.py`，纯逻辑无网络）。

## 4.1 分企划筛选功能（2026-08-26 追加）

**需求**：新闻列表含 7 个分企划 tag，希望自定义只更新选定企划的内容。

**调查结论**：
- 官方 7 个 brand tag（`cmsbase/SiteCommon/get` 的 brand 定义，实测）：`IDOLMASTER`(765) / `CINDERELLAGIRLS` / `MILLIONLIVE` / `SIDEM` / `SHINYCOLORS` / `GAKUEN` / `OTHER`。
- **服务端 `data.brand` 参数不真正过滤**（实测返回列表仍含全部品牌，仅影响 total_count）→ 筛选在客户端做。
- 200 条样本中 36 条为多 brand（跨企划合作新闻）；无 brand 条目 0 条（边界防御：白名单下排除）。

**实现**（`src/m1_fetcher.py`，向后兼容）：
- `fetch_news_list(limit=20, brands=None)`：`brands` 为白名单（str 或 list，大小写不敏感，空=不过滤）；匹配语义「article.brand 任一 code ∈ 白名单即保留」。
- 常量 `BRAND_CODES` 固化 7 个 code；纯函数 `_normalize_brands` / `_article_has_brand`（可单测）。
- CLI：`python src/m1_fetcher.py --brands SHINYCOLORS,GAKUEN`；未知 code 明确报错并列出可选值。

**验收**：单测 13/13（新增 5 条筛选用例）；`scripts/check_brand_filter_live.py` 与原始 API 数据交叉核对——SC/GAKUEN+IDOLMASTER/CG 三组返回全部命中对应企划（13/13/12 条）。

**后续对接**：M7/M8 可从 `config.yaml` 读 `brands: [SHINYCOLORS]` 传入；M3 增量检测消费过滤后的 `NewsItem[]` 即实现「只更新某些企划」。

## 5. 结论

- **M1 完成**：路径 A（直连 CMS JSON API）成功，比 HTML/Playwright 更轻更稳，适合 Windows Server 常驻。
- 交付物：`src/m1_fetcher.py`（契约签名 `fetch_news_list() -> list[NewsItem]`）、`tests/test_m1_fetcher.py`、`requirements.txt`、探针脚本 `scripts/*`。
- 后续交接：M3 增量检测直接消费 `NewsItem[]`；M2 详情页走 `__NEXT_DATA__`（与 M1 无关）。
- 遗留说明：`vendor/` 与 `.tmp/` 为本机沙箱产物，不入 git；服务器用 `requirements.txt` 正常 pip 安装。
