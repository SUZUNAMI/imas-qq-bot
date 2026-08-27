# M7 主控/调度 — 工作日志

> 时间：2026-08-26
> 线程：M7 主控/调度 + 后台启动脚本 + 本地留档

## 1. 需求

用户：需要一个可以由脚本启动、挂载后台的 cmd 进程，**每次启动时询问要启用哪些企划（brand）的新闻加入监听**；
实施中追加：**每次抓取到新闻后在本地留档（原文+图片+译文），按日期分文件夹**。

## 2. 已确认设计（ask_user_question）

1. 启动形态：**新开独立 cmd 窗口**（`start "M7 Bot" cmd /k ...`），窗口内交互+滚动日志，脚本立即返回。
2. 记住上次企划选择作默认（回车沿用）：**是**（`data/m7_brands.json`）。

## 3. 实施内容

| 文件 | 说明 |
|---|---|
| `src/main.py` | M7 主控：企划询问 / 主循环 / 留档 / 失败补救 / 日志 / CLI |
| `scripts/start_bot.cmd` | 后台挂载启动（新窗口，`chcp 65001` + `PYTHONUTF8=1`，纯 ASCII） |
| `scripts/stop_bot.cmd` | `taskkill /FI "WINDOWTITLE eq M7 Bot*"` 后备停止 |
| `tests/test_m7_main.py` | 21 个单测（无网络） |
| `docs/modules/M7-orchestrator.md` | 交接规格 |
| `docs/modules/M7-orchestrator-plan.md` | 计划（已确认并实施） |

## 4. 关键决策与理由

1. **推送成功口径 = 任一群成功即 mark_pushed**：全部群成功才回写会导致健康群反复收到同一新闻（更扰民）；失败群留在 push_log 供人工补发。
2. **留档按新闻发布日期分文件夹**：档案语义按新闻归类；item.date 为空回退抓取当天。
3. **留档与推送解耦**：详情解析成功即留档原文+图片，翻译失败也不丢原文；文本覆写幂等（重试成功补译文），图片只补缺。
4. **dry-run 用独立 state_dryrun.db**：若用正式库，dry-run 的 `get_new_items` 会把条目占位为「已见」，正式运行将不再视为新增（吞增量）。
5. **批处理纯 ASCII**：cmd 按 OEM 代码页解析 .cmd，中文注释/echo 会乱码；CJK 输出全部由 Python 侧 `sys.stdout.reconfigure(utf-8)` + 窗口内 `chcp 65001` 处理。
6. **翻译失败也返回 False → 留 unpushed**：下轮 get_unpushed() 自动补救（重试翻译+推送，届时译文补进留档）。

## 5. 测试与验收

- 单测：`python -m unittest discover -s tests` → **167/167 OK**（原 146 + M7 21）。
  - 覆盖：企划输入解析（多选/全选/非法/去重/中文逗号）、企划记忆（roundtrip/缺失/损坏/未知 code 过滤）、
    单轮流水线（无新增/全链路成功/推送失败不回写/补救成功/翻译失败仍留档）、留档（按日期目录/有无译文/空日期回退/图片幂等/扩展名猜测）。
- 全链路 dry-run（真实网络）：`--once --dry-run --brands SHINYCOLORS --limit 3` →
  抓取 1 条 `01_19692` → 增量 → 详情 → 真实 DeepSeek 翻译（HTTP 200）→ 留档
  `data/archive/2026-08-26/01_19692/`（原文.md/译文.md/meta.json/images 2 张，
  1 张 `[SSL: UNEXPECTED_EOF]` 瞬断被优雅跳过并告警）→ 不推送 → 本轮统计 抓取1/新增1/成功1/失败0/错误0。
- 交互询问：`echo 1,5,6 | python src/main.py --once --dry-run --limit 1` →
  显示「上次选择: SHINYCOLORS」，读取输入，写入 `data/m7_brands.json`（IDOLMASTER,SHINYCOLORS,GAKUEN），正常跑轮。
