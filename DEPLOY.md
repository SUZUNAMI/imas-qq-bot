# 服务器部署手册（Windows Server）

> 项目：爱马仕官方新闻 QQ 转发机器人（M7）+ songbot 曲库查询（独立进程）
> 仓库：https://github.com/SUZUNAMI/imas-qq-bot（私有）
> 目标：Windows Server 2025 · 需要管理员权限

---

## 第 0 步：装 Git（服务器上没有的话）

```powershell
winget install Git.Git
# 或手动下载安装 https://git-scm.com/download/win
```

## 第 1 步：拿到代码（二选一）

**方式 A：git clone（需要 GitHub 凭据）**
在 GitHub 设置 → Developer settings → Fine-grained tokens 创建一个只读 token
（Repository access: 仅 imas-qq-bot，Permissions: Contents=Read-only），然后：

```powershell
cd C:\
git clone https://<你的GitHub用户名>:<PAT>@github.com/SUZUNAMI/imas-qq-bot.git
cd imas-qq-bot
```

**方式 B：下载 zip（同样需要认证）**
浏览器登录 GitHub 后打开
`https://github.com/SUZUNAMI/imas-qq-bot/archive/refs/heads/main.zip`，
解压到 `C:\imas-qq-bot`。

## 第 2 步：运行部署脚本（管理员 PowerShell）

```powershell
cd C:\imas-qq-bot
Set-ExecutionPolicy -Scope Process Bypass -Force   # 允许本会话执行脚本
.\setup_server.ps1
```

脚本会依次：检查 Python 3.11+ → 检查 Edge → pip 安装依赖（阿里云镜像）→
生成 `config.yaml` / `.env` 模板 → 冒烟测试。

> 若提示 Python 未安装：到 https://www.python.org/downloads/ 装 3.11+，
> 安装时务必勾选 **Add python.exe to PATH**，然后重跑脚本。

## 第 3 步：编辑配置

```powershell
notepad config.yaml
```
- `napcat.base_url`：NapCat HTTP 地址（默认 http://127.0.0.1:3000）
- `napcat.group_ids` / `orchestrator.notify_groups` / `songbot.notify_groups`：换成服务器实际群号
- `songbot.port`：8090（保持默认，NapCat postUrls 要指向它）

```powershell
notepad .env
```
- `DEEPSEEK_API_KEY`：DeepSeek API key（与本地相同）
- `NAPCAT_BASE_URL` / `NAPCAT_GROUP_IDS` 等按需

## 第 4 步：安装 NapCat 并登录 bot 小号

参考 `docs/modules/M6-napcat-setup.md`：
1. 安装 NapCatQQ（Desktop 版）+ 启动，首次交互登录 bot 小号（1666562110 時津風）
2. OneBot 11 配置：
   - HTTP server：`127.0.0.1:3000`
   - **postUrls 追加**：`http://127.0.0.1:8090/event`（songbot 事件上报，messagePostFormat=array）
   - 若 Desktop 重启清空 httpClients，运行：`python scripts/restore_napcat_webhook.py`
3. NapCat 保持常驻（放开机自启）

## 第 5 步：启动验证

```powershell
# M7 新闻转发（先 dry-run 预演）
python src/main.py --dry-run
# 正式启动（新开窗口后台挂载）
scripts\start_bot.cmd

# songbot 曲库查询（先 dry-run）
python -m songbot.bot --dry-run
# 正式启动
scripts\start_songbot.cmd
```

验证：测试群 450599137 应收到「M7 已启动」「songbot 已启动」状态通知。

## 第 6 步：WinSW 服务化（开机自启，M9 要求）

后续按 `docs/modules/M9-migration-plan.md` 阶段 4 执行：
- 下载 WinSW（https://github.com/winsw/winsw/releases）
- 为 `src/main.py`（M7）和 `python -m songbot.bot`（songbot）各建一个服务
- 配置自动启动 + 失败重启
