# S2 工作日志：详情抓取 + 解析（s2_fetch_setlist）

> 所属：子项目 songbot（歌曲列表 bot） · 施工图：`docs/S1-S7-taskplan.md` §S2
> 日期：2026-08-27 · 状态：✅ 完成（单测 40/40 通过；离线 fixture 与 live 3 URL 双验收一致）

## 1. 目标

抓公演详情页（xxx.html），解析出结构化 `Setlist`（标题 / 日期场馆 / 出演者 / セットリスト），
供 S4 渲染与 S6 主控消费。契约为 `songbot/models_song.py` 的 `Track` / `Setlist`
（S1 阶段已提前冻结）。

## 2. 交付物

| 文件 | 说明 |
|---|---|
| `songbot/s2_fetch_setlist.py` | 抓取 + 解析：`fetch_setlist(url, *, client=None) -> Setlist`；纯函数 `parse_setlist_html(html, *, url="", base_url=...)` 供离线单测；请求层（`_request`/`FetchError`/UA/超时）**复用 s1_fetch_events**（单一事实源，避免重试逻辑漂移） |
| `tests/test_s2_fetch_setlist.py` | 40 个单测（unittest，全离线）：三版式解析 + 防御路径 + 抓取层 MockTransport |
| `fixtures/imas_db_million_13th_day1.html` | **新抓**真实详情页（13thLIVE DAY1，版式 B），2026-08-27 抓取 |
| `fixtures/imas_db_cg_musical_dd.html` | **新抓**真实详情页（DERE of the DEAD 音乐剧，版式 C），2026-08-27 抓取 |
| `scripts/probe_song_event.py` | 启用 `--setlist <url>`（live）与 `--setlist-local <html>`（离线）；`--json` 亦可输出 Setlist；强制 stdout UTF-8（Windows 控制台 GBK 无法编码「・」等字符） |

## 3. 关键发现：站点详情页有**三种版式**（计划 §S2 原按单一 IWSF 样本写的选择器必须泛化）

| 版式 | 样本 | 日期场馆位置 | 出演者位置 | tracklist 特征 |
|---|---|---|---|---|
| A | `imas_db_iwsf_day1.html` | `div.m-2`（含 開場/開演 + `<a>詳細</a>`） | `div.m-2`「出演アイドル:」 | 全行有序号；部分歌带 `/song/detail/N.html` 链接与品牌徽章 |
| B | `imas_db_million_13th_day1.html` | `<p>`（含 開場/開演 + `<a>詳細</a>`） | `div.my-2`「出演:」 | **无徽章**（brand=None）；歌名后可能有 `<small class="notes">(新曲)</small>`（非徽章，保留在标题） |
| C | `imas_db_cg_musical_dd.html`（音乐剧） | 公演概要节内 `div.mx-3 my-2`（含 `<a>詳細</a>`）；**另有一张公演日程表**（DAY1/DAY2 昼/夜 的 開場/開演 td，不能误当） | `div.mx-3 my-2`「出演:」 | 有 `<tr class="part-header"><th colspan="3">【第X幕 …】</th>` 幕标题行（<3 个 td，跳过）；有**无序号行**（td0 空 → no 回退运行序号）；有断号行（第二幕从 1 重新编号、卡门 20、谢幕 15——忠实保留原页编号） |

## 4. 实现要点（泛化规则）

- **日期场馆**：首选 `<a>詳細</a>`（官方公式サイト链接，每页唯一、总在日期/场馆行）所在 div/p 祖先，
  去掉 `<a>` 后取文本；无「詳細」链接时兜底取含「開演/開場」的**最短** div/p（最短防外层大容器误匹配）。
  → 三版式统一命中正确行（含 C 版式正确避开公演日程表单元格）。
