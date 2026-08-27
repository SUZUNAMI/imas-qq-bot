# M9 打包迁移计划（本地 Windows → Windows Server）

> 所属项目：爱马仕官方新闻 QQ 转发机器人 + songbot 曲库查询 bot
> 线程：开发环境管理与迁移（agent.md §1）
> 创建：2026-08-27 · 状态：⏳ 计划阶段
> 服务器：8.134.167.32（阿里云 ECS · Windows Server 2025 Datacenter · 1C2G · 40GB C盘）

---

## 1. 目标

将整套系统（M7 新闻转发主控 + songbot 曲库查询）从本地 Windows 迁移到 Windows Server，
并以 **WinSW 服务化 + 开机自启** 常驻运行；NapCatQQ 在服务器首次交互登录 bot 小号后常驻。

## 2. 迁移范围

| 类别 | 内容 | 去向 |
|---|---|---|
| 代码 | `src/` `songbot/` `scripts/` `tests/` `ref/` `fixtures/` `docs/` | GitHub 仓库 → 服务器下载 |
| 配置模板 | `config.example.yaml` `.env.example` `requirements.txt` `README.md` `agent.md` | GitHub 仓库 |
| vendor 依赖 | `vendor/`（111MB，gitignore 排除） | **不上传**；服务器 `pip install -r requirements.txt` + `playwright` |
| 状态库 | `data/state.db`（M3 增量状态） | 随迁拷贝（或服务器重建） |
| 缓存 | `data/songbot_*.json`（事件/歌曲索引等） | 可选上传（加速首启）或服务器重建 |
| 配置/密钥 | `config.yaml` `.env`（含 DeepSeek key、NapCat 地址、群号） | **不进 git**；服务器上另行配置 |
| 外部依赖 | NapCatQQ + NTQQ（bot 小号 1666562110）、Edge 浏览器、Python 3.11+ | 服务器手动安装 |

## 3. 服务器现状（2026-08-27 探测）

- OS：Windows Server 2025 Datacenter（10.0.26100）
- CPU/内存：1 核 2 线程 / 2GB RAM
- 磁盘：C: 40GB（空闲 ~19GB）
- SSH：sshd 已装，端口 **22022**（云盾对 22 拦截，改端口绕开）
- 内网 IP：172.31.146.147（公网 8.134.167.32）

## 4. 分阶段计划

### 阶段 0：准备与确认（本文档 + 用户确认）
- [ ] 确认迁移范围、GitHub 仓库方式、NapCat 登录安排

### 阶段 1：打包上传 GitHub
- [ ] 本地 git 初始化（工作区当前无 .git）
- [ ] 确认 `.gitignore` 排除：`config.yaml` `.env` `vendor/` `state*.db` `logs/` `.tmp/` `__pycache__/`
- [ ] 提交代码 → 推送到 GitHub（公开/私有仓）
- [ ] 更新 `docs/index.md`（M9 状态）

### 阶段 2：编写部署脚本 `setup_server.ps1`（随仓库上传）
- [ ] 检查 Python 3.11+（无则提示安装/自动下载安装）
- [ ] 检查 Edge（songbot 渲染用 `channel="msedge"`，无则提示安装）
- [ ] `pip install -r requirements.txt` + `playwright`（PyPI 直装）
- [ ] 从 `config.example.yaml` 生成 `config.yaml`（提示填写群号/NapCat 地址）
- [ ] 生成 `.env` 模板（提示填 `DEEPSEEK_API_KEY` / `NAPCAT_*`）
- [ ] 冒烟测试：`python -m songbot.bot --dry-run` + M7 `--dry-run`
- [ ] 输出部署报告与下一步指引

### 阶段 3：服务器外部依赖（用户手动，脚本辅助检查）
- [ ] NapCatQQ + NTQQ 安装，首次交互登录 bot 小号（时津風 1666562110）
- [ ] NapCat OneBot 配置：HTTP server 3000 + postUrls 追加 `http://127.0.0.1:8090/event`（songbot）
- [ ] Edge 安装（如缺失）

### 阶段 4：WinSW 服务化（开机自启）
- [ ] 下载 WinSW.exe（服务器）
- [ ] 注册服务 A：M7 主控（`python src/main.py`）
- [ ] 注册服务 B：songbot（`python -m songbot.bot`）
- [ ] 配置服务自启 + 失败重启

### 阶段 5：验收
- [ ] M7 dry-run 全链路（真实抓取 → 翻译 → 留档）
- [ ] songbot 索引构建 + dry-run
- [ ] 双服务开机自启验证（重启服务器）
- [ ] 群内真实推送测试（需用户同意，默认测试群 450599137）
- [ ] 更新 `docs/index.md` / `agent.md` 状态

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 2GB 内存跑 M7+songbot+NapCat 偏紧 | 服务分开启动；NapCat 内存优化；必要时升级实例 |
| 服务器访问 PyPI 慢 | pip 换阿里云镜像 `-i https://mirrors.aliyun.com/pypi/simple` |
| 云盾拦截 SSH（来源 IP 配额） | 已改 22022 端口；长期建议云盾白名单 |
| NapCat 登录需图形界面 | 用户 RDP 手动首次登录；登录态保存后常驻 |
| 服务器无 Edge | 安装 Edge 或用 playwright Chromium（体积大） |

## 6. 备注

- songbot 与 M7 独立进程独立服务（S7 用户要求 ④）
- 配置/密钥外置且不进 git（agent.md §2）
- 状态库 `state.db` 随迁（M3 增量状态避免重复推送）
