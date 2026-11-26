---
name: codex-orchestrator
description: >-
  Orchestrate Codex CLI as a pure execution worker under Antigravity's control using
  OpenSpec task nodes and a local SQLite WAL middleware (state, session bindings,
  decisions, communication logs, and console plugin telemetry). Use when the user
  wants Antigravity to plan/coordinate a change and dispatch execution tasks to Codex,
  unblock Codex, inspect Codex task bindings, or manage OpenSpec + Codex workflows.
---

# Codex Orchestrator (Antigravity Master → Codex CLI Worker)

Antigravity is the **Master Orchestrator** (planning, architectural decisions, task dispatch, unblocking, and acceptance). Codex CLI is the **Execution Worker** (implementing scoped tasks cleanly and handling internal write conflicts).

## Architecture

- **OpenSpec (`openspec/changes/<change>/`)**: Source of truth for `proposal.md`, `design.md`, and `tasks.md`. **Only Antigravity checks off `tasks.md`.**
- **SQLite WAL Middleware (`<project>/.codex-orchestrator/state.db`)**: Stores `changes`, `tasks` (including `codex_session_id`, `base_commit`, `result_commit`, `block_count`), `decisions`, and `messages` (also serves as the data source for any console plugin).
- **Orchestrator CLI** (prefer global `codex-orch` if on PATH, otherwise fall back to `python "~/.gemini/config/plugins/codex-orchestrator/skills/codex-orchestrator/scripts/orchestrate.py"`):
  ```powershell
  codex-orch --project "<PROJECT_ROOT>" <subcommand> ...
  ```

## Standard Operating Procedure

### 1. Initialize & Sync OpenSpec Change
1. Ensure OpenSpec exists (`openspec init` if needed) and author/update `openspec/changes/<change>/proposal.md`, `design.md`, and `tasks.md`.
2. Sync task nodes into SQLite:
   ```powershell
   python ".../orchestrate.py" --project "<PROJECT>" sync-tasks --change "<change_id>"
   ```
3. Record any non-obvious architectural choices upfront so Codex never stalls on them:
   ```powershell
   python ".../orchestrate.py" --project "<PROJECT>" decide --change "<change_id>" --topic "<topic>" --decision "<locked decision>"
   ```

### 2. Three-Phase Concurrent Execution (Recommended for Medium/Large Changes)
To maximize throughput without causing merge conflicts or token waste:
1. **Phase 1 — Entity-Based Parallel Development (`dev_entity`)**:
   - Split implementation by **domain entity / module** with disjoint `owned_paths` (keep concurrency to 2–4 medium-sized tasks so system-prompt overhead stays tiny).
   - Dispatch concurrently using `dispatch-batch` (with `--worktree` when tasks touch shared build/config or need branch isolation):
     ```powershell
     codex-orch --project "<PROJECT>" dispatch-batch --change "<change_id>" --max-workers 3 --worktree --spec batch_dev.json
     ```
   - Or dispatch a single task:
     ```powershell
     codex-orch --project "<PROJECT>" dispatch --change "<change_id>" --task "1.1" --kind dev_entity --owned-paths "app/src/.../deck/**" --instructions "<brief>"
     ```
2. **Phase 2 — Scenario-Based Parallel Testing (`test_scenario`)**:
   - After merging/accepting entity tasks, split verification by **end-to-end business scenarios** (NOT isolated entities) and dispatch concurrently with `--kind test_scenario` to catch cross-entity integration bugs.
3. **Phase 3 — Dual-Model Cross-Review (`cross-review`)**:
   - Run Codex (GPT) independent diff review while Antigravity (Gemini) reviews architecture, UX, and defensive bloat in parallel:
     ```powershell
     codex-orch --project "<PROJECT>" cross-review --change "<change_id>" --base "<base_commit>"
     ```
   - Synthesize both reviews, resolve any remaining concrete issues via a final targeted fix, then close the change.

- Every dispatch automatically records `base_commit`, injects locked decisions + file ownership boundary + anti-defensive rules, binds `codex_session_id` in SQLite, and logs the exchange.
- If `control_flag` is `pause` or `abort` in SQLite, dispatch halts immediately and reports `user_note`.

### 3. Handle Blockers & Circuit Breaker
- If Codex returns `status: "blocked"`:
  - **Routine technical decision**: Antigravity decides immediately and calls `resume` (which logs the decision and resumes the bound `codex_session_id`):
    ```powershell
    python ".../orchestrate.py" --project "<PROJECT>" resume --change "<change_id>" --task "1.1" --topic "<topic>" --message "<clear decision>"
    ```
  - **Breaking / red-line decision** (data deletion, public API breakage, major architecture shift): ask the user, then call `resume --decided-by human`.
  - **Circuit breaker (`circuit_breaker_triggered: true`, i.e., `block_count >= 2`)**: Stop arguing with that Codex session. Either split the task smaller and dispatch a fresh session, or implement that step directly in Antigravity.

### 4. Two-Layer Verification & Acceptance
1. **Hard Gate (automated)**: Run build/lint/tests first:
   ```powershell
   python ".../orchestrate.py" --project "<PROJECT>" gate --change "<change_id>" --task "1.1" --command "<test/build command>"
   ```
   If the hard gate fails, feed the failure output back via `resume` so Codex fixes it without wasting Antigravity review cycles.
2. **Soft Review (Antigravity)**: Inspect `git diff` specifically for:
   - Drift from OpenSpec requirements
   - Overly defensive bloat (redundant null checks, speculative `try/catch`, unnecessary abstractions)
3. **Accept or Roll Back**:
   - Accept (records `result_commit` and checks off `- [x]` in `openspec/changes/<change>/tasks.md`):
     ```powershell
     python ".../orchestrate.py" --project "<PROJECT>" complete --change "<change_id>" --task "1.1" --summary "<short acceptance note>"
     ```
   - Roll back cleanly to `base_commit` if Codex corrupted the working tree:
     ```powershell
     python ".../orchestrate.py" --project "<PROJECT>" rollback --change "<change_id>" --task "1.1" --reason "<why>" --force
     ```

### 5. Inspect State & Console Control
- Query full state (bindings, blockers, decisions, recent messages):
  ```powershell
  python ".../orchestrate.py" --project "<PROJECT>" status --change "<change_id>"
  ```
- Pause/resume/abort from CLI or console plugin:
  ```powershell
  python ".../orchestrate.py" --project "<PROJECT>" control --change "<change_id>" --flag pause --note "Wait for API review"
  ```
- Launch the local real-time web console dashboard (reads SQLite WAL every 2s, supports Pause/Resume/Abort, User Note, and adding Decisions):
  ```powershell
  codex-orch --project "<PROJECT>" ui --port 8765 --open
  # or inside the project root directly:
  codex-orch
  ```
