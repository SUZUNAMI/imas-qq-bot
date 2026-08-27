"""M6 QQ 推送（Notifier，NapCatQQ / OneBot 11）— 爱马仕官方新闻 QQ 转发机器人.

输入 ``PushMessage``（M5 输出，已分好片的文本段 + 配图 URL），通过 NapCatQQ 的
OneBot 11 HTTP API 推送到配置的多个 QQ 群，输出 ``PushResult[]``（每个群一条结果）。
契约：docs/modules/M6-notifier.md（自包含交接文档）；类型统一来自 ``src/models.py``。

规格要点（docs/modules/M6-notifier.md）
----------------------------------------------------------------------------
1. 发群消息：POST {base_url}/send_group_msg，body = {"group_id": int, "message": [...]}。
   鉴权：若配置了 token，请求头带 ``Authorization: Bearer <token>``。
2. 文本段按顺序逐条发；图片合并成一条多图消息发出；``link`` 已由 M5 拼进文本，不重复发。
3. 遍历 group_ids：每个群完整发一遍（文本段 + 图片），群与群之间 sleep ``interval_sec``
   （降低风控概率）；单个群失败不阻断其他群，最后汇总 ``PushResult[]``。
4. 容错：单条消息发送失败（传输错误 / 429 / 5xx）重试 1 次；仍失败记入该群 ok=False 继续下一群；
   返回体解析失败以 ok=False 记录，不抛异常中断整轮；
   网络不可达（NapCat 未启动）全部群失败，日志明确提示 "NapCat 未连接"。
5. **合并转发（2026-08-26 追加）**：配置 ``napcat.merge_forward: true`` 时改走
   POST {base_url}/send_forward_msg——每个文本段（保序）一个 node + 全部配图一个 node，
   合并为一条「合并聊天记录」（整条新闻只发一条消息）；发送者 uin/昵称取
   GET {base_url}/get_login_info（按 base_url 进程内缓存，失败回退 10001/爱马仕新闻）。
   环境变量覆盖：NAPCAT_MERGE_FORWARD（1/true/yes/on）。

可拓展性设计（与 M4 同思路）
----------------------------------------------------------------------------
1. 契约类型复用 ``src/models.py``（PushMessage / PushResult），本模块不重复定义；re-export 保持
   ``from m6_notifier import PushMessage`` 公共 API 可用。
2. ``push(message, *, config=None, client=None)`` 支持注入配置与 HTTP client：
   不传 ``config`` 自动 ``load_config()``（M8 将来可接管统一配置，接口不变）；
   不传 ``client`` 内部自建 ``httpx.Client``；测试可注入 fake client（零网络）。
3. 输入鸭子类型：``models.PushMessage`` / 任意含契约字段的 dataclass / dict 均可。
4. 配置三级覆盖（低→高）：内置默认 < config.yaml（或 config.json）的 ``napcat:`` 段 < .env < 环境变量。
   环境变量：NAPCAT_BASE_URL / NAPCAT_TOKEN / NAPCAT_GROUP_IDS（逗号分隔）/ NAPCAT_INTERVAL_SEC。
5. 网络层收敛在 ``_send_group_msg`` 单一接缝；重试语义：传输错误/429/5xx 重试 ``max_retries``
   （默认 1）次指数退避，鉴权等其他 4xx 快速失败。
6. 目标群来源：``message.group_ids`` 优先，为空时回退 ``config.group_ids``（配置默认值）。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

from models import PushMessage, PushResult  # 契约类型单一事实源（module-specs §1，见 src/models.py）

logger = logging.getLogger(__name__)

# 环境兜底：本机依赖已 vendor 化（沙箱无法 pip 安装），正常环境走系统 site-packages
try:
    import httpx
except ImportError:  # pragma: no cover
    _vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
    if os.path.isdir(_vendor):
        sys.path.insert(0, _vendor)
        import httpx
    else:
        raise

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
RETRY_BASE_DELAY = 1.0                # 指数退避基数（秒）：重试 1 次 → 1s
RETRYABLE_STATUS = {429, 500, 502, 503, 504}  # 可重试的 HTTP 状态

DEFAULT_BASE_URL = "http://127.0.0.1:3000"    # NapCat 默认 HTTP 地址
DEFAULT_INTERVAL_SEC = 1.5                    # 群间发送间隔（降低风控概率）
DEFAULT_TIMEOUT = 15.0                        # 单次请求超时（秒）
DEFAULT_MAX_RETRIES = 1                       # 单条消息失败重试次数（规格 §8：重试 1 次）


class PushError(RuntimeError):
    """推送失败（配置非法 / 网络重试耗尽 / API 错误 / 输入非法），message 面向日志与告警。"""


class RetryableError(PushError):
    """可重试错误（传输层 / 429 / 5xx）；重试耗尽后仍以 RetryableError 上抛。"""


# PushMessage / PushResult 契约类型已移至 src/models.py（统一 import 防漂移），
# 此处经 `from models import ...` re-export，保持 `from m6_notifier import ...` 公共 API 不变。


@dataclass
class NotifierConfig:
    """推送模块配置（load_config() 产出的单一配置面，M8 可无缝接管）。"""

    base_url: str = DEFAULT_BASE_URL          # NapCat 的 HTTP 地址（不含尾斜杠）
    token: str = ""                           # 可选，OneBot 鉴权 token（Bearer）
    group_ids: list[str] = field(default_factory=list)  # 默认目标群（M5/M7 传入时优先于 message.group_ids）
    interval_sec: float = DEFAULT_INTERVAL_SEC  # 群间发送间隔（秒）
    timeout: float = DEFAULT_TIMEOUT          # 单次请求超时（秒）
    max_retries: int = DEFAULT_MAX_RETRIES    # 单条消息失败重试次数，总尝试 = max_retries + 1
    merge_forward: bool = False               # 合并转发：文本段+图片合并为一条「合并聊天记录」（send_forward_msg）


# ---------------------------------------------------------------------------
# 配置加载：内置默认 < config.yaml/json < .env < 环境变量
# ---------------------------------------------------------------------------
def load_config(
    config_path: str | None = None,
    env_path: str | None = None,
) -> NotifierConfig:
    """按优先级合并配置并返回 NotifierConfig。

    :param config_path: config.yaml 路径（缺省取项目根 config.yaml，无则回退 config.json）
    :param env_path:    .env 路径（缺省取项目根 .env）
    """
    root = _project_root()
    cfg = NotifierConfig()

    # 1) 配置文件（config.yaml 优先，其次 config.json）的 napcat: 段
    file_cfg: dict = {}
    path = config_path or os.path.join(root, "config.yaml")
    if os.path.isfile(path):
        file_cfg = _read_config_file(path)
    elif config_path is None:
        alt = os.path.join(root, "config.json")
        if os.path.isfile(alt):
            file_cfg = _read_config_file(alt)
    n = file_cfg.get("napcat") if isinstance(file_cfg.get("napcat"), dict) else {}
    if v := n.get("base_url"):
        cfg.base_url = str(v).rstrip("/")
    if v := n.get("token"):
        cfg.token = str(v)
    if v := n.get("group_ids"):
        cfg.group_ids = _coerce_group_ids(v)
    _set_float(cfg, "interval_sec", n.get("interval_sec"))
    _set_float(cfg, "timeout", n.get("timeout"))
    _set_int(cfg, "max_retries", n.get("max_retries"))
    _v = n.get("merge_forward")
    if _v is not None:
        cfg.merge_forward = bool(_v) if isinstance(_v, bool) else str(_v).lower() in ("1", "true", "yes", "on")

    # 2) .env 文件（python-dotenv 优先，缺省用内置轻量解析）
    env = _load_env_file(env_path or os.path.join(root, ".env"))

    # 3) 环境变量（优先级最高）：os.environ > .env 文件
    def _pick(name: str) -> str | None:
        return os.environ.get(name) or env.get(name)

    if v := _pick("NAPCAT_BASE_URL"):
        cfg.base_url = v.strip().rstrip("/")
    if v := _pick("NAPCAT_TOKEN"):
        cfg.token = v.strip()
    if v := _pick("NAPCAT_GROUP_IDS"):
        ids = [x.strip() for x in v.split(",") if x.strip()]
        if ids:
            cfg.group_ids = ids
    if v := _pick("NAPCAT_INTERVAL_SEC"):
        try:
            cfg.interval_sec = float(v)
        except ValueError:
            pass  # 非法值忽略，保留默认
    if v := _pick("NAPCAT_MERGE_FORWARD"):
        cfg.merge_forward = v.strip().lower() in ("1", "true", "yes", "on")
    return cfg


def _set_float(cfg: NotifierConfig, attr: str, value) -> None:
    try:
        setattr(cfg, attr, float(value))
    except (TypeError, ValueError):
        pass


def _set_int(cfg: NotifierConfig, attr: str, value) -> None:
    try:
        setattr(cfg, attr, int(value))
    except (TypeError, ValueError):
        pass


def _coerce_group_ids(v) -> list[str]:
    """把配置里的 group_ids 统一成 list[str]。

    YAML 子集解析器（与 M4 同法）不支持内联列表，``["111", "222"]`` 会解析为
    裸字符串；此处兼容三种形态：真 list / JSON 数组字符串 / 逗号分隔字符串。
    """
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except ValueError:
                pass
        return [x.strip() for x in s.split(",") if x.strip()]
    return []


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_config_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise PushError(f"配置文件读取失败: {path}: {exc}") from exc
    if path.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise PushError(f"配置文件 JSON 解析失败: {path}: {exc}") from exc
    else:
        data = _parse_yaml_subset(text)
    return data if isinstance(data, dict) else {}


def _load_env_file(path: str) -> dict:
    """读取 .env 为 dict；python-dotenv 可用则用它，否则内置轻量解析。"""
    if not path or not os.path.isfile(path):
        return {}
    try:
        import dotenv  # type: ignore
    except ImportError:  # pragma: no cover
        dotenv = None  # type: ignore
    if dotenv is not None:
        try:
            values = dotenv.dotenv_values(path)
            return {k: (v if v is not None else "") for k, v in values.items()}
        except Exception:  # noqa: BLE001 — 任何异常回退轻量解析
            pass
    env: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if not key:
                    continue
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                env[key] = val
    except OSError:
        pass
    return env


# ---------------------------------------------------------------------------
# YAML 子集解析（config.yaml 专用，与 M4 同法：避免引入 PyYAML 依赖）
# ---------------------------------------------------------------------------
def _parse_yaml_subset(text: str) -> dict:
    """解析本项目用到的 YAML 子集：``#`` 注释、``key: value`` 标量、
    2 空格缩进嵌套映射、引号/整数/浮点/布尔/null/裸字符串。

    不支持列表、多行字符串、锚点等（本项目配置用不到；需要时改用 config.json）。
    """
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]  # (缩进, 所在映射)
    for raw in text.splitlines():
        line = _strip_yaml_comment(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if ":" not in body:
            continue  # 容错：跳过无法解析的行
        key, _, val = body.partition(":")
        key = key.strip().strip('"').strip("'")
        if not key:
            continue
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        val = val.strip()
        node: object = _yaml_scalar(val) if val else {}
        parent[key] = node
        if isinstance(node, dict):
            stack.append((indent, node))
    return root


def _strip_yaml_comment(line: str) -> str:
    """去掉行尾 `` #`` 注释（引号内的 # 不处理；行首 # 整行注释）。"""
    in_s = in_d = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _yaml_scalar(val: str):
    """YAML 子集标量 -> Python 值。"""
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        inner = val[1:-1]
        return inner.replace('\\"', '"') if val[0] == '"' else inner
    low = val.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", "~"):
        return None
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


