# Codex Orchestrator (Native Antigravity MCP App)

Antigravity 作为总调度与决策中心（Master），官方 `codex exec` 作为纯执行层（Worker）的原生插件：

1. **原生 MCP App（SEP-1865），零侵入零遥测**：严格按 Antigravity 官方 MCP App 规范实现，不改客户端 `asar`，不搞 CDP 注入，客户端更新不失效；前端已预编译为单文件 [mcp_server/app.html](mcp_server/app.html)，**安装与运行不依赖 Node.js**，无联网上报、无遥测追踪。
2. **纯本地只读轮询，零额外消耗**：通过 `app-only` MCP 工具每 2 秒只读轮询本地 SQLite WAL、`~/.codex/sessions/*.jsonl`（实时命令流、Token 消耗与缓存命中率）及本地 `git diff`，彻底打开 `codex exec` 黑盒，**零额外 LLM / API 额度消耗**。
3. **合规单向 + 安全护栏**：仅由 Antigravity 单向调用本地官方 `codex exec`，不反向调用、不代理、不碰 OAuth 凭据；Git 写任务默认启用 `git worktree` 隔离与沙箱锁定（`--approve-for-me`），不污染主分支。
4. **跨平台 + AI 一键自部署**：支持 Windows / macOS / Linux（仅需 Python 3.9+ 标准库）。仓库自带给 AI 阅读的 [INSTALL_SKILL.md](INSTALL_SKILL.md)，克隆后发给 Antigravity 即可自动完成环境检测、安装与自检。

## 默认分工路由

- **Antigravity（Gemini）亲手负责**：全部文档编写（OpenSpec / README / 说明文档）、全部前端页面与 UI/UX 视觉交互、总控统筹与阻塞拍板。
- **Codex CLI（GPT）专注负责**：按实体拆分的后端/数据层/核心算法/状态机、按业务场景拆分的自动化测试，以及收尾逻辑与边界交叉审查（`cross-review`）。

## 目录结构

```text
codex-orchestrator/
├── plugin.json               # Antigravity 插件清单
├── mcp_config.json           # 原生 MCP App 服务配置（Python 标准库，零外部依赖）
├── INSTALL_SKILL.md          # 给 AI 看的跨平台一键自动部署说明
├── install.ps1               # Windows 全局短命令 codex-orch 安装脚本
├── bin/                      # 跨平台短命令包装器
├── mcp_server/
│   ├── server.py             # 零依赖 Python stdio MCP App 服务端
│   └── app.html              # 预编译单文件 MCP App 前端（含 @modelcontextprotocol/ext-apps）
└── skills/
    └── codex-orchestrator/
        ├── SKILL.md          # 三阶段并发编排与分工路由 SOP
        └── scripts/
            ├── db.py         # SQLite WAL 中间件
            ├── orchestrate.py# 调度器（默认 worktree 隔离、并发 dispatch-batch、cross-review）
            └── console.py    # 可选独立小窗 Web 看板
```

## 快速安装

把本仓库发给 Antigravity 并让它按 `INSTALL_SKILL.md` 自动部署，或手动执行：

```bash
git clone https://github.com/jiuan0323/codex-orchestrator.git ~/.gemini/config/plugins/codex-orchestrator
python ~/.gemini/config/plugins/codex-orchestrator/mcp_server/server.py --self-test
```

在 Antigravity 侧边栏 `+` 号菜单或左侧边栏即可直接打开 **「Codex 实时观察台」**。
