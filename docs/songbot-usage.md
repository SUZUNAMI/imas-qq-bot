# songbot 群内使用说明：Q 群输入格式

> 适用：歌曲列表 bot（songbot）部署后，群成员在 QQ 群内的输入方式。
> 依据：`docs/S10-list-image-atbot-plan.md`（**S10 列表图片渲染 + @only 门控，2026-08-27 已实现并单测/离线全链路验收**）；
> `docs/S9-bindings-update-plan.md`（命令前缀 + 绑定 + 手动刷新，2026-08-27 已实现并单测/离线全链路验收）；
> `docs/S8-song-lookup-plan.md`（`song` 歌曲反查 LIVE，2026-08-27 已实现并验收）；
> `docs/modules/S7-delivery-plan.md`（S7 收尾：启动/停止状态通知 + 后台挂载脚本，2026-08-27）。
> live 测试约定：QQ 群消息测试默认只发**测试群（450599137，原 666 群 827029417 已失效）**；主群测试需用户明确允许。

---

## 1. 一句话用法

**命令前缀（强制）**：`@bot live <LIVE 名 / 年月>`，两段交互：

```
第一段：@bot live <LIVE 名 / 时间>  →  bot 回复子列表 / 候选列表 / 歌曲列表图片
第二段：@bot DAY1 / 序号 / 公演名   →  bot 发送对应公演的歌曲列表图片
```

> **每轮回复都需 @bot**（S10 @only 门控，2026-08-27 起）：二次确认（序号 / DAY1 / 歌名）与
> `quit` 取消**也必须 @bot**——未 @bot 的消息一律忽略（含带会话的普通闲聊）。
> **列表回复均为图片**（S10）：候选 / 子列表 / 时间筛选 / 歌曲出现 / bindings 等列表以
> **图片**发送（长列表自动分页多图），图内序号即会话确认序号，图片 footer 统一「回复序号」。
> **没有命令前缀**（如直接 `@bot IWSF2026`）会收到用法提示——请改用 `@bot live IWSF2026`。
> **回复按用户归属**：bot 的所有回复开头都会 @ 发起查询的用户（群内多人并发互不混淆）。
> **回复 `@bot quit` 可随时取消**当前等待（清会话，不再等二次确认）。

---

## 2. 命令一览（全部需 @bot + 命令前缀）

| 命令 | 用途 | 权限 | 示例 |
|---|---|---|---|
| `live <LIVE 名>` | 按名称查 LIVE（缩写/日文/英文均可） | 全员 | `@bot live IWSF2026` |
| `live <年月>` | 按年/月查 LIVE | 全员 | `@bot live 2026年7月` |
| `song <歌名>` | 反查该歌出现过的所有 LIVE（序号+日期），选序号出该 LIVE 歌曲列表图 | 全员 | `@bot song Dance in the Light` |
| `quit`（第二段回复） | **取消当前等待**（清会话，不再等二次确认；**需 @bot**，S10） | 全员 | `@bot quit` / `@bot QUIT` |
| `binding <略缩> <事件名>` | 给 LIVE 设置自定义略缩（**唯一命中才绑**） | **仅群主/管理员** | `@bot binding iwsf IDOL WORLD SUPER FESTIVAL 2026` |
| `unbind <略缩>` | 删除略缩绑定 | **仅群主/管理员** | `@bot unbind iwsf` |
| `bindings` | 列出全部绑定 | **仅群主/管理员** | `@bot bindings` |
| `update live` | 手动全量刷新（重抓事件列表 + 重建事件索引 + 歌曲反向索引） | **仅群主/管理员** | `@bot update live` |

> **权限说明（2026-08-27 起）**：`binding` / `unbind` / `bindings` / `update live` 是管理命令，
> 仅**群主（owner）**与**管理员（administrator）**可用（按 QQ 群内身份自动识别）；
> 普通成员使用会收到「该命令仅群主/管理员可用」的提示，命令不会执行。
> `live` / `song` 查询对全体成员开放。

---

## 3. 第一段：`@bot live <LIVE 名 / 年月>`

### 3.1 按时间查询（推荐查「某年某月有什么 LIVE」）