# ---------------------------------------------------------------------------
# 输入归一化（鸭子类型：本类 / 任意契约 dataclass / dict）
# ---------------------------------------------------------------------------
_MESSAGE_FIELDS = ("group_ids", "segments", "images", "link", "ats")


def _coerce_message(message) -> PushMessage:
    if isinstance(message, PushMessage):
        return message
    if isinstance(message, dict):
        src: dict = message
    elif dataclasses.is_dataclass(message) and not isinstance(message, type):
        src = {f.name: getattr(message, f.name) for f in dataclasses.fields(message)}
    else:
        raise PushError(
            f"不支持的 PushMessage 输入类型: {type(message).__name__}"
            "（需 dict 或含契约字段的 dataclass）"
        )
    missing = [k for k in _MESSAGE_FIELDS if k not in src]
    if missing:
        raise PushError(f"PushMessage 缺少字段: {missing}")
    return PushMessage(
        group_ids=[str(x) for x in (src.get("group_ids") or [])],
        segments=[str(x) for x in (src.get("segments") or [])],
        images=[str(x) for x in (src.get("images") or [])],
        link=str(src.get("link") or ""),
        ats=[str(x) for x in (src.get("ats") or [])],
    )


def _parse_group_id(group_id: str) -> int:
    """群号字符串 -> OneBot 请求体的 int group_id；非数字抛出 PushError。

    契约要求群号用字符串存储（QQ 群号可能超 32 位整数范围）；OneBot 11 的
    group_id 字段为 JSON number（NapCat 按 int64 处理），Python int 无范围问题。
    """
    try:
        return int(group_id)
    except (TypeError, ValueError) as exc:
        raise PushError(f"非法群号: {group_id!r}（须为纯数字字符串）") from exc