- 窗口挂载：`start "M7 Bot" cmd /c "..."` 标记文件验证通过（独立窗口成功执行，`cmd /k` 与 `/c` 引号同构）。

## 6. 踩坑记录

1. `--brands SC` 报「未知企划 code」：SC 是缩写，官方 7 个 code 是全称大写（SHINYCOLORS 等），已修正示例文案。
2. PowerShell 内联会解析 `&&`：验证 cmd 引号时需用变量传参或批处理文件绕开。
3. `cmd /k ""cd /d "path" && python ...""` 双引号包裹是 Windows 标准惯用法（cmd /? 规则 2 剥首尾引号），已验证可用。

## 7. 遗留 / 后续（M9）

- M9 服务化时用 `--brands` 非交互模式（WinSW 无法交互询问）；企划记忆文件与 state.db 随迁备份。
- 图片下载失败（SSL 瞬断）目前仅跳过留档该图并告警；如需重试可后续加。

---

# 第二轮（2026-08-26）：翻译失败直发原文 + 显式 config

## 1. 需求

1. **若翻译失败就直接推送原文，并附上翻译失败消息**（原行为：留 unpushed 下轮补救重试翻译）。
2. **轮询间隔（轮询cd）、API 两处写入显式 config** 方便调整。

## 2. 实施

| 文件 | 变更 |
|---|---|
| `src/main.py` | ① `_push_original_fallback()`：翻译失败 → 直发原文（复用 M5 模板常量/分片）+「⚠️ AI 翻译失败：<原因>（本条为原文直发）」；② `_TRANSLATE_FAILED` 进程内失败记忆（news_id→原因），后续轮次跳过重复翻译直接直发，重启后重试翻译；③ `load_orchestrator_config()` 读 `orchestrator:` 段（复用 M6 `_read_config_file` 的 YAML 子集解析器，避免第三份重复实现）；④ `run_once`/`main` 增加 `api_base` 透传；⑤ `--interval` 缺省改读 config；⑥ `archive_news` 增加 `translation_error` 记入 meta.json |
| `src/m1_fetcher.py` | `fetch_news_list(limit, brands, api_base=None)` + `_get_cms_token`/`_article_to_item`/`_articles_to_items` 加 `base` 可选参数（缺省 `CMS_API_BASE`，向后兼容，沿用 brands 的追加先例） |
| `config.yaml` / `config.example.yaml` | 新增 `orchestrator:` 段：`poll_interval_sec` / `api_base` |
| `tests/test_m7_main.py` | 新增 7 个用例（共 28/28）：config 加载×4、api_base 透传、翻译失败直发原文（含消息内容断言）、失败记忆跳过重试、直发也失败留补救；原「翻译失败留 unpushed」用例按新行为改写 |
| `tests/test_m1_fetcher.py` | 新增 TestApiBaseOverride×3（共 16/16） |
| `docs/module-specs.md` | §2 M1 追加 api_base 可选参数说明 |

## 3. 关键决策

1. **翻译失败直发原文计入「推送成功」**：消息已发出（原文+失败说明），避免死循环补救；若直发也失败（如 NapCat 断开）仍留 unpushed 下轮重试。
2. **失败原因进程内记忆**（`_TRANSLATE_FAILED`）：同一 news_id 翻译失败后，后续轮次跳过翻译 API 直接直发（防刷 API 费用）；**重启清空**，保证自愈（配置修好后重试翻译成功）。
3. **轮询/API 进 config**：`orchestrator.poll_interval_sec` / `orchestrator.api_base`；命令行 `--interval` 优先于 config（config 为缺省层）。
4. **api_base 走 M1 可选参数而非改模块常量**：不污染全局状态，向后兼容（缺省行为不变），与 brands 追加先例一致。

## 4. 测试与验收