| 输入示例 | 含义 |
|---|---|
| `@bot live 2026年7月` | 2026 年 7 月的全部 LIVE |
| `@bot live 2026年` | 2026 年全年 LIVE |
| `@bot live 7月` | 最近一年（索引中最新年份）的 7 月 |
| `@bot live 2026-07` / `@bot live 2026/07` / `@bot live 2026.07` | 同上（分隔符 - / . 均可） |
| `@bot live ２０２６年７月` | 全角数字/文字也可识别 |

回复形式：**图片**（S10：候选/时间筛选列表一律发图，长列表自动分页多图；图内
「序号 + 事件名 + 日期/子项」+ footer「回复序号」），例如图内内容：

```
2026年7月 的 LIVE（2 场）
1.  IDOL WORLD SUPER FESTIVAL 2026        多日：第一公演 -YAKUDOU-(2026/07/24(金))、…
2.  CINDERELLA GIRLS MUSICAL DERE of the DEAD   2026/07/04(土)・05(日)
回复序号
```

> 时间列表**全部事件都进图**（不再截断为 10 条）：图内序号与会话确认序号一致，直接 @bot 回复序号即可。

### 3.2 按名称查询（查某个具体 LIVE）

| 输入示例 | 说明 |
|---|---|
| `@bot live IWSF2026` | 支持缩写（IWSF = IDOL WORLD SUPER FESTIVAL） |
| `@bot live MOIW 2025` | 支持缩写别名（MOIW = M@STERS OF IDOL WORLD，带年份精确命中 2025 场） |
| `@bot live 13thLIVE` | 精确命中 MILLION LIVE! 13thLIVE（不会误中 11th/12th） |
| `@bot live シャニ` / `@bot live 学園` | 日文关键词模糊匹配 |
| `@bot live DERE of the DEAD` | 英文全名 |
| `@bot live １３ｔｈＬＩＶＥ` | 全角/大小写均自动归一化 |
| `@bot live I W S F` | 空格、分隔符自动忽略 |

三种回复：

1. **唯一多日 LIVE**（如 `live IWSF2026`）→ 回复子公演列表**图片**，等你选 DAY：
   ```
   IDOL WORLD SUPER FESTIVAL 2026
   1.  第一公演 -YAKUDOU-    2026/07/24(金)
   2.  第二公演 -ZESSYOU-    2026/07/25(土)
   3.  第三公演 -KYOUMEI-    2026/07/26(日)
   回复序号
   ```
2. **唯一单页 LIVE** → 直接发送该公演的歌曲列表图片（不用第二段）。
3. **多个候选** → 回复带序号的候选列表**图片**，等你 @bot 回复序号或 LIVE 名。
4. **无命中** → 回复未找到 + 用法提示。

---

## 3.5 歌曲反查 LIVE（S8）：`@bot song <歌名>`

反查「某首歌在哪些 LIVE 演唱过」，两段交互（**每轮都需 @bot**，S10）：

```
第一段：@bot song <歌名>      →  bot 回复该歌出现过的 LIVE 列表**图片**（序号 + 事件名 + 子公演 + 日期）
第二段：@bot 序号             →  bot 发送对应公演的歌曲列表图片
```

示例：

| 输入 | 说明 |
|---|---|
| `@bot song Dance in the Light` | 英文歌名（出现在 IWSF 2026 与 13thLIVE 等，会列出全部） |
| `@bot song Marionetteは眠らない` | 日文歌名 |
| `@bot song ダンスダンスダンス` | 全角/大小写/分隔符自动归一化 |

回复形式（图片内容）：

```
「Dance in the Light」出现在 2 场 LIVE
1.  …IDOL WORLD SUPER FESTIVAL 2026（第一公演 -YAKUDOU-）  2026/07/24(金)
2.  …MILLION LIVE! 13thLIVE（DAY1 全力援走）  2026/05/05(火祝)
回复序号
```

要点：

- **歌名多义/重名** → 先列出候选歌曲**图片**（序号 + 歌名 + 出现 LIVE 数），选歌后再列 LIVE，不静默猜；
- 同一歌在同一场 LIVE 多次演唱只列一次；
- 索引由 bot 启动时构建/加载缓存（首次约需数分钟，期间回「歌曲索引构建中…」），
  **每次 `song` 查询前自动增量刷新**（新 LIVE 自动并入，只抓新增）；`update live` 也可手动全量重建；
- 无命中 → 回未找到 + 用法提示。

---

## 4. 第二段：确认回复（**需 @bot**，S10）

