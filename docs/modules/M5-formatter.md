# M5 消息组装（Formatter）— 交接规格

> 项目：爱马仕官方新闻 QQ 转发机器人。追踪 https://idolmaster-official.jp/news，新新闻发布后推送「原文 + AI 日译中」到 QQ 群。
> 技术栈：Python 3.11+。
> 本文件**自包含**：只读本文件即可实现本模块，无需读其他模块文档。
> 契约冻结：输入/输出结构必须严格符合本文件定义；如需改动，回改 `docs/module-specs.md` §1。
> **2026-08-26 变更（v2）**：模板改为**原文与译文均拼接**——消息同时包含原文（标题+正文）与译文（标题+正文），见 §4。`PushMessage` 输入/输出结构不变（`segments` 为自由文本）。

## 1. 本模块在流水线中的位置

```
… ──► M4 翻译 ──TranslationResult──► M5 消息组装 ──PushMessage──► M6 QQ推送 ──► QQ群
```

本模块：把「原文详情 + 译文」拼成最终要发给 QQ 群的消息，并处理好**分片**与**图片**。

## 2. 输入契约

```python
from dataclasses import dataclass

@dataclass
class NewsDetail:
    id: str
    url: str
    title: str          # 原文标题
    date: str           # "YYYY-MM-DD"
    body_text: str      # 原文正文（"\n\n" 分段落）
    images: list[str]   # 正文配图

@dataclass
class TranslationResult:
    title_zh: str
    body_zh: str
```

另需目标群列表（由 M7 主控传入）：`group_ids: list[str]`。

## 3. 输出契约：`PushMessage`

```python
from dataclasses import dataclass

@dataclass
class PushMessage:
    group_ids: list[str]   # 要推送的群号列表
    segments: list[str]    # 已分好片的文本段，M6 按顺序逐条发送
    images: list[str]      # 配图 URL 数组（可空），M6 决定如何转图片消息
    link: str              # 原文链接
```

示例：

```json
{
  "group_ids": ["123456789"],
  "segments": [
    "【NEWS】2026-08-06\n【イベント】◯◯開催決定！\n\n（原文正文第一段…）\n\n（原文正文第二段…）\n\n——— 中文翻译 ———\n【活动】◯◯举办决定！\n（正文译文第一段…）\n\n（正文译文第二段…）\n\n🔗 原文：https://idolmaster-official.jp/news/01_17821",
    "（超长消息的后续分片）…"
  ],
  "images": ["https://idolmaster-official.jp/.../a.jpg"],
  "link": "https://idolmaster-official.jp/news/01_17821"
}
```

## 4. 消息模板（v2：原文与译文均拼接）

```
【NEWS】<date>
<原标题 title>

<原文正文 body_text>

——— 中文翻译 ———
<标题译文 title_zh>
<正文译文 body_zh>

🔗 原文：<url>
```

- 分隔线以上为**原文**（标题 + 正文），以下为**译文**（标题 + 正文），结尾固定带 `🔗 原文：<url>`。
- `title` / `body_text` / `title_zh` / `body_zh` 为空时跳过对应行，非空内容块之间空行分隔，空正文时消息仍合法。
- 原文与译文都进消息后文本更长，超长新闻必然触发分片（§5）。

## 5. 分片规则（关键）

- QQ 群单条文本消息过长体验差，单段上限建议 **3500 字符**（可配置）。
- 组装出完整文本（原文 + 译文 + 链接）后，按上限切成多段：
  - 尽量在**段落边界（`\n\n`）**切，避免一句话被劈开；
  - 若某单段仍超上限，才在该段内部硬切。
- 切好的每段依次放入 `segments`。
- 分片后 `segments` 可能只有 1 段（多数情况），也可能多段。

## 6. 图片处理

- 把 `NewsDetail.images` 原样透传进 `PushMessage.images`（**不要在这里下载或转码**，那是 M6 的事）。
- 图片建议上限 4 张（M2 已限制）；本模块不再二次截断，但可做防御性 `images[:4]`。

## 7. 实现约定

- 文件：`src/m5_formatter.py`
- 入口函数签名：`def format_message(detail: NewsDetail, tr: TranslationResult, group_ids: list[str]) -> PushMessage:`
- 纯函数，无 IO、无网络，便于单测。

## 8. 验收标准

1. 给定样例 `NewsDetail` + `TranslationResult`，输出 `PushMessage` 符合 §3 结构。
2. 完整文本包含：日期、**原文标题、原文正文**、标题译文、正文译文、原文链接。
3. 超长正文被切成多段，且每段 ≤ 3500 字符，切分点在段落边界。
4. `images` 正确透传（≤4 张）。
5. 空正文时也能生成合法消息（标题+译文+链接，`body` 部分为空）。

## 9. 边界与注意事项

- 本模块不做任何网络请求、不碰数据库、不接 QQ——保持纯函数，最易测试与复用。
- 原文 `body_text` 和译文 `body_zh` 都保留段落换行，模板里用 `\n\n` 衔接，不要挤压成一行。