- 单测：全仓 **177/177 OK**（M1 16、M2 30、M3 10、M4 34、M5 26、M6 33、M7 28）。
- 翻译失败实测（伪造 key）：`DEEPSEEK_API_KEY=sk-bogus` + `--once --dry-run --limit 2` →
  2 条学マス新闻 401 → 「翻译失败（将直发原文）」→ 留档原文+图片（meta `translated:false` + `translation_error`）→
  「翻译失败，改为直发原文」→ [DRY-RUN] 预览为直发原文消息（含 `⚠️ AI 翻译失败`）→ 本轮 抓取2/新增2/推送成功2/失败0/错误0。
- config 生效：启动日志「间隔 300s | API https://cmsapi-frontend…/sitern/api/」来自 `orchestrator:` 段。

## 5. 踩坑记录

1. PowerShell 内联传参遇 `&&`/引号解析问题 → 用变量传参或批处理文件绕开（与第一轮同）。
2. 验收时 `--brands IDOLMASTER` 抓到 0 条——最新 2 条里没有 765PRO 新闻；改用全企划（删掉 m7_brands.json 使上次选择失效）才拿到 2 条学マス新闻触发失败路径。

---

# 第三轮（2026-08-26）：启动时间截断 + 轮询间隔改 1 分钟

## 1. 问题

用户反馈：**没有时间限制，bot 在疯狂推送新闻**。诊断：17:52 用户正式启动后，首轮把最新
14 条新闻全部视为新增一股脑推送（state.db 当时几乎为空）；且 `get_unpushed()` 补救循环会把
推送失败/未完成的旧条目在每轮反复重试补推。要求：**只推送启动时间之后更新的新闻**；同时
**轮询间隔（cd）改为 1 分钟查询一次**。

## 2. 实施

| 文件 | 变更 |
|---|---|
| `src/m1_fetcher.py` | `fetch_news_list(..., min_updated=None)` + `_articles_to_items(..., min_updated)`：只保留 `updated`（缺失回退 `startdate`）>= 截断值的条目（实测 CMS article 有 `updated` 字段，1787713200 发布 / 1787714402 更新） |
| `src/main.py` | ① 启动时 `cutoff = int(time.time())`，每轮 `fetch_news_list(min_updated=cutoff)`（启动时间截断）；② `_suppress_preexisting_unpushed()`：启动时把历史遗留未推送条目标记为已处理（防止补救循环补推旧新闻）；③ `--no-cutoff` 关闭截断；④ 启动日志显示截断开关 |
| `config.yaml` / `config.example.yaml` | `orchestrator.poll_interval_sec: 300 → 60`（1 分钟查询一次）+ 截断行为注释 |
| `tests/test_m1_fetcher.py` | TestMinUpdatedFilter ×3（截断保留/回退 startdate/无截断全保留） |
| `tests/test_m7_main.py` | min_updated 透传、_suppress_preexisting_unpushed ×2（共 31/31） |

## 3. 关键决策

1. **截断用 CMS `updated` 字段**（缺失回退 `startdate`）：语义就是「启动时间之后更新/发布」；
   同一新闻后续被编辑（updated 变化）不会重复推送——id 去重（seen_items）仍然兜底。
2. **启动截断默认开启**，`--no-cutoff` 关闭（M9/补推场景）；截断只作用于初始抓取，
   已进入 seen_items 的条目由 id 去重，不受影响。
3. **历史遗留未推送条目启动即跳过**：它们都是启动前抓到的旧新闻，符合「不补推」语义；
   否则补救循环会在重启后把它们补推出去（用户遇到的刷屏的另一半原因）。
4. **间隔 1 分钟走 config**（`orchestrator.poll_interval_sec: 60`），不改代码默认值 300
   （配置缺失时兜底），命令行 `--interval` 仍可临时覆盖。

## 4. 测试与验收

