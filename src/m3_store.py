"""M3 增量检测 + 状态库（StateStore）— 爱马仕官方新闻 QQ 转发机器人.

维护「已见过的新闻」状态（SQLite 单文件），判断哪些是新增、防止重复推送，并记录推送结果。
规格见 docs/modules/M3-state-store.md（本文件为其实现）。

设计要点（可拓展性）
--------------------
1. 契约类型统一来自 src/models.py（NewsItem / PushResult），本模块不重复定义。
2. DB 路径解析优先级：显式 ``db_path`` 参数 > 环境变量 ``STATE_DB_PATH``
   > 默认 ``data/state.db``（相对仓库根，与启动 CWD 无关——M9 服务化/定时任务下 CWD 不可靠）。
3. 幂等：``get_new_items`` 用 ``INSERT OR IGNORE`` 先占位（rowcount==1 才视为新增），
   天然防并发重入（M3 规格 §6.2）；其余写操作均可安全重复调用。
4. 表结构版本化：``PRAGMA user_version`` 记录 schema 版本，未来加列/加表走迁移分支，不删库。
5. 时间统一 UTC ISO 字符串存储（可排序、无时区歧义，M3 规格 §8）。

约定
----
- 失败重试不由本模块处理（M3 规格 §6.4）：推送失败的条目由 M7 调 ``get_unpushed()`` 补救。
- 去重只认 ``NewsItem.id``；date/title 仅作展示。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from models import NewsItem, PushResult

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 1             # 当前表结构版本（PRAGMA user_version）
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "state.db"
ENV_DB_PATH = "STATE_DB_PATH"  # 环境变量覆盖（测试/运维注入用）
_BUSY_TIMEOUT_MS = 10_000      # 并发写等待上限（毫秒），防止 "database is locked"

# 路径参数类型：显式路径（str / os.PathLike）或 None（走解析链）
_DbPath = Optional[Union[str, os.PathLike]]


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    """UTC ISO 时间戳（秒精度，带 +00:00，可排序、无时区歧义）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_db_path(db_path: _DbPath) -> Path:
    """解析 DB 路径：显式参数 > 环境变量 > 默认；自动创建父目录（幂等）。"""
    if db_path is not None:
        p = Path(db_path)
    elif os.environ.get(ENV_DB_PATH):
        p = Path(os.environ[ENV_DB_PATH])
    else:
        p = DEFAULT_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _connect(db_path: _DbPath = None) -> sqlite3.Connection:
    """打开连接并确保 schema 存在（幂等）。每操作一个短连接，避免跨线程共享连接。"""
    conn = sqlite3.connect(_resolve_db_path(db_path), timeout=10.0)
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """按 SCHEMA_VERSION 建表/迁移（幂等，首次运行自动建库建表）。"""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_items (
                id            TEXT PRIMARY KEY,   -- NewsItem.id，去重依据
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

            CREATE INDEX IF NOT EXISTS idx_push_log_news_id ON push_log(news_id);
            """
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    # 未来迁移分支（可拓展性）：elif version < 2: 新增列/表（ALTER TABLE ... ）


def _row_to_item(row: sqlite3.Row) -> NewsItem:
    """seen_items 行 -> NewsItem（get_unpushed 用；thumbnail 不在库内，置 None）。"""
    return NewsItem(
        id=row["id"], url=row["url"], title=row["title"], date=row["date"]
    )


# ---------------------------------------------------------------------------
# 公共 API（契约签名冻结，见 M3 规格 §5；db_path 为可拓展的可选参数）
# ---------------------------------------------------------------------------
def init_db(db_path: _DbPath = None) -> None:
    """建库建表（幂等）。首次运行自动调用，也可显式调用做初始化/备份前准备。"""
    conn = _connect(db_path)
    conn.close()


def get_new_items(items: list[NewsItem], db_path: _DbPath = None) -> list[NewsItem]:
    """过滤出未见过（新增）的条目，**保持输入顺序**。

    幂等规则：判断新增的**同时** ``INSERT OR IGNORE`` 占位（first_seen_at=当前 UTC，
    pushed_at=NULL），rowcount==1 才视为新增——并发重入也不会重复返回（M3 规格 §6.2）。
    """
    if not items:
        return []
    conn = _connect(db_path)
    try:
        now = _now_iso()
        new_items: list[NewsItem] = []
        for item in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO seen_items "
                "(id, url, title, date, first_seen_at, pushed_at) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (item.id, item.url, item.title, item.date, now),
            )
            if cur.rowcount == 1:
                new_items.append(item)
        conn.commit()
    finally:
        conn.close()
    return new_items


def mark_pushed(news_id: str, db_path: _DbPath = None) -> None:
    """推送成功后回写 pushed_at（UTC ISO）。已成功过则保留首次时间（幂等）。"""
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE seen_items SET pushed_at = COALESCE(pushed_at, ?) WHERE id = ?",
            (_now_iso(), news_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_push_result(r: PushResult, news_id: str, db_path: _DbPath = None) -> None:
    """写 push_log 一行（append-only 日志；ok 转 0/1 存储）。"""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO push_log (news_id, group_id, ok, message_id, error, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (news_id, r.group_id, 1 if r.ok else 0, r.message_id, r.error, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def get_unpushed(db_path: _DbPath = None) -> list[NewsItem]:
    """返回所有尚未成功推送的条目（pushed_at IS NULL），按首次见到时间升序。

    供 M7 主控做失败补救（M3 规格 §6 简化建议），与 M2/M4/... 流水线输入同构。
    """
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, url, title, date FROM seen_items "
            "WHERE pushed_at IS NULL ORDER BY first_seen_at ASC, id ASC"
        ).fetchall()
        return [_row_to_item(r) for r in rows]
    finally:
        conn.close()
