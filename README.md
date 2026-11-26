# Codex Orchestrator

Antigravity 作为总调度与决策中心（Master），Codex CLI 作为纯执行层（Worker）的多 Agent 编排插件：

- **OpenSpec**：记录 `change` 与 `tasks.md` 任务节点（仅由 Antigravity 维护勾选状态）。
- **SQLite（WAL 模式）**：作为沟通记录中间件、会话绑定存储与控制台插件数据源，默认位于 `<project_root>/.codex-orchestrator/state.db`。
- **Codex CLI**：以非交互模式执行单个清晰 task，遇到需要决策的问题按 `BLOCKED:` 协议退出并交由 Antigravity 拍板续跑。
- **默认分工路由**：
  - **Antigravity（Gemini）亲手负责**：全部文档编写（OpenSpec / README / 说明文档）、全部前端页面与 UI/UX 视觉交互、总控调度与阻塞拍板。
  - **Codex CLI（GPT）专注负责**：后端与核心业务实体、数据层/算法/状态机、按业务场景拆分的自动化测试，以及收尾逻辑与边界交叉审查。
- **本地控制台看板**：零外部依赖紧凑小窗 Web UI，1.5 秒轮询 SQLite WAL 与本地 `~/.codex/sessions/` 实时终端流，支持查看当前派发指令、实时黑框直播、一键暂停与叮嘱。

## 目录结构

```text
codex-orchestrator/
├── plugin.json
├── README.md
└── skills/
    └── codex-orchestrator/
        ├── SKILL.md
        └── scripts/
            ├── db.py           # SQLite WAL 中间件（changes / tasks / messages / decisions）
            ├── orchestrate.py  # 统一调度 CLI（init / sync-tasks / dispatch / resume / gate / complete / rollback / decide / control / status / console）
            └── console.py      # 零依赖本地 Web 控制台看板
```

## 前置依赖

- Python 3.9+（仅使用标准库 `sqlite3`、`http.server` 等，零第三方包）
- Node.js / npm：
  ```bash
  npm install -g @openai/codex @fission-ai/openspec
  ```
- 完成 `codex login`（若已登录 Codex App 通常会自动复用登录态）

## 安装为全局 Antigravity 插件

```powershell
git clone https://github.com/jiuan0323/codex-orchestrator.git ~/.gemini/config/plugins/codex-orchestrator
# 安装全局短命令 codex-orch（Windows）
~/.gemini/config/plugins/codex-orchestrator/install.ps1
```

Antigravity 会自动发现并加载该插件。

## 快速开始

在任意项目目录下：

```bash
# 一键启动控制台看板并自动打开浏览器（默认监听当前目录，端口 8765）
codex-orch

# 或者指定项目目录 / 端口
codex-orch --project /path/to/repo ui --port 8765 --open

# 1. 同步 OpenSpec 任务节点到 SQLite
codex-orch sync-tasks --change my-change

# 2. 派发单个 task 给 Codex CLI 执行
codex-orch dispatch --change my-change --task 1.1 --instructions "Implement feature X"

# 3. 查看状态
codex-orch status
```
