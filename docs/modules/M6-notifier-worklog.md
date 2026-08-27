# M6 QQ 推送（Notifier，NapCatQQ / OneBot 11）— 执行工作日志

> 线程：M6（本线程）；项目：爱马仕官方新闻 QQ 转发机器人
> 契约：以 `docs/modules/M6-notifier.md` 与 `docs/module-specs.md` §1 为准（冻结）；类型复用 `src/models.py`（单一事实源）。
> 创建：2026-08-26；状态：✅ 全部完成——实现（单测 33/33 全绿，全仓 146/146）、验收脚本 PASS、**NapCat 环境配置完成 + live 真实推送验收通过（2026-08-26）**。运维细节见 `docs/modules/M6-napcat-setup.md`。

---

## 1. 执行计划（2026-08-26）

| 步骤 | 内容 | 验收 | 状态 |
|---|---|---|---|
| S1 环境与契约确认 | httpx 0.28.1 已 vendor 可用；契约 PushMessage/PushResult 已在 `src/models.py` 冻结；本机 NapCat 未运行（端口 3000 无监听） | 依赖可 import；契约复用不重复定义 | ✅ |
| S2 实现 | `src/m6_notifier.py`：`push(message, *, config=None, client=None) -> list[PushResult]`，OneBot 11 `send_group_msg`，配置三级覆盖 | 入口签名符合契约；文件就位 | ✅ |
| S3 单测 | `tests/test_m6_notifier.py`（fake client + 仓库内临时目录，零网络） | 33/33 通过（全仓 146/146） | ✅ |
| S4 验收 | `scripts/acceptance_m6.py`：配置面 + dry-run + 有 NapCat 时真实推送 | ✅ 全部通过（live 因 NapCat 未启动 SKIP，见 §4） | ✅ |
| S5 文档回写 | 本日志 + `docs/index.md` + `agent.md` + `config.example.yaml` / `.env.example` 补 napcat 段 | 索引与实际一致 | ✅ |
| S6 真实推送验收 | 测试群收到「原文 + 译文 + 链接」（需 NapCatQQ 启动 + bot 小号登录 + 测试群号） | 规格 §9.1–9.5 | ✅ 通过（2026-08-26：NapCatQQ v4.18.19 + Desktop v3.1.10 配置完成，bot 1666562110 登录，OneBot HTTP 3000 生效，`acceptance_m6.py --group 827029417` 真实推送成功，666 群收到消息） |

## 2. 环境记录（2026-08-26）

- Python 3.13.7；httpx 0.28.1（沿用 vendor，未新增依赖）；测试临时目录 `tests` 仓库内 `.tmp/m6_tests`（与 M3/M4 同法）。
- **NapCat 环境（2026-08-26 配置完成）**：NapCatQQ-Desktop v3.1.10（`C:\Program Files\NapCatQQ Desktop`）+ NapCat v4.18.19 组件（`C:\ProgramData\NapCatQQ Desktop\components\NapCatQQ`）；bot 小号 `1666562110`（時津風）已登录；OneBot 11 HTTP `127.0.0.1:3000` 生效；WebUI `http://127.0.0.1:6099`。配置与运维细节见 `docs/modules/M6-napcat-setup.md`。

## 3. 实现要点（S2/S3）★

**契约复用**：`from models import PushMessage, PushResult`（与 M1–M4 同法），本模块仅 re-export 保持 `from m6_notifier import PushMessage` 公共 API 不变。

**可拓展性设计**（与 M4 同思路落实）：
1. **依赖注入**：`push(message, *, config=None, client=None)` —— 测试注入 fake client 零网络；M7 可传入共享 httpx.Client；M8 可接管配置加载，`push()` 签名不变。
2. **输入鸭子类型**：`_coerce_message()` 接受 `models.PushMessage` / 任意含契约字段的 dataclass / dict。
3. **配置三级覆盖**（低→高）：内置默认 < `config.yaml`（或 `config.json`）的 `napcat:` 段 < `.env` < 环境变量；环境变量 `NAPCAT_BASE_URL / TOKEN / GROUP_IDS（逗号分隔）/ INTERVAL_SEC`。
4. **YAML 子集内联列表兼容**：子集解析器（与 M4 同法）不支持 `[a, b]` 列表字面量 → `_coerce_group_ids()` 兼容真 list / JSON 数组字符串 / 逗号分隔字符串三种形态，`config.example.yaml` 保持规格 §7 的 `group_ids: ["123456789"]` 写法。
5. **网络单一接缝** `_send_group_msg()`：重试语义——传输错误/429/5xx 重试 `max_retries`（默认 1，规格 §8）次指数退避；401/403 等鉴权 4xx 快速失败（重试徒劳）；返回体解析失败（非 JSON / status≠ok / 缺 message_id）→ `ok=False` 记录，不抛异常中断整轮。
6. **群间间隔**：群与群之间 `sleep(interval_sec)`（默认 1.5s），最后一个群后不 sleep；单群失败不阻断其他群，最后汇总 `PushResult[]`。
7. **群号处理**：契约存字符串（可能超 32 位整数范围）；请求体按 OneBot 11 规范转 int（Python int 无范围问题）；非数字群号该群 `ok=False` 不阻断。
8. **消息段组装**：文本段各自一条（`{"type":"text"}`），图片合并成一条多图消息（`{"type":"image","file":<URL>}`，NapCat 代为下载）；`link` 已由 M5 拼入文本，不重复发；空消息（无段无图）防御性 `ok=False`。
9. **网络不可达**：NapCat 未启动 → 全部群失败 + `logging.warning("NapCat 未连接：<base_url> 不可达…")` 明确提示（规格 §8）；`PushResult.error` 保留具体错误供 M8 记录。
10. **目标群来源**：`message.group_ids` 优先，为空回退 `config.group_ids`（配置默认群）；两者皆空 → 空结果 + 日志警告，不抛异常。

