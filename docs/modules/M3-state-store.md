# M3 增量检测 + 状态库（StateStore）— 交接规格

> 项目：爱马仕官方新闻 QQ 转发机器人。追踪 https://idolmaster-official.jp/news，新新闻发布后推送「原文 + AI 日译中」到 QQ 群。
> 技术栈：Python 3.11+。
> 本文件**自包含**：只读本文件即可实现本模块，无需读其他模块文档。
> 契约冻结：输入/输出结构必须严格符合本文件定义；如需改动，回改 `docs/module-specs.md` §1。

## 1. 本模块在流水线中的位置

```
M1 列表抓取 ──NewsItem[]──► M3 增量检测 ──新增NewsItem[]──► M2 详情解析 ──► …
                             ▲  │
                             │  └─(推送成功)回写 pushed_at
                             │
                         M8 状态库(SQLite)
```

本模块：维护"已见过的新闻"状态，判断哪些是**新增**，防止重复推送，并记录推送结果。

## 2. 输入契约：`NewsItem[]`

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class NewsItem:
    id: str                 # 唯一键，如 "01_17821" —— 去重依据
    url: str
    title: str
    date: str               # "YYYY-MM-DD"
    thumbnail: Optional[str] = None
```

## 3. 输出契约

- `get_new_items(items: list[NewsItem]) -> list[NewsItem]`：返回其中**未见过**的条目（保持输入顺序）。
- 其余函数（见 §5）为写操作，不返回数据。

推送结果类型（供记录用，本模块定义，M6 也会产生同结构）：

```python
@dataclass
class PushResult:
    group_id: str
    ok: bool
    message_id: str
    error: Optional[str] = None
```

## 4. 数据库设计（SQLite，单文件）

文件：`data/state.db`（首次运行自动建库建表）。

```sql
CREATE TABLE IF NOT EXISTS seen_items (
    id            TEXT PRIMARY KEY,   -- NewsItem.id
    url           TEXT,
    title         TEXT,
    date          TEXT,
    first_seen_at TEXT,               -- ISO 时间戳，首次见到
    pushed_at     TEXT                -- 推送成功时间；NULL 表示尚未成功推送
);

CREATE TABLE IF NOT EXISTS push_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id    TEXT,                  -- 关联 seen_items.id
    group_id   TEXT,
    ok         INTEGER,               -- 0/1
    message_id TEXT,
    error      TEXT,
    ts         TEXT                   -- ISO 时间戳
);
```

## 5. 实现约定

- 文件：`src/m3_store.py`
- 依赖：`sqlite3`（标准库）。
- 函数签名（**幂等**是核心要求）：

```python
def init_db() -> None                                   # 建库建表
def get_new_items(items: list[NewsItem]) -> list[NewsItem]  # 过滤出未见过的
def mark_pushed(news_id: str) -> None                   # 推送成功后置 pushed_at
def record_push_result(r: PushResult, news_id: str) -> None  # 写 push_log
```

## 6. 幂等规则（必须遵守）

1. `id NOT IN seen_items` → 判定为新增。
2. 判断新增的**同时**把条目 `INSERT` 进 `seen_items`（`first_seen_at` 记当前时间，`pushed_at` 为 NULL）——即"先占位，防并发重入"。
3. 只有**推送成功后**才调用 `mark_pushed` 写 `pushed_at`。
4. 若某轮推送失败（`pushed_at` 仍为 NULL），下一轮该条目因已在 `seen_items` 中、不会再次被 `get_new_items` 返回——这需要与 M7 主控约定：**失败重试由主控另行触发，不在本模块处理**。本模块只保证"不重复、不丢失记录"。

> 简化建议：如果不想引入"失败重试"复杂度，可约定 `get_new_items` 只返回「完全未见过」的条目；推送失败的条目由 M7 调用一个 `get_unpushed() -> list[NewsItem]` 补救。本模块需同时提供 `get_unpushed()`。

## 7. 验收标准

1. 首次喂 10 条 → 返回 10 条新增；再喂同样 10 条 → 返回 0 条。
2. 喂 8 条旧的 + 2 条新的 → 只返回 2 条新的，顺序与输入一致。
3. `mark_pushed` 后，`pushed_at` 非空；`get_unpushed()` 不再含该条。
4. 关闭重开进程后，状态仍在（数据持久化）。
5. 并发/快速连调不会重复插入报错（`INSERT` 用 `INSERT OR IGNORE` 或捕获主键冲突）。

## 8. 边界与注意事项

- 单文件 SQLite 便于备份迁移（迁移服务器时直接拷 `state.db`）。
- `date` 字段仅作展示，**去重只认 `id`**。
- 所有时间统一 UTC 或本地时区，用 ISO 字符串存储，避免时区混乱。