# ---------------------------------------------------------------------------
# OneBot 11 消息构建
# ---------------------------------------------------------------------------
def _text_segment(text: str) -> dict:
    return {"type": "text", "data": {"text": text}}


def _image_segment(url: str) -> dict:
    return {"type": "image", "data": {"file": url}}


# ---------------------------------------------------------------------------
# OneBot 11 调用层（单一网络接缝）
# ---------------------------------------------------------------------------
def _post_api(client, cfg: NotifierConfig, endpoint: str, payload: dict) -> str:
    """POST OneBot 11 接口并解析 ``data.message_id``（网络层单一接缝）。

    传输错误 / 429 / 5xx -> RetryableError；其余 4xx（鉴权等）-> PushError。
    返回体解析失败也以 PushError 上抛（由调用方记入该群失败，不中断整轮）。
    """
    url = f"{cfg.base_url}/{endpoint}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg.token:
        headers["Authorization"] = f"Bearer {cfg.token}"
    try:
        resp = client.post(url, json=payload, headers=headers, timeout=cfg.timeout)
    except httpx.TransportError as exc:
        raise RetryableError(f"OneBot 网络错误（{url}）: {exc}") from exc
    if resp.status_code >= 400:
        body = _extract_error_text(resp)
        if resp.status_code in RETRYABLE_STATUS or resp.status_code >= 500:
            raise RetryableError(f"OneBot API 错误 HTTP {resp.status_code}: {body}")
        raise PushError(f"OneBot API 错误 HTTP {resp.status_code}: {body}")
    message_id = _parse_message_id(resp)
    if message_id is None:
        raise PushError(f"OneBot 返回体解析失败: {resp.text[:200]}")
    return message_id