- 单测：全仓 **183/183 OK**（M1 19、M2 30、M3 10、M4 34、M5 26、M6 33、M7 31）。
- 截断实测：`--once --dry-run` 启动日志「时间截断：只推启动时间(Unix 1787738199)之后更新的新闻」，
  M1 抓取 **0 条**（启动前已存在的新闻全部被截断）→ 不再刷屏。
- 间隔生效：启动日志「间隔 60s」（来自 config.yaml）。
- 诊断确认：旧 bot 已停止（17:52:33 后无新日志）；state.db 遗留 13 条未推送旧新闻，
  下次启动被 `_suppress_preexisting_unpushed` 自动跳过（已实测该函数逻辑）。

---

# 第四轮（2026-08-26）：合并转发（文本段+图片合并为一条「合并聊天记录」）

## 1. 需求

最终发送时，把文字及图片消息合并为一条**合并聊天记录**发送（不再逐条刷屏）。

## 2. 实施（模块归属：M6，本线程代实现）

| 文件 | 变更 |
|---|---|
| `src/m6_notifier.py` | ① `NotifierConfig.merge_forward`（默认 false，config.yaml `napcat.merge_forward` + 环境变量 `NAPCAT_MERGE_FORWARD`）；② 网络层收敛为 `_post_api`（`send_group_msg` / `send_forward_msg` 共用，行为不变）；③ `_build_forward_nodes`：每个文本段一个 node + 全部配图一个 node；④ `_get_self_info`：`get_login_info` 取 bot uin/昵称（按 base_url 缓存，失败回退 10001/爱马仕新闻）；⑤ `_push_one_group_merged` + `_call_forward_with_retry`；⑥ CLI `--merge` 自测参数 |
| `config.yaml` / `config.example.yaml` | `napcat.merge_forward: true`（本项目）/ `false`（模板默认） |
| `tests/test_m6_notifier.py` | FakeClient 加 `get`；新增合并转发测试 10 个（config 解析×3、node 结构、单次调用成功、无图、失败重试、非合并仍走 send_group_msg、self_info 获取与缓存、失败回退）；M6 33→43，全仓 193/193 |
| `docs/modules/M6-notifier.md` | 新增 §7 合并转发 + §10 验收 + §11 注意事项 |

## 3. 关键决策

1. **默认 false，本项目 config 显式开启**：向后兼容（其他调用方/模板行为不变），本 bot 由 config 决定。
2. **每个文本段一个 node**：M5 已按 3500 字/段落边界分片，作为合并记录里的独立气泡，
   避免超长文本在单个 node 内被 QQ 截断；配图单独一个 node（多图）。
3. **uin/昵称动态取自 get_login_info**（按 base_url 进程内缓存）：不硬编码 bot 号，
   换号/换服务器不用改代码；查询失败回退占位不阻断。
4. **多群也只查一次 get_login_info**（缓存）；群间仍保留 interval_sec 间隔。

## 4. 测试与验收

- 单测：全仓 **193/193 OK**（M6 43/43）。
- live 验收（2026-08-26）：`python src/m6_notifier.py --merge 450599137` → `ok=True message_id=1487872687`，
  test 群显示为一条「合并聊天记录」（用户确认正常显示）。
- 探针确认：NapCat v4.18.19 `send_forward_msg` 返回 `data.message_id`；`get_login_info` 返回
  user_id=1666562110 / nickname=時津風。

---

# 第五轮（2026-08-26）：开机/关机状态通知

## 1. 需求

bot 启动时在 test 群发一条消息、结束时再发一条，让用户知道 bot 开关机了。

## 2. 实施

