# M4 翻译（Translator，DeepSeek）— 交接规格

> 项目：爱马仕官方新闻 QQ 转发机器人。追踪 https://idolmaster-official.jp/news，新新闻发布后推送「原文 + AI 日译中」到 QQ 群。
> 技术栈：Python 3.11+。
> 本文件**自包含**：只读本文件即可实现本模块，无需读其他模块文档。
> 契约冻结：输入/输出结构必须严格符合本文件定义；如需改动，回改 `docs/module-specs.md` §1。

## 1. 本模块在流水线中的位置

```
… ──► M2 详情解析 ──NewsDetail──► M4 翻译 ──TranslationResult──► M5 组装 ──► …
```

本模块：把新闻详情（日文）翻译成中文，输出标题译文 + 正文译文。

## 2. 输入契约：`NewsDetail`

```python
from dataclasses import dataclass

@dataclass
class NewsDetail:
    id: str
    url: str
    title: str       # 原文日文标题
    date: str        # "YYYY-MM-DD"
    body_text: str   # 正文纯文本（段落用 "\n\n" 分隔）
    images: list[str]  # 本模块不用，忽略
```

## 3. 输出契约：`TranslationResult`

```python
from dataclasses import dataclass

@dataclass
class TranslationResult:
    title_zh: str   # 标题译文
    body_zh: str    # 正文译文（段落结构尽量与原文对齐，用 "\n\n" 分隔）
```

## 4. 配置与密钥

- API Key 放 `.env`：`DEEPSEEK_API_KEY=sk-...`（用 `python-dotenv` 读取，**绝不硬编码、不进 git**）。
- 接口参数（可在 `config.yaml` 覆盖）：
  - `base_url`: `https://api.deepseek.com`（或 `https://api.deepseek.com/v1`）
  - `model`: `deepseek-chat`
  - `temperature`: `0.3`（翻译要稳定，不宜高）

## 5. 实现约定

- 文件：`src/m4_translator.py`
- 依赖：`httpx`（或 `openai` 库，用 DeepSeek 的 OpenAI 兼容接口）、`python-dotenv`。
- 入口函数签名：`def translate(detail: NewsDetail) -> TranslationResult:`

## 6. Prompt 与输出格式（关键）

用 chat 接口，`response_format={"type": "json_object"}`（或明确要求返回 JSON），让模型输出：

```json
{ "title_zh": "标题译文", "body_zh": "正文译文" }
```

**System prompt 建议**：

```
你是专业的日语→简体中文翻译。翻译爱马仕（アイドルマスター）系列官方新闻。
要求：
1. 忠实原文、通顺自然、符合中文阅读习惯；
2. 保留原文段落结构（段落间用空行分隔）；
3. 专有名词按以下术语表翻译，未列出的保持原文或用通用译名；
4. 只输出 JSON：{"title_zh": "...", "body_zh": "..."}，不要输出任何多余文字。
```

**术语表**（放 `config.yaml` 或 `terms.json`，随 prompt 注入；示例，可扩充）：

```json
{
  "アイドルマスター": "偶像大师",
  "アイマス": "爱马仕",
  "シャイニーカラーズ": "闪耀色彩",
  "ミリオンライブ": "百万现场",
  "シンデレラガールズ": "灰姑娘女孩",
  "SideM": "SideM",
  "学園アイドルマスター": "学园偶像大师"
}
```

## 7. 容错

- 请求失败：重试 2 次（指数退避），仍失败则抛出明确异常（由 M7 主控决定重试/记录）。
- 返回 JSON 解析失败：再试一次；仍失败则回退为纯文本输出（把返回文本整体当 `body_zh`，`title_zh` 留空或复制原文标题）。
- 单条新闻正文数百字，成本极低（<¥0.01），无需特殊节流。

## 8. 验收标准

1. 给定 1 条真实新闻正文，译文通顺、信息不缺失、段落对齐。
2. 输出是合法 `TranslationResult`（两个字段均为字符串）。
3. 术语表里的词按表翻译（如 `アイドルマスター` → `偶像大师`）。
4. 无 Key 或网络异常时，抛异常或返回明确错误，不静默产出乱码。

## 9. 边界与注意事项

- 正文可能很长：DeepSeek 上下文足够，一次性整篇翻译即可；如担心超长，可按段落分批再拼接。
- `title_zh` 用于消息头部，务必精炼；`body_zh` 保留段落。
- 不要在本模块做任何消息排版/分片——那是 M5 的职责。