def _send_group_msg(client, cfg: NotifierConfig, group_id: int, segments: list[dict]) -> str:
    """调用 POST {base_url}/send_group_msg，返回 ``data.message_id``（字符串）。"""
    return _post_api(client, cfg, "send_group_msg", {"group_id": group_id, "message": segments})


def _send_forward_msg(client, cfg: NotifierConfig, group_id: int, nodes: list[dict]) -> str:
    """调用 POST {base_url}/send_forward_msg（合并聊天记录），返回 ``data.message_id``（字符串）。"""
    return _post_api(client, cfg, "send_forward_msg", {"group_id": group_id, "messages": nodes})


def _extract_error_text(resp) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            msg = data.get("message") or data.get("msg")
            if msg:
                return str(msg)
            retcode = data.get("retcode")
            if retcode is not None:
                return f"retcode={retcode}"
    except ValueError:
        pass
    return resp.text[:200]


def _parse_message_id(resp) -> str | None:
    """从 OneBot 11 响应提取 message_id；status!=ok / 缺字段 / 非 JSON -> None。"""
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    if status is not None and status != "ok":
        return None
    retcode = data.get("retcode")
    if retcode is not None and retcode != 0:
        return None
    msg_id = (data.get("data") or {}).get("message_id") if isinstance(data.get("data"), dict) else None
    if msg_id is None:
        return None
    return str(msg_id)


