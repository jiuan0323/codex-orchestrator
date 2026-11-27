# AI 自动部署指南（INSTALL_SKILL.md）

> 把本仓库地址或本文件直接发给 Antigravity，Antigravity 会自动完成跨平台环境检测、零侵入原生 MCP App 安装、全局命令配置与自检。

## 插件核心特性

1. **原生 MCP App（SEP-1865），零侵入零遥测**：不改客户端 `asar`，不搞 CDP 注入，纯本地 stdio MCP 协议；前端已预编译为单文件 `mcp_server/app.html`，**安装和运行完全不依赖 Node.js**，无联网上报。
2. **纯本地只读轮询，零额外消耗**：UI 通过 `app-only` 工具每 2 秒只读轮询本地 SQLite WAL、`~/.codex/sessions/*.jsonl`（实时命令、Token 消耗与缓存命中率）和本地 `git diff`，不消耗任何 LLM / API 额度。
3. **合规单向 + 安全护栏**：仅由 Antigravity 单向调用官方 `codex exec`，不反向调用、不代理、不碰 OAuth 凭据；Git 写任务默认启用 `git worktree` 隔离与沙箱锁定，不污染主分支。
4. **跨平台支持**：Windows / macOS / Linux 通用，仅需 Python 3.9+（标准库，零 `pip` 依赖）。

## 给 Antigravity 的自动安装步骤

当你（Antigravity）收到用户要求部署本仓库时，请按以下顺序自动执行并验证：

### Step 1: 环境检测
检查以下工具是否可用：
- `python3`（macOS/Linux）或 `python`（Windows），要求版本 >= 3.9。
- `git`
- `codex` CLI（若缺失，提示用户安装 `@openai/codex` 并运行 `codex login`）
- `openspec` CLI（可选，若缺失可提示安装 `@fission-ai/openspec`）

### Step 2: 安装插件到全局插件目录
确保本仓库位于 Antigravity 全局插件目录：
- **Windows**：`%USERPROFILE%\.gemini\config\plugins\codex-orchestrator`
- **macOS / Linux**：`~/.gemini/config/plugins/codex-orchestrator`

如果当前目录不是目标路径，则 clone 或复制到该路径。

### Step 3: 适配 `mcp_config.json` 中的 Python 命令
检查 `<PLUGIN_ROOT>/mcp_config.json`：
- Windows 上确认 `"command": "python"`（或替换为检测到的绝对路径，如 `C:/.../python.exe`）。
- macOS / Linux 上若系统只有 `python3`，将 `"command"` 更新为 `"python3"`。

### Step 4: 安装全局短命令 `codex-orch`（可选但推荐）
- **Windows**：运行 `<PLUGIN_ROOT>/install.ps1` 将 `codex-orch.cmd` 安装到 `~/.local/bin`。
- **macOS / Linux**：将 `<PLUGIN_ROOT>/bin/codex-orch` 软链或复制到 `~/.local/bin/codex-orch` 并 `chmod +x`。

### Step 5: 执行 MCP App 协议自检
运行零依赖自检脚本，确认 `ok: true` 且 `html_bytes > 10000`：
```bash
python "<PLUGIN_ROOT>/mcp_server/server.py" --self-test
```

### Step 6: 刷新 MCP 服务并交付使用
1. 在 Antigravity 中重启/刷新 MCP Server `codex-orchestrator_codex_monitor`（或在插件页切换启用）。
2. 告知用户现在可以直接：
   - 在侧边栏 `+` 号菜单或左侧边栏打开 **「Codex 实时观察台」**
   - 或在对话中直接让 Antigravity 调用 `open_codex_monitor` 打开面板。