针对上一轮 bot 给的列表（图片），**@bot** 回复以下任一种（未 @bot 的消息一律忽略）：

| 输入 | 效果 |
|---|---|
| `@bot DAY1` / `@bot day1` | 选第 1 个子公演（DAY2、DAY3…同理） |
| `@bot 1` / `@bot 2` | 按图内序号选候选 / 子公演 |
| `@bot 全力援走` | 子公演名（全名、简名均可） |
| `@bot YAKUDOU` | 子公演名关键词（IWSF 的第一公演） |
| `@bot quit` / `@bot QUIT` | **取消当前等待**（清会话，不再等二次确认） |

→ bot 抓取该公演详情并**发送歌曲列表图片**（标题 / 日期场馆 / 曲目编号+歌名+演者 / 品牌徽章 / 应援色；长表自动分页多图；图片消息与全部文本回复均 **@ 发起用户**，按用户归属）。

---

## 5. 绑定略缩（S9，仅群主/管理员）：`binding` / `unbind` / `bindings`

给常用 LIVE 起一个短略缩，之后 `live <略缩>` 直接命中（本组命令**仅群主/管理员可用**，普通成员会被拒绝）：

```
@bot binding iwsf IDOL WORLD SUPER FESTIVAL 2026
bot:  已绑定：iwsf → THE IDOLM@STER 20th Anniversary MORE RE@LITY LIVE IDOL WORLD SUPER FESTIVAL 2026（live iwsf 可直接查询）

@bot live iwsf          → 直接出该 LIVE 的子列表图片/歌曲列表图片（绑定对全体成员生效）
@bot bindings           → 全部绑定列表**图片**（略缩 + 事件名，图内 footer「回复序号」）
@bot unbind iwsf        → 删除该绑定
```

要点：

- **略缩须是单个词**（无空格），如 `iwsf`、`13th`、`dere`；大小写/全角自动归一化（`I W S F` = `iwsf`）；
- **事件名须唯一命中才绑**：0 命中（不存在）或多命中（如 `シャニ` 有好几个）都会提示「请用更精确的名字」，不会静默绑定；
- 绑定只影响 `live` 分支；绑定的 LIVE 若已下架/改版（不在索引中），`live <略缩>` 会提示绑定失效，不会报错；
- 绑定数据持久化在 `data/songbot_bindings.json`，重启 bot 不丢失；
- **绑定对全体成员生效**（成员可用 `live <略缩>` 查询），但只有群主/管理员能增删改。请注意：QQ 群内身份由上报事件 `sender.role` 自动识别（NapCat 自带，无需额外配置）。

---

## 6. 手动全量刷新（S9，仅群主/管理员）：`update live`

```
@bot update live
bot:  正在刷新全部 LIVE 索引，请稍候…
bot:  已刷新：125 事件
```

- 强制重抓事件列表并重建索引（绕过 24h 缓存）；刷新期间旧索引继续服务，完成后原子替换；
- 同时**全量重建歌曲反向索引**，回执格式「已刷新：N 事件 / M 歌曲」；
- 刷新失败会提示并继续使用旧索引；
- **仅群主/管理员可用**；普通成员会收到拒绝提示，命令不执行。

---

## 6.5 启动 / 停止状态通知（S7）

- bot **启动成功**（事件索引就绪、接收器开始监听）后，自动向配置的 `songbot.notify_groups`
  （默认：主群 1033148779 + 测试群 450599137）发送「songbot 已启动」状态消息
  （含监听端口、事件数、最新年份、歌曲索引状态、运行模式）；
- bot **优雅停止**（窗口内 Ctrl+C，或运行 `scripts\stop_songbot.cmd`）时，向同样群发送
  「songbot 已停止」消息（含本次运行起止时刻）；
- 状态通知只描述 bot 自身启停状态，不影响任何查询功能；发送失败仅记日志，不阻塞启停流程；
- **文案可自定义**（仿主程序 M7）：`config.yaml` 的 `songbot.notify_startup` / `notify_shutdown`
  可改启动/停止通知文本（`\n` 表示换行）。占位符（写进文案即自动替换，未知占位符原样保留）：
  - 启动：`{port}` 监听端口 / `{events}` 事件数 / `{year}` 最新年份 / `{song_index}` 歌曲索引状态 /
    `{ttl}` 会话 TTL / `{mode}` 模式 / `{time}` 当前时间
  - 停止：`{started}` 启动时刻 / `{stopped}` 停止时刻 / `{time}` 停止时刻