**PushResult 语义**（契约 §1.5）：每群一条；`ok` = 该群所有消息全部成功；`message_id` = 该群**第一条成功消息**的 id（失败 `""`）；`error` = 第一个失败原因。

## 4. 验收记录（S4，2026-08-26）

- `python -m unittest discover -s tests`：**146/146 通过**（M1 8 + M2 30 + M3 10 + M4 35 + M6 33）。M6 覆盖：契约 re-export、配置三级覆盖/YAML 子集/内联列表兼容/非法值兜底、输入归一化（models/dict/异构 dataclass/缺字段/非法类型）、群号解析（含超长群号）、单群单段、多段顺序、图片合并多图消息、token 鉴权头、空消息防御、重试成功/重试耗尽/鉴权快速失败、响应解析失败、status=failed、网络不可达全部失败+日志提示、单群失败不阻断、多群完整发送+群间 sleep、群号回退配置、无群空结果。
- `python scripts/acceptance_m6.py`：✅ 配置面（base_url 默认 http://127.0.0.1:3000）· ✅ dry-run 预演（不发真实消息）· ✅ live SKIP 提示明确（NapCat 未连接 / 未指定群号）。`[ALL PASS]`。
- `python src/m6_notifier.py --dry-run`：打印配置与将发送内容，exit 0。
- **live 推送验收（§9.1–9.5）**：✅ 通过（2026-08-26）——`python scripts/acceptance_m6.py --group 827029417` → `[config] base_url=http://127.0.0.1:3000` · `[dry-run]` 预演 · `[live] 群 827029417: ok=True message_id=1543301484` · `[ALL PASS]`；OneBot `get_group_msg_history` 二次确认 666 群收到完整「原文 + 译文 + 链接」测试消息（message_sent by 1666562110）。
- **真实新闻完整链路推送（2026-08-26，追加）**：用户要求「发送最新一条新闻到 test 群」——走 `.tmp/send_latest.py`（M1→M2→M4→M5→M6 真实链路）：最新新闻 `01_19667`（【学マス】学园偶像大师 ねむらせ隊 扭蛋登场，正文 572 字符，配图 1 张）→ M4 真实翻译 → M5 组装 1 段 1 图 → M6 推送 **test 群 450599137**（`get_group_list` 定位：`group_name="test"`）→ `ok=True message_id=1232731473`，`[DONE]`。M3 已 `mark_pushed(01_19667)` + `record_push_result`，正式管道（M7）不会将其重复推送。
- **M5 v2 模板重发验证（2026-08-26，追加）**：M5 线程 v2 变更（原文+译文均拼接，分隔线上原文下译文）后重发最新新闻 → test 群 450599137 `ok=True message_id=1704448846`；`get_group_msg_history` 拉回确认结构：原文 9 块 + 分隔线 + 译文 6 块 + `🔗 原文` 结尾（17 内容块，段落对齐）。全仓 146/146 回归通过（M5 v2 未破坏 M6）。
- **正式群推送验证（2026-08-26，追加）**：`python .tmp/send_latest.py --group 1033148779`（正式群，闪耀特种兵文娱部2.0）→ `ok=True message_id=1605962264`；`get_group_msg_history` 拉回确认：首行 `【NEWS】2026-08-26`、分隔线第 30 行（原文完整）、译文首行、`🔗 原文` 结尾，结构完整。
- **环境事件（2026-08-26）**：`.env` 的 `DEEPSEEK_API_KEY` 曾失效（旧 key `sk-3806…ce15` 返回 401 Authentication Fails），已由用户提供新 key 替换生效（本轮完整链路即用新 key 完成）。
- **配置观察（已修复）**：`config.yaml`/`.env` 的 `group_ids` 原含 `827029417`（不在 NapCat 当前群列表），已更新为实际存在的两群 `1033148779`（正式群）+ `450599137`（test 群，验收观察用，正式上线可删）；`load_config()` 实测生效。

## 5. 交接与遗留

- **交付物**：`src/m6_notifier.py`（`push() -> list[PushResult]`）、`tests/test_m6_notifier.py`、`scripts/acceptance_m6.py`、`config.example.yaml` / `.env.example` 补 `napcat` 段。
- **M5 对接**：`format_message(detail, tr, group_ids) -> PushMessage` → `push(message)`；segments 按顺序逐条发，images 合并多图消息，link 无需再发（已在文本末尾）。
- **M7 对接**：`push(message, config=cfg)` 直接调用；`PushResult[]` 供 M8 记录（`push_log` 表，M3 已建同结构）。
- **M8 对接**：配置面收敛在 `NotifierConfig` + `load_config()`；将来 M8 统一配置时替换 `load_config()` 实现即可，`push()` 签名不变。
- **M9 提示（运维）**：live 验收需 NapCatQQ 常驻（服务器首次交互登录 bot 小号）；`base_url`/`token`/`group_ids` 服务器上另行配置（不进 git）。
- **遗留提示**：`m4_translator.py` 与 `m6_notifier.py` 各内置一份 YAML 子集解析器（自包含交接文档要求模块独立）；M8 统一配置时可收敛到共享工具，无行为差异。
