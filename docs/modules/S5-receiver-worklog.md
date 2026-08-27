# S5 工作日志：事件接收 + 会话（s5_receiver）

> 所属：子项目 songbot（歌曲列表 bot） · 施工图：`docs/S1-S7-taskplan.md` §S5
> 日期：2026-08-27 · 状态：✅ 完成（单测 34/34 通过；离线验收 `scripts/acceptance_s5.py` ALL PASS）

## 1. 目标

接收 NapCat 推送的 OneBot 11 群消息事件（HTTP POST 上报到本地接收器），识别 `@bot`，
维护两段交互会话状态（`(group_id, user_id) → context`，默认 5 分钟 TTL），
供 S6 主控（`bot.py`）把「@bot 查询 → 候选/子列表 → 二次确认」串起来。

## 2. 交付物

| 文件 | 说明 |
|---|---|
| `songbot/s5_receiver.py` | `Incoming`（数据契约）/ `parse_event`（纯解析）/ `SessionStore`（会话表）/ `EventReceiver`（本地 HTTP 服务），**全部标准库零第三方依赖**；`python -m songbot.s5_receiver` 自测演示 |
| `tests/test_s5_receiver.py` | 34 个单测（unittest，全部离线）：parse_event 22 / SessionStore 8 / EventReceiver 8（实际按类统计） |
| `scripts/acceptance_s5.py` | S5 离线验收：parse 形态 + 会话 TTL + 模拟 POST 两条事件 → `[ALL PASS]`；`--listen` 可常驻（配合 `/healthz` 排障） |

## 3. 实现要点 / 设计决策

- **`parse_event(payload) -> Incoming | None`**：仅处理 `post_type=='message' and message_type=='group'`；
  - `message` 为 **array**（`messagePostFormat=array`）：逐段解析——`type=='at'` 且 `data.qq == self_id` → `at_bot=True`（`qq` 兼容 int/str，`qq=all` 不算）；`type=='text'` 拼接正文；face/image 等段跳过。
  - `message` 为 **string**（或缺失回退 `raw_message`）：正则 `[CQ:at,qq=<数字>[^\]]*]` 识别 at（兼容 at 后带 `name` 等参数），`[CQ:...]` 全部剥掉取正文。
  - **决策**：无文本的群消息（纯图片/表情/仅 @ 无文字）→ 返回 `None`（bot 只响应带文字的群消息，与 S6 处理链「at_bot 且正文非空」一致）。
  - `self_id` 取自事件 payload（NapCat 每事件自带）；缺失时 at 识别关闭（`at_bot=False`）。
- **`SessionStore`**：`dict[(group_id, user_id)] -> (context, deadline)`，读写全程 `threading.Lock`；
  键统一字符串化（QQ 号 int/str 皆可）；`get` 命中但过期即删（惰性清理），`cleanup()` 显式批量清理；
  `clock` 可注入（单测假时钟验证 TTL）；`set` 覆盖写入并重置截止时间（二次确认前重置，防中途超时）。
- **`EventReceiver`**：标准库 `ThreadingHTTPServer`（每请求一线程 = 线程池语义，天然不阻塞）；
  `POST /event` 读 JSON → `parse_event` → **先回 200 再执行回调**（不阻塞 NapCat 上报）；
  回调异常只记日志不回错误；`GET /healthz` 健康检查；`port=0` 自动分配（测试/验收用），
  类工厂 `_make_handler(receiver)` 闭包绑定实例，避免类属性污染。
- 常量集中（`DEFAULT_HOST=127.0.0.1` / `DEFAULT_PORT=8090` / `DEFAULT_TTL_SEC=300` / `EVENT_PATH=/event`），
  NapCat `postUrls: ["http://127.0.0.1:8090/event"]` 配置属 S6 前置（主仓库 `docs/modules/M6-napcat-setup.md`）。

## 4. 测试与验收

- 单测：`python -m unittest tests.test_s5_receiver -v` → **34/34 通过**（全部离线）：
  - parse_event：array 形态（at 命中/他用户/多人中一人是 bot/at=all/多段文本拼接/仅 @ 无文字/ int id 转 str/缺 self_id）；
    string 形态（CQ at 识别/raw_message 回退且 at 带参数/他用户/空串）；None 路径（非 dict、notice/request、
    private、缺 group/user、纯图片、空白文本）。
  - SessionStore：set/get/clear、按 (群,人) 隔离、假时钟 TTL 过期、覆盖写入重置截止、cleanup 计数、
    真实时钟微秒 TTL、8 线程并发 smoke。
  - EventReceiver：回环 `port=0` 起真实服务 + httpx 模拟 POST——200 应答且回调收到 Incoming、
    非群消息/无文本 200 但不下发、坏 JSON 400、错误路径 404、`/healthz` 200、上下文管理器退出后端口关闭、
    回调抛异常不影响 200。
- 离线验收（S5 验收清单）：`python scripts/acceptance_s5.py` → `[ALL PASS]`
  （模拟 POST 两条事件被正确解析；会话 set/get/超时通过）。
- 回归：S1+S2+S5 合计 102 单测 3 次连续全绿（S3/S4 并行线程同仓工作，其 S4 渲染测试在沙箱
  tempfile 清理有环境性报错，与 S5 无关）。

## 5. 已知项 / 后续（S6 接续点）

- S5 只负责「接收 + 解析 + 会话」；真正的 `@bot` 群内联调需要 **S6**：
  1. NapCat WebUI `POST /api/OB11Config/SetConfig` 追加 `postUrls: ["http://127.0.0.1:8090/event"]`
     （`messagePostFormat=array`），配置方法见主仓库 `docs/modules/M6-napcat-setup.md`；
  2. `bot.py` 把处理链注入 `EventReceiver(handler)`，会话 context 存「待确认事件/候选列表」；
  3. `scripts/acceptance_song.py` live 两段交互验收。
- `EventReceiver` 绑定 `127.0.0.1`（仅本机）；若 NapCat 与他机分离需放开 host（风险自查项）。
- 会话表为进程内内存态：bot 重启即清空（两段交互 5 分钟 TTL 内可接受，无需落盘）。