- **强制结束**（任务管理器结束 / `taskkill /F`）不会发停止通知（进程被直接终止，无法执行收尾）；
- **bot 收不到 `@bot` 消息？** 多为 NapCat Desktop 重启后把 8090 事件上报配置覆盖回空（已知行为）——
  运行 `python scripts/restore_napcat_webhook.py` 一键恢复（无需重启 NapCat，不影响 M7）。
- 常用启停方式：
  ```
  scripts\start_songbot.cmd            # 后台挂载：新开「SongBot」窗口运行，脚本立即返回
  scripts\stop_songbot.cmd             # 优雅停止：写停止文件 → bot 发停止通知后退出（最长等 40s）
  （或直接在 SongBot 窗口内 Ctrl+C）   # 同为优雅停止，会发停止通知
  ```

---

## 7. 匹配容错（自动处理，无需刻意规范输入）

- 全角↔半角、英文字母大小写：`１３ｔｈＬＩＶＥ` = `13thLIVE`
- 空格与分隔符（`-` `・` `:` `/` `_` 等）忽略：`I W S F` = `IWSF`
- 缩写识别：`IWSF` → `IDOL WORLD SUPER FESTIVAL`；`MOIW` → `M@STERS OF IDOL WORLD`（`MOIW 2025` 带年份精确命中对应年份）
- 关键词子串：`シャニ` → シャイニーカラーズ相关；`学園` → 学園アイドルマスター相关

## 8. 已知限制（当前版本）

| 情况 | 行为 |
|---|---|
| 无命令前缀（如直接 `@bot IWSF2026`） | 回用法提示（强制前缀，S9 起） |
| **未 @bot 的消息（S10 @only 门控）** | **一律忽略**（含二次确认 / `quit` / 带会话的闲聊——每轮回复都需 @bot） |
| 普通成员使用管理命令（binding/unbind/bindings/update live） | 回「该命令仅群主/管理员可用」，命令不执行 |
| 歌曲索引未就绪（首次构建中） | `song` 回「歌曲索引构建中…」，稍后再试 |
| 品牌名查询（如 `ミリオン`、`SideM`） | 不命中（候选文本不含品牌徽章名） |
| `2026年7月14日`（带「日」的精确日期） | 按名称查询处理，可能未找到（不支持日粒度） |
| 拼写错得较多（如 `13thLIVE` vs `11thLIVE`） | 不强行匹配，返回未找到（短查询有防误配；超长全名可能近匹配，绑定事件名请用唯一短名） |
| 会话有效期 | 5 分钟；超时后需重新 `@bot` 发起 |
| 单次回复上限 | 列表类一律**图片**发送（S10）：全部条目进图、长列表自动分页多图，不再按 10 条截断 |
| 列表图片渲染/发送失败 | 回退**纯文本列表**（`format_*` 产物，含确认提示），功能不中断 |

---

## 9. 典型对话示例（测试群，S10 起每轮都 @bot）

```
你:   @bot live IWSF2026
bot:  [图片] IDOL WORLD SUPER FESTIVAL 2026（子公演列表，footer「回复序号」）
你:   @bot 1
bot:  [图片]（IDOL WORLD SUPER FESTIVAL 2026 -YAKUDOU- [DAY1] 21 曲，标题/日期/出演/曲目均在图内）
```

```
你:   @bot live 2026年7月
bot:  [图片] 2026年7月 的 LIVE（2 场）（IWSF 2026 / DERE of the DEAD，footer「回复序号」）
你:   @bot 1
bot:  [图片] IWSF2026 子公演列表
你:   @bot 1
bot:  [图片]（IWSF 2026 第一公演的歌曲列表）
```

```
你:   @bot binding iwsf IDOL WORLD SUPER FESTIVAL 2026
bot:  已绑定：iwsf → …IDOL WORLD SUPER FESTIVAL 2026（live iwsf 可直接查询）
你:   @bot live iwsf
bot:  [图片] IWSF2026 子公演列表
```

```
你:   @bot song Dance in the Light
bot:  [图片] 「Dance in the Light」出现在 2 场 LIVE（IWSF 2026 / 13thLIVE，footer「回复序号」）
你:   @bot 1
bot:  [图片]（IWSF 2026 第一公演的歌曲列表）
```