def _call_with_retry(client, cfg: NotifierConfig, group_id: int, segments: list[dict]) -> str:
    """传输错误/429/5xx 重试 max_retries 次（指数退避）；耗尽后原样上抛。"""
    last_err: RetryableError | None = None
    for attempt in range(cfg.max_retries + 1):
        try:
            return _send_group_msg(client, cfg, group_id, segments)
        except RetryableError as exc:
            last_err = exc
            if attempt < cfg.max_retries:
                time.sleep(RETRY_BASE_DELAY * (2**attempt))
    assert last_err is not None
    raise last_err


# ---------------------------------------------------------------------------
# 合并转发（send_forward_msg）：文本段 + 配图合并为一条「合并聊天记录」
# ---------------------------------------------------------------------------
_SELF_CACHE: dict[str, tuple[str, str]] = {}  # base_url -> (uin, nickname)，进程内缓存


def _get_self_info(client, cfg: NotifierConfig) -> tuple[str, str]:
    """查询登录 bot 的 uin/昵称（合并转发 node 需要）；失败回退占位。结果按 base_url 缓存。"""
    cached = _SELF_CACHE.get(cfg.base_url)
    if cached:
        return cached
    uin, name = "10001", "爱马仕新闻"
    try:
        resp = client.get(f"{cfg.base_url}/get_login_info", timeout=cfg.timeout)
        if resp.status_code == 200:
            data = resp.json().get("data") or {}
            if data.get("user_id"):
                uin, name = str(data["user_id"]), str(data.get("nickname") or "爱马仕新闻")
    except Exception:  # noqa: BLE001 — 查询失败回退占位，不阻断
        pass
    _SELF_CACHE[cfg.base_url] = (uin, name)
    return uin, name


def _build_forward_nodes(msg: PushMessage, uin: str, name: str) -> list[dict]:
    """把文本段 + 配图合并为「合并聊天记录」的 node 列表（保序）。

    每个文本段一个 node（M5 已按 3500 字/段落边界分片，作为独立气泡）；全部配图合并为一个 node。
    """
    nodes: list[dict] = []
    ats = getattr(msg, "ats", None) or []
    for i, seg in enumerate(msg.segments):
        content = [_text_segment(seg)]
        if i == 0 and ats:
            content = [{"type": "at", "data": {"qq": str(q)}} for q in ats] + content
        nodes.append({"type": "node", "data": {"uin": uin, "name": name, "content": content}})
    if msg.images:
        content = [_image_segment(u) for u in msg.images]
        if ats:
            content = [{"type": "at", "data": {"qq": str(q)}} for q in ats] + content
        nodes.append({"type": "node", "data": {"uin": uin, "name": name, "content": content}})
    return nodes