| 文件 | 变更 |
|---|---|
| `src/main.py` | ① `_load_notify_groups()`：读 `orchestrator.notify_groups`（list/JSON 数组字符串/逗号分隔，复用 M6 `_coerce_group_ids`）；② `_send_notification()`：普通文本消息发送（`dataclasses.replace` 临时关 merge_forward），失败仅告警；③ main() 启动时发「已启动」（监听企划/间隔/模式/目标群/时间），运行中累计轮数与推送统计，`finally` 里发「已停止」（轮数/成功/失败/时间）——Ctrl+C、--once、正常退出都会触发 |
| `config.yaml` / `config.example.yaml` | `orchestrator.notify_groups: ["450599137"]`（本项目）/ 示例 |
| `tests/test_m7_main.py` | NotifyTests ×7：通知群归一化（list/JSON 字符串/逗号/空/缺失）、通知用普通文本且关 merge_forward、空群 no-op、发送异常不抛出；M7 31→38，全仓 200/200 |
| `docs/modules/M7-orchestrator.md` | §3.4 加 notify_groups；新增 §3.6 状态通知 |

## 3. 关键决策

1. **通知用普通文本**（临时关 merge_forward）：短状态消息包一层合并聊天记录反而碍事。
2. **关机通知放 finally**：Ctrl+C / --once / 正常退出都保证发出；进程被强杀（kill -9）除外。
3. **失败仅告警不阻断**：NapCat 未启动时开机通知失败，bot 照常运行（用户仍可从窗口日志得知）。
4. **通知群独立于推送群**：`orchestrator.notify_groups` 只管状态消息，不影响 `napcat.group_ids` 的新闻推送目标。

## 4. 测试与验收

- 单测：全仓 **200/200 OK**（M7 38/38）。
- live 验收（2026-08-26）：`python src/main.py --once` → 开机通知 send_group_msg 200 OK（"🤖 M7 …已启动"）、
  跑一轮（截断生效 0 新增）、关机通知 200 OK（"🤖 M7 已停止"），test 群收到两条状态消息。
- 顺带确认：state.db 遗留未推送旧新闻已全部处理（unpushed=0），启动截断无历史包袱。

---

# 第六轮（2026-08-26）：状态通知双群发送 + 文案进 config 自定义

## 1. 需求

1. 开机/关机消息在**两个群**（正式群 1033148779 + test 群 450599137）都发出；
2. **消息内容放进 config** 让用户自定义。

## 2. 实施

| 文件 | 变更 |
|---|---|
| `src/main.py` | ① `DEFAULT_STARTUP_TEXT` / `DEFAULT_SHUTDOWN_TEXT` 内置默认文案（带占位符）；② `_notify_template()`：读 config 的 `notify_startup`/`notify_shutdown`（缺失回退默认，`\n` 转义还原为换行）；③ `_render_template()`：占位符渲染，未知占位符原样保留不崩溃（lenient format_map）；④ main() 开机/关机均向 `notify_groups` **全部群**发送渲染后的模板 |
| `config.yaml` / `config.example.yaml` | `notify_groups: ["1033148779", "450599137"]`；新增 `notify_startup` / `notify_shutdown` 完整模板（含占位符说明注释） |
| `tests/test_m7_main.py` | NotifyTests +5：占位符渲染、未知占位符保留、模板缺省回退、`\n` 还原、config 值生效；M7 38→43，全仓 205/205 |
| `docs/modules/M7-orchestrator.md` | §3.6 更新：双群 + 文案模板占位符说明 |

## 3. 关键决策

1. **文案模板走 config、缺省有内置默认**：用户改 config 即可自定义，删掉/写错模板自动回退默认文案。
2. **占位符渲染 lenient**：未知占位符原样保留，模板缺字段不崩溃（`_Lenient` dict + format_map）。
3. **`\n` 转义还原**：YAML 子集解析器不解码转义，config 中写 `\n` 由代码统一还原为真实换行。
4. **通知群与推送群解耦**：`notify_groups` 只管状态消息（现配正式群+test 群），不影响 `napcat.group_ids` 的新闻推送。

## 4. 测试与验收

- 单测：全仓 **205/205 OK**（M7 43/43）。
- live 验收（2026-08-26）：`python src/main.py --once` → 开机通知 ×2 群、关机通知 ×2 群，
  共 4 次 send_group_msg 均 200 OK（正式群 + test 群都收到）。
