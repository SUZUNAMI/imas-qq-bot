# Agent 全局约定 + M9 线程职责（开发环境管理与迁移）

> 所属项目：爱马仕官方新闻 QQ 转发机器人（idolmaster-official.jp → QQ 群）
> 创建时间：2026 年
> 状态：✅ M1–M7 已完成（2026-08-26），管道集成测试通过；M7 主控/调度+后台挂载+本地留档已完成，等待下一步指令（M9 迁移准备）

---

## 0. 全局约定（所有线程必须遵守）

1. **文档索引维护**：任何线程创建或修改文件（文档 / 代码 / 脚本 / 配置）后，必须同步更新 [`docs/index.md`](docs/index.md) 的对应章节（文档列表、模块状态、代码交付物、工作日志），保持索引与实际文件一致。
2. 需求不清晰时先提问确认执行细节；大型操作前先给出明确计划并分步确认；及时把结论写入文档备查。
3. 模块数据契约以 `docs/module-specs.md` §1 为准（冻结）；若需改动契约，必须回改该文档，并同步更新 `docs/index.md`。

---

## 1. 本线程职责

1. **开发环境管理**：负责本地（Windows）开发环境的搭建、维护与状态确认——包括 Python 环境、依赖安装、NapCatQQ / NTQQ 登录态、配置与密钥（`.env` / `config.yaml`）等。
2. **完成后迁移**：待开发阶段完成后，负责将整套系统迁移到 **Windows Server** 并使其常驻运行（对应 `docs/module-specs.md` 中的 **M9 打包与迁移**）。

## 2. 迁移要点（依据 M9 与架构文档）

| 项 | 要求 |
|---|---|
| 服务化 | WinSW 将 Python 主程序封装为 Windows 服务，开机自启 |
| NapCatQQ | 需在服务器首次交互登录 bot 小号后常驻 |
| 状态库 | SQLite 状态库随迁备份（拷贝 `state.db`） |
| 配置/密钥 | 外置且不进 git，服务器上另行配置 |
| 部署路径 | 本地（Windows）→ 迁移 Windows Server |

## 3. 与项目文档的关系

- 架构与部署计划：`docs/architecture-and-plan.md`
- 模块交接契约：`docs/module-specs.md`（M9 为本线程负责的收尾模块）
- 模块细节：`docs/modules/`（M1–M6）

## 4. 当前状态

- [x] **M1 列表抓取（2026-08-26 完成）**：路径 A（直连 CMS JSON API）打通并验收，交付 `src/m1_fetcher.py` + `tests/test_m1_fetcher.py`（8/8 单测通过）。详见 `docs/modules/M1-fetcher-worklog.md`。
- [x] **M2 详情解析（2026-08-26 完成）**：`src/m2_parser.py`（复用 models.NewsDetail）+ `tests/test_m2_parser.py`（30/30 通过）。详见 `docs/modules/M2-detail-parser-worklog.md`。
- [x] **M3 增量检测（2026-08-26 完成）**：`src/m3_store.py` + `tests/test_m3_store.py`（10/10 通过）。详见 `docs/modules/M3-state-store-worklog.md`。
- [x] **M4 翻译（2026-08-26 完成）**：`src/m4_translator.py` + `tests/test_m4_translator.py`（34/34 通过，全仓 82/82）。详见 `docs/modules/M4-translator-worklog.md`。真实翻译验收已通过（.env 已配置 `DEEPSEEK_API_KEY`，`python scripts/acceptance_m4.py` ALL PASS）。
- [x] **M1–M4 管道集成测试（2026-08-26 通过）**：`python scripts/pipeline_test_m1m4.py` → 17/17 通过（真实抓取→增量→详情→真实翻译）。详见 `docs/modules/pipeline-test-worklog.md`。
- [x] **M5 消息组装（2026-08-26 完成）**：`src/m5_formatter.py`（纯函数，模板/段落边界分片/图片透传，契约复用 models.py）+ `tests/test_m5_formatter.py`（26/26 通过，M1–M5 合计 113/113）+ `scripts/acceptance_m5.py`（ALL PASS）。详见 `docs/modules/M5-formatter-worklog.md`。
- [x] **M6 QQ 推送（2026-08-26 全部完成）**：`src/m6_notifier.py`（OneBot 11 `send_group_msg`，多群/群间间隔/重试/容错，契约复用 models.py）+ `tests/test_m6_notifier.py`（33/33 通过，全仓 146/146）+ `scripts/acceptance_m6.py`（配置面 + dry-run ALL PASS）。**NapCat 环境已配置、live 真实推送验收通过（2026-08-26）**：NapCatQQ-Desktop v3.1.10 + NapCat v4.18.19（本机 Windows），bot 小号 1666562110（時津風）登录，OneBot 11 HTTP `127.0.0.1:3000` 生效；`config.yaml` 建好 `napcat` 段、`.env` 补 `NAPCAT_*`；`python scripts/acceptance_m6.py --group 827029417` → 666 群收到测试消息，`[ALL PASS]`。运维/配置/踩坑详见 `docs/modules/M6-napcat-setup.md`，实现日志见 `docs/modules/M6-notifier-worklog.md`。
- [x] **M7 主控/调度（2026-08-26 完成）**：`src/main.py`（最终入口：每次启动交互询问监听企划 + 常驻轮询串联 M1→M6 + **本地留档**（原文/译文/图片，按新闻日期分文件夹）+ 失败自动补救 + `--once`/`--dry-run`/`--no-archive`）+ `scripts/start_bot.cmd`（后台挂载：新开 M7 Bot 窗口，脚本立即返回）+ `scripts/stop_bot.cmd` + `tests/test_m7_main.py`（21/21 通过，全仓 167/167）。dry-run 全链路验收通过（真实抓取 SC 企划→增量→详情→真实翻译→留档 `data/archive/2026-08-26/01_19692/`）。详见 `docs/modules/M7-orchestrator.md`（交接规格）与 `docs/modules/M7-orchestrator-worklog.md`（日志）。
- [ ] 等待下一步指令（M9 迁移准备）

## 5. 工作原则

- 需求不清晰时，先提问确认执行细节再动手。
- 大型操作前先给出明确计划，分步征求确认后执行，并及时写入文档备查。