def _call_forward_with_retry(client, cfg: NotifierConfig, group_id: int, nodes: list[dict]) -> str:
    """合并转发发送 + 重试（语义同 _call_with_retry）。"""
    last_err: RetryableError | None = None
    for attempt in range(cfg.max_retries + 1):
        try:
            return _send_forward_msg(client, cfg, group_id, nodes)
        except RetryableError as exc:
            last_err = exc
            if attempt < cfg.max_retries:
                time.sleep(RETRY_BASE_DELAY * (2**attempt))
    assert last_err is not None
    raise last_err


def _push_one_group_merged(client, cfg: NotifierConfig, group: str, msg: PushMessage) -> PushResult:
    """合并转发模式：整条新闻（文本段 + 配图）合并为一条「合并聊天记录」发送。

    单群失败记入结果并继续（不抛出）；失败原因（网络/API/构建）写入 error。
    """
    try:
        gid = _parse_group_id(group)
    except PushError as exc:
        return PushResult(group_id=group, ok=False, message_id="", error=str(exc))
    try:
        uin, name = _get_self_info(client, cfg)
        nodes = _build_forward_nodes(msg, uin, name)
    except Exception as exc:  # noqa: BLE001 — 构建失败不中断整轮
        return PushResult(group_id=group, ok=False, message_id="", error=f"合并消息构建失败: {exc}")
    if not nodes:
        return PushResult(group_id=group, ok=False, message_id="", error="空消息（无文本段也无图片）")
    try:
        message_id = _call_forward_with_retry(client, cfg, gid, nodes)
        return PushResult(group_id=group, ok=True, message_id=message_id)
    except PushError as exc:
        return PushResult(group_id=group, ok=False, message_id="", error=str(exc))


# ---------------------------------------------------------------------------
# 入口（契约签名冻结）
# ---------------------------------------------------------------------------
def push(
    message,
    *,
    config: NotifierConfig | None = None,
    client=None,
) -> list[PushResult]:
    """把组装好的消息推送到目标群，返回每个群一条 ``PushResult``。

    :param message: PushMessage（本类 / 任意契约 dataclass / dict）
    :param config:  NotifierConfig；缺省自动 load_config()
    :param client:  可注入 httpx.Client（或同接口 fake），缺省内部自建
    :return: list[PushResult]，顺序与群列表一致；单群失败不阻断其他群
    """
    cfg = config or load_config()
    msg = _coerce_message(message)
    groups = list(msg.group_ids) or list(cfg.group_ids)
    if not groups:
        logger.warning("push: 无目标群（message.group_ids 与 config.group_ids 均为空），跳过")
        return []

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=cfg.timeout)
    results: list[PushResult] = []
    net_down = False  # 本轮回是否有网络层错误（用于"NapCat 未连接"提示）
    try:
        for idx, group in enumerate(groups):
            result = _push_one_group(client, cfg, group, msg)
            results.append(result)
            if result.error and "网络错误" in (result.error or ""):
                net_down = True
            if idx < len(groups) - 1:
                time.sleep(cfg.interval_sec)
    finally:
        if own_client:
            client.close()

    if net_down:
        logger.warning("NapCat 未连接：%s 不可达，请确认 NapCatQQ 已启动且 base_url 正确", cfg.base_url)
    return results