- **出演者**：首个文本以「出演」开头的 div 内全部 `span.idol-name` 文本（覆盖 出演アイドル/出演 两种前缀）。
- **歌曲行**：`table.tracklist > tbody > tr`；不足 3 个 td 的行跳过（幕标题行/坏行）；
  td0 空/非数字 → `no` 回退为运行序号（len+1）；td1 有 `<a>` 取链接文本 + `urljoin` 绝对化，
  否则去掉 badge/`visually-hidden`（( ) 装饰）后取文本；td2 取 idol-name span 文本，无 span（「全員」/
  「城主(穴沢裕介)」）取整格文本。
- **bs4 4.15 坑（同 S1）**：克隆元素一律「序列化 → 重解析」建立独立树，不用 `copy.deepcopy(Tag)`。
- **请求层复用**：`from songbot.s1_fetch_events import _request, FetchError, DEFAULT_HEADERS, REQUEST_TIMEOUT`，
  重试/异常/UA 与 S1 完全一致；单测里 mock `s1_fetch_events.RETRY_ATTEMPTS` 仍生效（`_request` 读 s1 模块全局）。
- **探针编码**：`sys.stdout.reconfigure(encoding="utf-8")`，否则 Windows 控制台 GBK 对「・」(U+30FB) 抛
  `UnicodeEncodeError`（S1 探针没遇到是因为当时的标题恰好全在 GBK 可编码范围）。

## 5. 测试与验收

- 单测：`python -m unittest tests.test_s2_fetch_setlist -v` → **40/40 通过**（全离线）：
  - 版式 A（IWSF）：标题/日期场馆（去「詳細」链接）/14 出演者/21 曲；首行 Dance in the Light
    （no=1、brand=ミリオンライブ！、4 演者）；链接行 Marionetteは眠らない（link 以
    `/song/detail/285.html` 结尾）；末行 ダンス・ダンス・ダンス（performers==["全員"]、
    brand=THE IDOLM@STERシリーズ）；歌名不含徽章文本与 ( )；performers 无分隔符残留。
  - 版式 B（13thLIVE）：日期场馆精确匹配 `<p>` 行；14 出演者；23 曲全 brand=None；
    `SPARKERS (新曲)` 保留 notes；首/末行 全員。
  - 版式 C（DERE）：日期场馆 = 场馆行（**非**日程表单元格）；9 出演者；23 行 − 2 幕标题行 = 21 曲；
    幕标题行不混入；无序号行 no 回退（1、2…）；有号行忠实保留（14 パ・リ・ラ）。
  - 防御 11 项：缺表格/缺 tbody/不足 3 td/非数字 no 回退/混合编号/缺标题/缺日期行/缺出演块/空 HTML/
    无詳細链接时的 開演/開場 兜底/urljoin 基准。
  - 抓取层 5 项：MockTransport 注入、UTF-8 显式解码（charset 谎报 iso-8859-1 仍正常）、404/连接失败/5xx
    重试耗尽 → FetchError。
- 探针验收（S2 验收清单）：
  - `python scripts/probe_song_event.py --setlist-local fixtures/imas_db_iwsf_day1.html`（及另两份）✓
  - `python scripts/probe_song_event.py --setlist http://imas-db.jp/song/event/{idolmaster_iwsf_day1,
    million_13th_day1,cinderella_cg_musical_dd}.html` → 3 个真实 URL 输出正确 Setlist，
    与 2026-08-27 抓取的 fixture 完全一致（站点未改版）✓

## 6. 已知项 / 后续

- 音乐剧 tracklist 的**幕标题行**（【第X幕 …】）被跳过（`Track` 契约无该字段）；若 S4 渲染想显示幕标题，
  需扩契约或在渲染层另取。
- 音乐剧无序号行 no 回退为运行序号、断号行保留原页编号——渲染时按列表顺序展示，无影响。
- `(新曲)`、`(short ver.)` 等 `<small class="notes">` 保留在歌名（非徽章，忠实显示）。
- 下一步：S3 模糊匹配（`s3_match.py`，依赖 S1 `Event` 结构，可随时开工）。
