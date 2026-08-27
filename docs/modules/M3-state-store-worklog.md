# M3 增量检测 + 状态库（StateStore）— 工作日志

> 线程：M3
> 实现时间：2026-08-26
> 状态：✅ 已完成（全量单测 48/48 通过，其中 M3 10/10）

## 1. 实施计划（开工前确定，写入备查）

1. 通读 `docs/module-specs.md` §1 冻结契约 + `docs/modules/M3-state-store.md` 规格（含 M2/M4/M5/M6 规格交叉核对，确认 §1.1–§1.5 各文档定义一致）。
2. 新建 `src/models.py`：冻结契约 dataclass 集中为**单一事实源**（可拓展性基础，防接口漂移）。
3. 改造 `src/m1_fetcher.py`：`NewsItem` 改为从 `models.py` import 并 re-export（公共 API `from m1_fetcher import NewsItem` 不变，既有 8 条单测不受影响）。
4. 实现 `src/m3_store.py`（仅标准库 `sqlite3`，无新增依赖）。
5. 编写 `tests/test_m3_store.py`，覆盖 M3 规格 §7 全部 5 条验收 + 边界 + 并发 + 持久化。
6. 全量跑单测（M1 8 + M2 30 + M3 10 = 48 条，全过）。
7. 更新文档：`docs/index.md`（§1/§2/§3/§4）、`docs/module-specs.md` §1 指针、本日志。

## 2. 可拓展性设计决策

| 决策 | 做法 | 理由 |
|---|---|---|
| 契约类型单一事实源 | 全部放 `src/models.py`，M1 复用并 re-export | M2/M4/M5/M6 直接 import，杜绝同名 dataclass 漂移（`==`/`isinstance` 失效） |
| DB 路径 3 级解析 | 显式 `db_path` 参数 > 环境变量 `STATE_DB_PATH` > 默认 `data/state.db`（相对仓库根） | 测试/迁移可注入；不依赖启动 CWD（M9 服务化/定时任务下 CWD 不可靠） |
| 表结构版本化 | `PRAGMA user_version` 记录 schema 版本（当前 v1） | 未来加列/加表走 `_ensure_schema` 迁移分支，不删库 |
| 幂等防并发 | `get_new_items` 用 `INSERT OR IGNORE` 占位，`rowcount==1` 判定新增 | 并发重入天然安全（验收 5），无需显式锁 |
| 并发写容错 | 每操作一个短连接 + `busy_timeout=10s` | 避免跨线程共享连接；多线程写串行化不报锁错误 |
| 时间 | UTC ISO（+00:00，秒精度） | 可排序、无时区歧义（规格 §8） |
| 失败重试 | 本模块不处理；提供 `get_unpushed()` 供 M7 补救 | 规格 §6 简化建议，职责单一 |

## 3. 验收对照（M3 规格 §7）

| 验收标准 | 测试 | 结果 |
|---|---|---|
| 首喂 10 条 → 10 新增；再喂同样 10 条 → 0 | `test_first_feed_all_new_then_none` | ✅ |
| 8 旧 + 2 新 → 只返回 2 新、顺序与输入一致 | `test_only_new_returned_order_preserved` | ✅ |
| `mark_pushed` 后 `pushed_at` 非空；`get_unpushed()` 不再含该条 | `test_mark_pushed_sets_timestamp` | ✅ |
| 关闭重开进程后状态仍在（持久化） | `test_persistence_across_connections` | ✅ |
| 并发/快速连调不重复插入不报错 | `test_concurrent_claims_each_id_once`（4 线程 × 20 条，每个 id 恰好被认领一次） | ✅ |

另覆盖：`record_push_result` 落库（ok→0/1）、空列表、`init_db` 幂等、未知 id no-op、`STATE_DB_PATH` 环境变量覆盖、`get_unpushed` 按首次见到升序。

## 4. 交付物

- `src/models.py` — 冻结契约类型（§1.1–§1.5，单一事实源）
- `src/m3_store.py` — StateStore 实现（`init_db` / `get_new_items` / `mark_pushed` / `record_push_result` / `get_unpushed`）
- `tests/test_m3_store.py` — 单测（10 条）
- `src/m1_fetcher.py` — 改为复用 `models.NewsItem`（行为不变，8 条单测仍过）

## 5. 环境踩坑记录（沙箱/Windows，备查，**对全部模块线程生效**）

- **不要用 `tempfile.TemporaryDirectory`**：本机沙箱下其创建目录的内部写入（sqlite 建库 / mkdir / rmtree 清理）会被拒（`WinError 5` / `unable to open database file`）；普通 `os.makedirs` 目录完全正常（验证脚本 `.tmp/probe_plain.py`）。测试临时目录统一放仓库内 `.tmp/`（已 gitignore，服务器上同样成立）。
- **系统 `%TEMP%` 在沙箱下不可写**：`tempfile` 默认目录、以及任何指向 `C:\Users\Z\AppData\Local\Temp\...` 的路径都会被拒。所有测试文件的临时目录必须落在工作区内。
- 上述问题已导致并行 M4 线程的 `tests/test_m4_translator.py`（7 条配置加载测试）同款报错——M4 线程请参照本日志 §5 修复；若需协助可找 M3 线程。

## 6. 后续可拓展点（本期未做，留档）

- **内容级变更检测**：`seen_items` 增加内容哈希列，标题/正文被官方修改时按需重推（需回改契约 §1 与 M3 规格，走 `_ensure_schema` 迁移分支 v2）。
- `push_log` 保留期清理策略（定时 DELETE 旧行）。
- M7 集成时的失败重试策略（消费 `get_unpushed()`，注意重试上限防死循环）。