def _push_one_group(client, cfg: NotifierConfig, group: str, msg: PushMessage) -> PushResult:
    """向单个群完整发送（合并转发模式 或 逐条文本+图片）；失败记入结果并继续，不抛出。"""
    if cfg.merge_forward:
        return _push_one_group_merged(client, cfg, group, msg)
    try:
        gid = _parse_group_id(group)
    except PushError as exc:
        return PushResult(group_id=group, ok=False, message_id="", error=str(exc))

    try:
        # 每条消息 = 一个段列表：文本段各自一条（单元素），图片合并成一条多图消息
        messages: list[list[dict]] = [[_text_segment(seg)] for seg in msg.segments]
        if msg.images:
            messages.append([_image_segment(url) for url in msg.images])
        # @ 归属（songbot 回复用）：拼为独立 at 段附在第一条消息前（与文本/图片同一条发出）。
        # 2026-08-27 修正：CQ 码嵌 text 在 NapCat array 消息形态下会字面显示，须用独立 at 段。
        ats = getattr(msg, "ats", None) or []
        if ats and messages:
            messages[0] = [{"type": "at", "data": {"qq": str(q)}} for q in ats] + messages[0]
    except Exception as exc:  # noqa: BLE001 — 构建消息段不应中断整轮
        return PushResult(group_id=group, ok=False, message_id="", error=f"消息段构建失败: {exc}")

    # 空消息防御（M5 正常保证至少 1 段；防御边界）
    if not messages:
        return PushResult(group_id=group, ok=False, message_id="", error="空消息（无文本段也无图片）")

    first_message_id = ""
    first_error: Optional[str] = None
    all_ok = True
    for seg in messages:
        try:
            message_id = _call_with_retry(client, cfg, gid, seg)
            if not first_message_id:
                first_message_id = message_id
        except PushError as exc:
            all_ok = False
            if first_error is None:
                first_error = str(exc)
            logger.warning("push 群 %s 消息发送失败: %s", group, exc)
    return PushResult(
        group_id=group,
        ok=all_ok,
        message_id=first_message_id,
        error=first_error,
    )


# ---------------------------------------------------------------------------
# 命令行自测：python src/m6_notifier.py [--dry-run] [--config napcat.yaml] [group1 group2 ...]
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="M6 QQ 推送自测")
    parser.add_argument("--dry-run", action="store_true", help="不发真实消息，只打印将发送的内容与配置")
    parser.add_argument("--merge", action="store_true", help="合并转发：整条消息合并为一条聊天记录（send_forward_msg）")
    parser.add_argument("--config", default=None, help="配置文件路径（缺省项目根 config.yaml）")
    parser.add_argument("groups", nargs="*", help="覆盖目标群号（缺省用 message/config 的群）")
    args = parser.parse_args(argv)

    cfg = load_config(config_path=args.config)
    if args.merge:
        cfg.merge_forward = True
    groups = args.groups or cfg.group_ids
    msg = PushMessage(
        group_ids=groups,
        segments=[
            "【NEWS】2026-08-26\n【イベント】サンプル タイトル\n\n——— 中文翻译 ———\n【活动】样例标题\n样例正文。",
        ],
        images=[],
        link="https://idolmaster-official.jp/news/01_17821",
    )

    if args.dry_run:
        print("[DRY-RUN] 配置：")
        print(f"  base_url    = {cfg.base_url}")
        print(f"  token       = {'***' if cfg.token else '(无)'}")
        print(f"  group_ids   = {cfg.group_ids}")
        print(f"  interval    = {cfg.interval_sec}s")
        print(f"  timeout     = {cfg.timeout}s, max_retries = {cfg.max_retries}")
        print("[DRY-RUN] 将发送：")
        for g in groups:
            for i, seg in enumerate(msg.segments, 1):
                print(f"  → 群 {g} 文本段 {i}/{len(msg.segments)}（{len(seg)} 字符）")
            if msg.images:
                print(f"  → 群 {g} 图片 {len(msg.images)} 张（合并一条多图消息）")
        print("[DRY-RUN] 完成（未发送任何消息）")
        return 0

    try:
        results = push(msg, config=cfg)
    except PushError as exc:
        print(f"[ERROR] {exc}")
        return 1
    ok = all(r.ok for r in results)
    for r in results:
        print(f"  群 {r.group_id}: ok={r.ok} message_id={r.message_id!r} error={r.error!r}")
    if not results:
        print("[WARN] 无目标群，未发送")
        return 1
    print("[ALL PASS]" if ok else "[FAILED]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main())
