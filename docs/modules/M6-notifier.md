# M6 QQ 推送（Notifier，NapCatQQ / OneBot 11）— 交接规格

> 项目：爱马仕官方新闻 QQ 转发机器人。追踪 https://idolmaster-official.jp/news，新新闻发布后推送「原文 + AI 日译中」到 QQ 群。
> 技术栈：Python 3.11+。
> 本文件**自包含**：只读本文件即可实现本模块，无需读其他模块文档。
> 契约冻结：输入/输出结构必须严格符合本文件定义；如需改动，回改 `docs/module-specs.md` §1。

## 1. 本模块在流水线中的位置

```
… ──► M5 消息组装 ──PushMessage──► M6 QQ推送 ──PushResult[]──► QQ群
```

本模块：把组装好的消息，通过 NapCatQQ（OneBot 11 协议）发到配置的多个 QQ 群。

## 2. 输入契约：`PushMessage`

```python
from dataclasses import dataclass

@dataclass
class PushMessage:
    group_ids: list[str]   # 目标群号列表
    segments: list[str]    # 已分好片的文本段，按顺序逐条发送
    images: list[str]      # 配图 URL（可空）
    link: str              # 原文链接
```

## 3. 输出契约：`PushResult[]`

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class PushResult:
    group_id: str
    ok: bool
    message_id: str          # 成功时填返回的消息 id，失败填 ""
    error: Optional[str] = None
```

示例：

```json
{ "group_id": "123456789", "ok": true, "message_id": "12345", "error": null }
```

## 4. 前置环境（运维，本模块只负责调用）

NapCatQQ 已在本机/服务器安装并登录 bot 小号，且以 OneBot 11 对外提供接口。本模块通过 **HTTP API**（或反向 WebSocket）与之通信。

## 5. OneBot 11 协议要点

- 发群消息接口：`POST /send_group_msg`
- 请求体：
  ```json
  {
    "group_id": 123456789,
    "message": [
      { "type": "text", "data": { "text": "……" } }
    ]
  }
  ```
- 图片消息段：`{ "type": "image", "data": { "file": "https://.../a.jpg" } }`（OneBot 支持 URL，NapCatQQ 会代为下载）。
- 接口鉴权：NapCatQQ 通常要求请求头 `Authorization: Bearer <token>`（若配置了 token）。

## 6. 发送顺序与多群规则

1. 对 `PushMessage.segments` **按顺序逐条**发文本。
2. 文本发完后，若有 `images`，把图片（可合并成一条多图消息，或逐张发）发出；`link` 已在 M5 拼进文本末尾，无需额外发。
3. 遍历 `group_ids`：
   - 每个群完整发一遍（文本段 + 图片）。
   - **群与群之间 sleep 1–2 秒**（降低风控概率）。
   - 单个群失败**不阻断**其他群，继续发完，最后汇总 `PushResult[]`。

## 7. 合并转发（2026-08-26 追加，可选）

配置 `napcat.merge_forward: true` 时，整条新闻（文本段 + 图片）合并为**一条「合并聊天记录」**发送，
不再逐条刷屏：

- 接口：`POST {base_url}/send_forward_msg`，body = `{"group_id": int, "messages": [node...]}`。
- node 结构：每个文本段一个 `{"type":"node","data":{"uin":...,"name":...,"content":[text段]}}`；
  全部配图合并为一个 node（多图）。发送者 uin/昵称取自 `GET {base_url}/get_login_info`
  （按 base_url 进程内缓存；失败回退 `10001`/「爱马仕新闻」）。
- 环境变量覆盖：`NAPCAT_MERGE_FORWARD=1|true|yes|on`。
- 实测：NapCat v4.18.19 支持，`send_forward_msg` 返回 `data.message_id`；
  合并记录在群内显示为可点开的「聊天记录」卡片（2026-08-26 live 验收通过）。
- 默认 `false`（逐条发送，向后兼容）。

## 8. 实现约定

- 文件：`src/m6_notifier.py`
- 依赖：`httpx`。
- 入口函数签名：`def push(message: PushMessage) -> list[PushResult]:`
- 配置（`config.yaml`）：
  ```yaml
  napcat:
    base_url: "http://127.0.0.1:3000"   # NapCat 的 HTTP 地址
    token: "..."                        # 可选，鉴权 token
    group_ids: ["123456789"]            # 默认目标群（M5/M7 会传入）
    interval_sec: 1.5                   # 群间发送间隔
    merge_forward: false                # true=文本段+图片合并为一条「合并聊天记录」
  ```

## 9. 容错

- 单条消息发送失败：重试 1 次；仍失败记入该群的 `PushResult(ok=False, error=...)`，继续下一群。
- 返回体解析失败：以 `ok=False` 记录，不抛异常中断整轮。
- 网络不可达（NapCat 未启动）：全部群返回失败，抛出一次明确日志提示"NapCat 未连接"。

## 10. 验收标准

1. 测试群能收到「原文 + 译文 + 链接」完整消息。
2. 多群配置时，每个群都能收到；某群失败不影响其他群。
3. 图片（若传入）能正常显示。
4. 返回的 `PushResult[]` 与各群实际发送结果一致。
5. 超长消息（多 segments）按顺序完整送达、不截断。
6. `merge_forward: true` 时，整条新闻在群内显示为**一条**合并聊天记录，内容完整（2026-08-26 live 验收通过）。

## 11. 边界与注意事项

- 本模块**不关心**消息内容怎么来（M5 已组装好），只负责发送。
- 不要在本模块里改文案/分片/翻译——保持职责单一。
- 发送频率务必温和（群间 sleep），避免 bot 小号被风控。
- 群号用字符串存储（QQ 群号可能超 32 位整数范围）。
- 合并转发在群内显示为「聊天记录」卡片，需点开查看；追求直接可见用 `merge_forward: false`。
