# M6 NapCatQQ 配置（运维交接文档）

> 项目：爱马仕官方新闻 QQ 转发机器人。
> 目的：为 M6 live 推送验收（`scripts/acceptance_m6.py --group <测试群号>`）准备 NapCatQQ（OneBot 11）环境。
> 创建：2026-08-26；状态：✅ 配置完成，M6 live 验收通过（2026-08-26）。

## 0. 现状（2026-08-26 探查）

| 项目 | 状态 |
|---|---|
| 3000 端口（NapCat HTTP） | ❌ 未监听（NapCat 未运行） |
| NapCat 安装 | ❌ 常见路径未找到（未安装） |
| QQ 进程 | ✅ QQExternal 运行中（NTQQ 客户端已装） |
| 项目 config.yaml | ❌ 未创建（仅有 config.example.yaml 模板） |
| 项目 .env | ⚠️ 存在但仅 2 行，无 NAPCAT_* 项 |
| 部署位置决策 | 本机 Windows（用户确认） |
| Bot 小号 | ✅ 已有，测试群号待用户提供 |
| 安装方式决策 | 官方安装器（用户确认） |

## 1. 官方方案调研结论（2026-08-26）

- NapCatQQ 最新版：**v4.18.19**（[GitHub Releases](https://github.com/NapNeko/NapCatQQ/releases)）。
- Windows 官方推荐两条路（[官方文档 Shell 页](https://doc.napneko.icu/guide/boot/Shell.html)）：
  1. **NapCat.Windows 可视化管理工具（NapCatQQ-Desktop）**：单 EXE / MSI，Fluent 界面，支持引导安装 QQ 与 NapCat、创建/管理配置文件、一键启停、多账户管理、托盘后台运行。
     - 最新：**v3.1.10**（2026-08-26）`NapCatQQ-Desktop-3.1.10-x64.msi`（6.7 MB）。
  2. **NapCat.Shell.Windows.OneKey.zip** 一键包（1.0 MB）：解压后 `NapCatWinBootMain.exe <QQ号>` 启动。
- 注意：旧版 Windows 安装器（NapCat.Installer）已废弃；NapCat-Installer 现为 Linux 一键脚本。

## 2. 执行计划（分步，每步确认后执行）

1. **下载并安装 NapCatQQ-Desktop v3.1.10**（MSI）。
2. **启动 Desktop → 引导安装 NapCatQQ + 校验 QQ 环境**（QQ 已有则跳过重装）。
3. **配置 OneBot 11 HTTP 服务**：
   - 网络服务：HTTP，监听 `127.0.0.1:3000`（与 M6 默认 base_url 一致）。
   - 生成鉴权 token（可选但建议，写入项目 .env 的 NAPCAT_TOKEN）。
4. **登录 bot 小号**（Desktop 内扫码/快速登录）。
5. **验证接口可达**：调用 `GET /get_login_info`（或 `/send_group_msg` dry-run）确认 NapCat 正常响应。
6. **项目侧接线**：
   - 创建 `config.yaml`（复制 config.example.yaml），`napcat:` 段填 base_url/token/group_ids。
   - `.env` 补 `NAPCAT_BASE_URL` / `NAPCAT_TOKEN` / `NAPCAT_GROUP_IDS`。
7. **M6 live 验收**：用户提供测试群号 → 跑 `python scripts/acceptance_m6.py --group <测试群号>` → 测试群收到「原文 + 译文 + 链接」。

## 3. 执行记录（2026-08-26，全部完成）

| 步骤 | 结果 |
|---|---|
| 下载安装 Desktop | ✅ v3.1.10 装到 `C:\Program Files\NapCatQQ Desktop`（SHA256 校验通过；需 UAC 提权，per-user 模式不支持） |
| 引导安装 NapCat | ✅ v4.18.19 组件于 `C:\ProgramData\NapCatQQ Desktop\components\NapCatQQ`（含 NapCatWinBootMain.exe / launcher.bat） |
| 添加 bot + 登录 | ✅ bot QQID `1666562110`（昵称 時津風），NapCat 进程运行中，收发消息正常 |
| 启用 OneBot HTTP 3000 | ✅ 通过 NapCat WebUI API（`POST /api/auth/login` + `POST /api/OB11Config/SetConfig`）写入 `127.0.0.1:3000` HTTP 服务，`messagePostFormat=array` |
| 验证接口 | ✅ `GET /get_login_info` 返回 bot 信息；`GET /get_group_list` 返回 2 个群 |
| 项目侧接线 | ✅ `config.yaml` 创建（napcat 段：base_url/group_ids/interval_sec）；`.env` 补 `NAPCAT_BASE_URL/TOKEN/GROUP_IDS/INTERVAL_SEC` |
| M6 live 验收 | ✅ `python scripts/acceptance_m6.py --group 827029417` → `ok=True message_id=1543301484`，666 群收到测试消息，`[ALL PASS]` |

## 4. 关键经验（踩坑记录）

1. **NapCat 配置源是 Desktop 的 `config\bot.json`（connect 段）**，不是 `components\NapCatQQ\config\onebot11_*.json`——直接改组件配置会被 Desktop 重启时覆盖回空。正确途径：Desktop UI 或 **NapCat WebUI API**。
2. **WebUI API 调用方式**：`http://127.0.0.1:6099`（webui.json 端口/token 在 `components\NapCatQQ\config\webui.json`）：
   - 登录：`POST /api/auth/login` body `{"hash": sha256(token + ".napcat").hex}` → 返回 `data.Credential`（base64，1 小时有效）
   - 改 OneBot 配置：`POST /api/OB11Config/SetConfig`，Header `Authorization: Bearer <Credential>`，body `{"config": "<onebot11 JSON 字符串>"}`
   - 注意 API 前缀是 `/api`（如 `/api/auth/login`），不是 `/auth/login`。
3. **`.env` 编码坑**：PowerShell `Add-Content` 会按系统 ANSI（GBK）写入中文注释，与原 UTF-8 内容混合导致 Python `utf-8` 解码失败（`UnicodeDecodeError`）。已重建为统一 UTF-8。以后改 `.env` 用 UTF-8 显式写入。
4. **MSI 安装需管理员**：当前会话非管理员时 `msiexec /qn` 报 1603；需 `Start-Process -Verb RunAs` 提权（弹 UAC）或用户手动安装。
5. **端口**：NapCat WebUI 6099；OneBot HTTP 3000（本机回环）；QQ 内部端口 4001/4301/4310/5283。

## 5. 日常运维备忘

- **启动**：NapCatQQ Desktop → 点 bot「启动」（NapCat 随 QQ 进程注入运行）。
- **停止**：Desktop 点「停止」；注意 QQ 进程会被 NapCat 拉起，直接杀 QQ 可能残留。
- **登录态**：QQ 登录态在 `C:\Users\Z\Documents\Tencent Files\nt_qq`；掉线重登在 Desktop 内扫码。
- **重新配置**：Desktop UI 或 WebUI（`http://127.0.0.1:6099`，token 见 webui.json）。
- **验收重跑**：`python scripts/acceptance_m6.py --group <群号>`。
- **服务器迁移（M9）**：NapCat 需在服务器首次交互登录后常驻；base_url/token/group_ids 服务器上另行配置（不进 git）。

## 6. 验收标准（承接 M6 规格 §9）

1. 测试群收到完整消息（原文 + 译文 + 链接）。
2. `PushResult[]` 与各群实际发送结果一致。
3. 图片（若传入）正常显示。
4. 群间间隔 1.5s，发送频率温和（防风控）。
