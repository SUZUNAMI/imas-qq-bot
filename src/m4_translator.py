"""M4 翻译（Translator，DeepSeek）— 爱马仕官方新闻 QQ 转发机器人.

输入 ``NewsDetail``（M2 输出，日文），调用 DeepSeek chat 接口日译中，
输出 ``TranslationResult``（契约 docs/module-specs.md §1.3，字段名冻结勿改）。
契约类型统一来自 ``src/models.py``（单一事实源，防接口漂移）。

规格：docs/modules/M4-translator.md（自包含交接文档）。

可拓展性设计
----------------------------------------------------------------------------
1. 契约类型复用 ``src/models.py``（NewsDetail / TranslationResult），本模块不重复定义；
   re-export 保持 ``from m4_translator import NewsDetail`` 可用（与 m1 同法）。
2. ``translate(detail, *, config=None, client=None)`` 支持注入配置与 HTTP client：
   - 不传 ``config`` 时自动 ``load_config()``（M8 将来可接管统一配置，接口不变）；
   - 不传 ``client`` 时内部自建 ``httpx.Client``；测试可注入 fake client。
3. 输入鸭子类型：``models.NewsDetail`` / 任意含契约字段的 dataclass（M2 并行期自己的类）/ dict 均可。
4. 配置三级覆盖（低→高）：内置默认 < config.yaml（或 config.json）< .env < 环境变量。
   环境变量：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL / DEEPSEEK_TEMPERATURE。
5. 术语表：内置默认（DEFAULT_TERMS）+ config.yaml 的 ``terms:`` 段 + terms.json 逐层覆盖扩充。
6. 网络层收敛在 ``_chat_completion`` 单一接缝；重试语义：传输错误/429/5xx 重试
   ``max_retries`` 次（指数退避），鉴权/余额等其他 4xx 快速失败；JSON 解析失败再试一次后
   回退纯文本（title_zh 复制原文标题，body_zh 用原始返回文本）。
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import time
from dataclasses import dataclass, field

from models import NewsDetail, TranslationResult  # 契约类型单一事实源（module-specs §1，见 src/models.py）

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

try:
    import dotenv  # type: ignore
except ImportError:  # pragma: no cover
    dotenv = None  # type: ignore  # 缺省时 _load_env_file 走内置轻量解析，不阻断

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"  # 默认端点（兼容 /v1 前缀）
RETRY_BASE_DELAY = 2.0      # 指数退避基数（秒）：重试 2 次 → 2s、4s
RETRYABLE_STATUS = {429, 500, 502, 503, 504}  # 可重试的 HTTP 状态

DEFAULT_TERMS: dict[str, str] = {
    "アイドルマスター": "偶像大师",
    "アイマス": "爱马仕",
    "シャイニーカラーズ": "闪耀色彩",
    "ミリオンライブ": "百万现场",
    "シンデレラガールズ": "灰姑娘女孩",
    "SideM": "SideM",
    "学園アイドルマスター": "学园偶像大师",
}


class TranslationError(RuntimeError):
    """翻译失败（缺 Key / 网络重试耗尽 / API 错误 / 输入非法），message 面向日志与告警。"""


class RetryableError(TranslationError):
    """可重试错误（传输层 / 429 / 5xx）；重试耗尽后仍以 RetryableError 上抛。"""


# NewsDetail / TranslationResult 契约类型已移至 src/models.py（统一 import 防漂移），
# 此处经 `from models import ...` re-export，保持 `from m4_translator import ...` 公共 API 不变。


@dataclass
class TranslatorConfig:
    """翻译模块配置（load_config() 产出的单一配置面，M8 可无缝接管）。"""

    api_key: str = ""                       # DEEPSEEK_API_KEY
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = 0.3                # 翻译要稳定，不宜高
    timeout: float = 60.0                   # 单次请求超时（秒）
    max_retries: int = 2                    # 请求失败重试次数（指数退避），总尝试 = max_retries + 1
    terms: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TERMS))


# ---------------------------------------------------------------------------
# 配置加载：内置默认 < config.yaml/json < .env < 环境变量
# ---------------------------------------------------------------------------
def load_config(
    config_path: str | None = None,
    env_path: str | None = None,
    terms_path: str | None = None,
) -> TranslatorConfig:
    """按优先级合并配置并返回 TranslatorConfig。

    :param config_path: config.yaml 路径（缺省取项目根 config.yaml，无则回退 config.json）
    :param env_path:    .env 路径（缺省取项目根 .env）
    :param terms_path:  术语表 JSON 路径（缺省取项目根 terms.json；config 里 terms_file 优先于此参数）
    """
    root = _project_root()
    cfg = TranslatorConfig()

    # 1) 配置文件（config.yaml 优先，其次 config.json）
    file_cfg: dict = {}
    path = config_path or os.path.join(root, "config.yaml")
    if os.path.isfile(path):
        file_cfg = _read_config_file(path)
    elif config_path is None:
        alt = os.path.join(root, "config.json")
        if os.path.isfile(alt):
            file_cfg = _read_config_file(alt)
    t = file_cfg.get("translator") if isinstance(file_cfg.get("translator"), dict) else {}
    cfg.base_url = str(t.get("base_url") or cfg.base_url).rstrip("/")
    cfg.model = str(t.get("model") or cfg.model)
    _set_float(cfg, "temperature", t.get("temperature"))
    _set_float(cfg, "timeout", t.get("timeout"))
    _set_int(cfg, "max_retries", t.get("max_retries"))
    terms = file_cfg.get("terms")
    if isinstance(terms, dict):
        cfg.terms.update({str(k): str(v) for k, v in terms.items()})
    terms_file = t.get("terms_file") or terms_path

    # 2) 术语表文件（terms.json，覆盖/扩充内置与 config.yaml 的 terms）
    tf = terms_file or os.path.join(root, "terms.json")
    if os.path.isfile(tf):
        try:
            with open(tf, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise TranslationError(f"术语表文件解析失败: {tf}: {exc}") from exc
        if isinstance(data, dict):
            cfg.terms.update({str(k): str(v) for k, v in data.items()})

    # 3) .env 文件（python-dotenv 优先，缺省用内置轻量解析）
    env = _load_env_file(env_path or os.path.join(root, ".env"))

    # 4) 环境变量（优先级最高）：os.environ > .env 文件
    def _pick(name: str) -> str | None:
        return os.environ.get(name) or env.get(name)

    if v := _pick("DEEPSEEK_API_KEY"):
        cfg.api_key = v.strip()
    if v := _pick("DEEPSEEK_BASE_URL"):
        cfg.base_url = v.strip().rstrip("/")
    if v := _pick("DEEPSEEK_MODEL"):
        cfg.model = v.strip()
    if v := _pick("DEEPSEEK_TEMPERATURE"):
        try:
            cfg.temperature = float(v)
        except ValueError:
            pass  # 非法值忽略，保留默认
    return cfg


def _set_float(cfg: TranslatorConfig, attr: str, value) -> None:
    try:
        setattr(cfg, attr, float(value))
    except (TypeError, ValueError):
        pass


def _set_int(cfg: TranslatorConfig, attr: str, value) -> None:
    try:
        setattr(cfg, attr, int(value))
    except (TypeError, ValueError):
        pass


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_config_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise TranslationError(f"配置文件读取失败: {path}: {exc}") from exc
    if path.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise TranslationError(f"配置文件 JSON 解析失败: {path}: {exc}") from exc
    else:
        data = _parse_yaml_subset(text)
    return data if isinstance(data, dict) else {}


def _load_env_file(path: str) -> dict:
    """读取 .env 为 dict；python-dotenv 可用则用它，否则内置轻量解析。"""
    if not path or not os.path.isfile(path):
        return {}
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
# YAML 子集解析（config.yaml 专用，避免引入 PyYAML 依赖）
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
# Prompt 构建（系统提示词固化 + 术语表注入）
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_TEMPLATE = (
    "你是专业的日语→简体中文翻译。翻译爱马仕（アイドルマスター）系列官方新闻。\n"
    "要求：\n"
    "1. 忠实原文、通顺自然、符合中文阅读习惯；\n"
    "2. 保留原文段落结构（段落间用空行分隔）；\n"
    "3. 专有名词按以下术语表翻译，未列出的保持原文或用通用译名：\n"
    "{terms}\n"
    "4. 只输出 JSON：{{\"title_zh\": \"...\", \"body_zh\": \"...\"}}，不要输出任何多余文字。"
)


def _build_system_prompt(terms: dict[str, str]) -> str:
    lines = "\n".join(f"   - {k} → {v}" for k, v in (terms or {}).items())
    return SYSTEM_PROMPT_TEMPLATE.format(terms=lines)


def _build_user_message(detail: NewsDetail) -> str:
    return f"【标题】\n{detail.title}\n\n【正文】\n{detail.body_text}"


def _build_messages(detail: NewsDetail, terms: dict[str, str]) -> list[dict]:
    return [
        {"role": "system", "content": _build_system_prompt(terms)},
        {"role": "user", "content": _build_user_message(detail)},
    ]


# ---------------------------------------------------------------------------
# 输入归一化（鸭子类型：本类 / 任意契约 dataclass / dict）
# ---------------------------------------------------------------------------
_DETAIL_FIELDS = ("id", "url", "title", "date", "body_text")


def _coerce_detail(detail) -> NewsDetail:
    if isinstance(detail, NewsDetail):
        return detail
    if isinstance(detail, dict):
        src: dict = detail
    elif dataclasses.is_dataclass(detail) and not isinstance(detail, type):
        src = {f.name: getattr(detail, f.name) for f in dataclasses.fields(detail)}
    else:
        raise TranslationError(
            f"不支持的 NewsDetail 输入类型: {type(detail).__name__}"
            "（需 dict 或含契约字段的 dataclass）"
        )
    missing = [k for k in _DETAIL_FIELDS if k not in src]
    if missing:
        raise TranslationError(f"NewsDetail 缺少字段: {missing}")
    return NewsDetail(
        id=str(src["id"]),
        url=str(src["url"]),
        title=str(src["title"]),
        date=str(src["date"]),
        body_text=str(src.get("body_text") or ""),
        images=list(src.get("images") or []),
    )


# ---------------------------------------------------------------------------
# DeepSeek 调用层（单一网络接缝）
# ---------------------------------------------------------------------------
def _chat_endpoint(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def _extract_api_error(resp) -> str:
    try:
        data = resp.json()
        err = data.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else None
        if msg:
            return str(msg)
    except ValueError:
        pass
    return resp.text[:200]


def _chat_completion(client, cfg: TranslatorConfig, messages: list[dict]) -> str:
    """调用 OpenAI 兼容 chat/completions，返回 ``choices[0].message.content``。

    传输错误 / 429 / 5xx -> RetryableError；其余 4xx（鉴权、余额等）-> TranslationError。
    """
    url = _chat_endpoint(cfg.base_url)
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        resp = client.post(url, json=payload, headers=headers, timeout=cfg.timeout)
    except httpx.TransportError as exc:
        raise RetryableError(f"DeepSeek API 网络错误: {exc}") from exc
    if resp.status_code >= 400:
        err = _extract_api_error(resp)
        if resp.status_code in RETRYABLE_STATUS or resp.status_code >= 500:
            raise RetryableError(f"DeepSeek API 错误 HTTP {resp.status_code}: {err}")
        raise TranslationError(f"DeepSeek API 错误 HTTP {resp.status_code}: {err}")
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise TranslationError(f"DeepSeek 响应格式异常: {resp.text[:300]}") from exc
    if not isinstance(content, str):
        raise TranslationError(f"DeepSeek 响应 content 非字符串: {content!r}")
    return content


def _call_with_retry(client, cfg: TranslatorConfig, messages: list[dict]) -> str:
    """传输错误/429/5xx 重试 max_retries 次（指数退避）；耗尽后原样上抛。"""
    last_err: RetryableError | None = None
    for attempt in range(cfg.max_retries + 1):
        try:
            return _chat_completion(client, cfg, messages)
        except RetryableError as exc:
            last_err = exc
            if attempt < cfg.max_retries:
                time.sleep(RETRY_BASE_DELAY * (2**attempt))
    assert last_err is not None
    raise last_err


# ---------------------------------------------------------------------------
# 响应解析（JSON 优先，含代码围栏剥离；失败回退纯文本）
# ---------------------------------------------------------------------------
def _parse_json_object(text: str) -> dict | None:
    """把模型返回文本解析为 dict；非 JSON 或非对象返回 None（容忍 ```json 围栏）。"""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    try:
        obj = json.loads(t)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _parse_translation(text: str) -> TranslationResult | None:
    """解析为 TranslationResult；非 JSON / 缺 title_zh|body_zh 字段 / 类型不符 -> None。"""
    obj = _parse_json_object(text)
    if obj is None:
        return None
    title_zh = obj.get("title_zh")
    body_zh = obj.get("body_zh")
    if not isinstance(title_zh, str) or not isinstance(body_zh, str):
        return None
    return TranslationResult(title_zh=title_zh, body_zh=body_zh)


def _strip_code_fence(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# 入口（契约签名冻结）
# ---------------------------------------------------------------------------
def translate(
    detail,
    *,
    config: TranslatorConfig | None = None,
    client=None,
) -> TranslationResult:
    """把新闻详情（日文）翻译为中文。

    :param detail: NewsDetail（本类 / 任意契约 dataclass / dict）
    :param config: TranslatorConfig；缺省自动 load_config()
    :param client: 可注入 httpx.Client（或同接口 fake），缺省内部自建
    :raises TranslationError: 缺 Key / 网络重试耗尽 / API 错误 / 输入非法
    """
    cfg = config or load_config()
    if not cfg.api_key:
        raise TranslationError("未配置 DEEPSEEK_API_KEY：请在项目根 .env 设置（参考 .env.example）")
    detail = _coerce_detail(detail)
    messages = _build_messages(detail, cfg.terms)

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=cfg.timeout)
    try:
        text = _call_with_retry(client, cfg, messages)
        result = _parse_translation(text)
        if result is None:
            # JSON 解析失败：再试一次；仍失败则回退纯文本（title 复制原文，body 用原始返回）
            text = _call_with_retry(client, cfg, messages)
            result = _parse_translation(text)
        if result is not None:
            return result
        return TranslationResult(title_zh=detail.title, body_zh=_strip_code_fence(text))
    finally:
        if own_client:
            client.close()


# ---------------------------------------------------------------------------
# 命令行自测：python src/m4_translator.py [detail.json]
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = load_config()
    if not cfg.api_key:
        print("[ERROR] 未配置 DEEPSEEK_API_KEY（请在项目根 .env 设置，参考 .env.example）")
        return 1
    if argv:
        path = argv[0]
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"[ERROR] 读取 {path} 失败: {exc}")
            return 1
        detail = _coerce_detail(data)
    else:
        detail = NewsDetail(
            id="sample",
            url="https://idolmaster-official.jp/news/sample",
            title="【イベント】アイドルマスター 新情報発表会 開催決定！",
            date="2026-08-26",
            body_text="第一段落のテキスト。\n\n第二段落のテキスト。",
            images=[],
        )
    try:
        result = translate(detail, config=cfg)
    except TranslationError as exc:
        print(f"[ERROR] {exc}")
        return 1
    print(json.dumps({"title_zh": result.title_zh, "body_zh": result.body_zh}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
