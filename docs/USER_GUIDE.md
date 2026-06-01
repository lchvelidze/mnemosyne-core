# Mnemosyne Core User Guide

This guide explains what Mnemosyne Core contains today, how the pieces fit together, how to run it, and how to use its dashboard, API, tools, memory, skills, terminal access, and safety boundaries.

## What Mnemosyne Core Is

Mnemosyne Core is a local-first agent control plane. The current MVP is built around a dashboard-first research agent:

- A user enters a goal in the Run Console.
- The backend creates or continues a durable conversation thread in SQLite.
- Each user goal and assistant final answer is stored as a thread message.
- The agent retrieves local memory and relevant reusable skills.
- The agent sends recent thread messages back into model context when continuing a thread.
- The model chooses safe tool calls.
- The backend executes only tools allowed by the run contract.
- Every step is persisted as timeline events.
- The model synthesizes a final Markdown answer.
- An eval record is created for the run.

The system is intentionally local-first and localhost-bound. It does not have multi-user auth, Kubernetes, Postgres, Redis, browser automation, or unrestricted shell execution.

## Architecture

```text
Browser Dashboard
  React + Vite at http://127.0.0.1:5173
        |
        | HTTP + Server-Sent Events
        v
FastAPI Backend
  mnemosyne_core.main:app at http://127.0.0.1:8003
        |
        +-- AgentRuntime
        |     - creates/continues runs
        |     - retrieves memory
        |     - retrieves relevant skills
        |     - calls LiteLLM through ModelClient
        |     - executes safe tools
        |     - records evals and events
        |
        +-- ToolRegistry
        |     - file tools
        |     - HTTP/web tools
        |     - terminal tools
        |     - skill tools
        |
        +-- SQLite Database
              - runs
              - threads
              - thread messages
              - run contracts
              - events
              - memories + FTS + local vectors
              - skills + FTS + local vectors
              - run memory links
              - tool calls
              - eval results
```

## Main Directories

- `mnemosyne_core/`: FastAPI backend, agent runtime, tools, model client, SQLite persistence.
- `frontend/`: React/Vite dashboard.
- `tests/`: backend unit/API tests.
- `docs/`: durable documentation.
- `data/`: local runtime data such as SQLite DB and elevated command logs. Do not commit it.
- `.env`: local configuration and secrets. Do not commit it.
- `.env.example`: safe configuration template.

## Configuration

Configuration is loaded from `.env` through `pydantic-settings` with the `MNEMOSYNE_` prefix. Provider credentials such as `OPENAI_API_KEY` are read from the environment by LiteLLM/OpenAI tooling.

Current template:

```dotenv
MNEMOSYNE_LITELLM_MODEL=gpt-5.5-2026-04-23
OPENAI_API_KEY=
MNEMOSYNE_MODEL_TIMEOUT_SECONDS=120

MNEMOSYNE_ALLOWED_FILE_ROOTS=["C:/path/to/mnemosyne-core","F:/"]

MNEMOSYNE_TERMINAL_ENABLED=true
MNEMOSYNE_TERMINAL_SHELLS=["powershell","wsl"]
MNEMOSYNE_TERMINAL_TIMEOUT_SECONDS=300
MNEMOSYNE_TERMINAL_MAX_OUTPUT_BYTES=100000
MNEMOSYNE_ELEVATED_POWERSHELL_ENABLED=true
MNEMOSYNE_ELEVATED_POWERSHELL_TIMEOUT_SECONDS=60
MNEMOSYNE_ELEVATED_POWERSHELL_LOG_DIR=data/elevated
MNEMOSYNE_ELEVATED_WSL_ENABLED=true
MNEMOSYNE_ELEVATED_WSL_TIMEOUT_SECONDS=300
MNEMOSYNE_ELEVATED_WSL_LOG_DIR=data/elevated-wsl
MNEMOSYNE_WSL_DISTRO=Ubuntu
MNEMOSYNE_WSL_ALLOWED_ROOTS=["/mnt/f","/mnt/c/path/to/mnemosyne-core"]
MNEMOSYNE_WSL_SHELL_MODE=interactive
```

Important settings:

- `MNEMOSYNE_LITELLM_MODEL`: model name sent to LiteLLM.
- `OPENAI_API_KEY`: provider credential. Keep only in `.env` or your OS environment.
- `MNEMOSYNE_MODEL_TIMEOUT_SECONDS`: maximum time to wait for each model call.
- `MNEMOSYNE_ALLOWED_FILE_ROOTS`: Windows paths where file tools and elevated logs may read/write.
- `MNEMOSYNE_TERMINAL_ENABLED`: enables the terminal tool.
- `MNEMOSYNE_TERMINAL_SHELLS`: allowed terminal shells, currently `powershell` and `wsl`.
- `MNEMOSYNE_TERMINAL_TIMEOUT_SECONDS`: max normal terminal command duration.
- `MNEMOSYNE_ELEVATED_WSL_TIMEOUT_SECONDS`: max elevated WSL command duration.
- `MNEMOSYNE_WSL_ALLOWED_ROOTS`: WSL directories allowed as working directories.
- `MNEMOSYNE_WSL_SHELL_MODE`: `interactive` loads `.bashrc`, useful for tools such as `openclaw` in user-local paths.

## Install

From the repo root:

```powershell
python -m pip install -e ".[dev]"
cd frontend
npm install
cd ..
```

## Start The App

Recommended current local setup:

```powershell
python -m uvicorn mnemosyne_core.main:app --reload --host 127.0.0.1 --port 8003
```

In another terminal:

```powershell
cd frontend
$env:VITE_API_BASE_URL = "http://127.0.0.1:8003"
npm run dev -- --port 5173
```

Open:

```text
http://127.0.0.1:5173/
```

Health check:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8003/health"
```

Expected:

```json
{
  "database": "ok",
  "model": "ok"
}
```

If `model` is `missing_configuration`, set `MNEMOSYNE_LITELLM_MODEL` and provider credentials.

## Development Commands

Backend checks:

```powershell
python -m ruff check .
python -m pytest tests -q
```

Frontend checks:

```powershell
cd frontend
npm test
npm run lint
npm run build
```

Git workflow rule for this repo:

```powershell
git status --short
git add <changed files>
git commit -m "Describe the change"
git push origin main
```

Do not commit `.env`, local SQLite databases, logs, caches, build output, or generated local artifacts.

## Dashboard Workflows

### Create Or Continue A Thread

1. Open `http://127.0.0.1:5173/`.
2. Select an existing thread from the left sidebar, or click **New Chat**.
3. Type a message in the chat composer at the bottom.
4. Open **Allowed Tools** if you want to restrict tool access for the next run.
5. Click **Send**.
6. Watch the run timeline while the answer is produced.
7. Inspect the chat transcript, final answer, memory hits, contract, and eval result.

Each submitted message creates a run. If a thread is selected, the run continues that thread and recent thread messages are included in the model request. If no thread is selected, the backend creates a new thread automatically.

### Retry A Run

1. Select a previous thread from **Threads**.
2. Click **Retry**.
3. A new run is created in the same thread using the selected run's goal and contract.

### Duplicate A Goal

1. Select a previous thread/run.
2. Click **Duplicate**.
3. The old goal is copied into the bottom chat composer.

### Cancel A Run

1. Select a running run.
2. Click **Cancel**.
3. The run is marked cancelled in SQLite and a cancellation message is appended to the thread.

For long-running shell work, prefer **Terminal Jobs** so status and logs persist separately from the agent run timeout.

### Add Memory

1. Use **Memory Manager**.
2. Enter a memory.
3. Click **Add Memory**.

Memory is stored in SQLite and indexed with FTS5 plus local vector embeddings. Future runs retrieve relevant memory automatically.

### Add A Skill

1. Use **Skill Manager**.
2. Fill in:
   - Skill name
   - Description
   - Instructions
   - Trigger terms
   - Preferred tools
   - Enabled/disabled state
3. Click **Add Skill**.

Existing skills can be edited or deleted directly from the Skill Manager list. Editing loads the skill into the form; deleting asks for browser confirmation first.

Skills are stored in SQLite and indexed with FTS5 plus local vector embeddings. Future runs retrieve relevant skills automatically and send them to the model as instruction context.

Example skill:

```text
Name: OpenClaw Model Run
Description: Run OpenClaw model inference from WSL.
Instructions: Use openclaw infer model run --prompt for one-shot model replies.
Trigger terms: openclaw, model run, inference
Preferred tools: run_terminal_command
```

## Agent Runtime Workflow

For each run, `AgentRuntime` does this:

1. Creates a new thread or validates the selected thread.
2. Creates a run linked to that thread.
3. Stores the user goal as a `user` thread message.
4. Loads the run contract.
5. Loads recent thread messages for model context.
6. Emits `plan.created`.
7. Searches memory with local vectors and SQLite FTS fallback.
8. Emits `memory.retrieved`.
9. Searches skills with local vectors and SQLite FTS fallback.
10. Emits `skills.retrieved`.
11. Calls the model with goal, recent thread context, memory, skills, and allowed tools.
12. Emits `model.started` and `model.completed`.
13. Executes requested safe tool calls.
14. Emits tool events such as `tool.started`, `tool.completed`, `tool.failed`, or `tool.blocked`.
15. Calls the model again to synthesize from tool results.
16. Emits synthesis events.
17. Scores the answer with a local rubric eval.
18. Emits `eval.completed`.
19. Stores the final answer as an `assistant` thread message.
20. Marks run completed or failed.

## Eval Rubrics

Each completed or failed run gets a persisted eval record. The top-level fields stay simple for history views:

- `score`: weighted score from `0` to `1`.
- `passed`: true when the run clears the local pass threshold.
- `notes`: short summary of what needs attention.
- `evaluator_version`: rubric implementation version.
- `rubric`: dimension-level scores and notes.

Current local rubric dimensions:

- Task Completion
- Success Criteria
- Tool Use
- Memory Use
- Grounding
- Clarity
- Safety

The dashboard Eval panel shows the total score plus each dimension with its weight and notes. The current evaluator is deterministic and local; it does not call the model, so evals continue to work when model configuration is missing.

## Timeline Events

Current timeline events include:

- `run.created`
- `plan.created`
- `memory.retrieved`
- `skills.retrieved`
- `model.started`
- `model.completed`
- `tool.started`
- `tool.completed`
- `tool.failed`
- `tool.blocked`
- `model.synthesis_started`
- `model.synthesis_tool_calls_detected`
- `model.synthesis_retry_started`
- `model.synthesis_completed`
- `eval.completed`
- `run.completed`
- `run.failed`
- `run.cancelled`

The dashboard consumes live events through Server-Sent Events:

```text
GET /runs/{run_id}/events
```

It can also load persisted event history:

```text
GET /runs/{run_id}/events.json
```

## API Reference

Base URL:

```text
http://127.0.0.1:8003
```

### Health

```http
GET /health
```

PowerShell:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8003/health"
```

### Tools

```http
GET /tools
```

PowerShell:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8003/tools"
```

### Execute A Tool Directly

```http
POST /tools/{tool_name}/execute
```

Body:

```json
{
  "arguments": { "expression": "2 + 2" },
  "confirm_risk": false
}
```

PowerShell:

```powershell
$body = @{
  arguments = @{ expression = "2 + 2" }
  confirm_risk = $false
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8003/tools/calculator/execute" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

Tools with write, terminal, modify, or elevated permission categories require `"confirm_risk": true`. The dashboard Tool Runner prompts before sending that confirmation.

### Terminal Jobs

Use terminal jobs for long-running local commands such as OpenClaw model runs. Jobs return immediately, keep running in the background, persist status/logs in SQLite, and expose live log streaming.

```http
GET /terminal/jobs
POST /terminal/jobs
GET /terminal/jobs/{job_id}
POST /terminal/jobs/{job_id}/cancel
GET /terminal/jobs/{job_id}/logs
GET /terminal/jobs/{job_id}/logs/stream
```

Create a WSL job:

```json
{
  "shell": "wsl",
  "working_directory": "/mnt/f",
  "shell_mode": "interactive",
  "command": "openclaw infer model run --prompt \"what were we working on today?\"",
  "confirm_risk": true
}
```

PowerShell:

```powershell
$body = @{
  shell = "wsl"
  working_directory = "/mnt/f"
  shell_mode = "interactive"
  command = 'openclaw infer model run --prompt "what were we working on today?"'
  confirm_risk = $true
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8003/terminal/jobs" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

The log stream is Server-Sent Events. It emits `terminal.job.log` records for stdout/stderr/system lines and `terminal.job.status` records when the job changes status.

### Create Thread

```http
POST /threads
```

Body:

```json
{
  "title": "Battery storage research"
}
```

PowerShell:

```powershell
$body = @{
  title = "Battery storage research"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8003/threads" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

### List Threads

```http
GET /threads
```

Returns thread summaries ordered by most recently updated first.

### Get Thread

```http
GET /threads/{thread_id}
```

Returns the thread summary plus ordered `messages` and linked `runs`.

### Create Run

```http
POST /runs
```

Body:

```json
{
  "goal": "Compare LFP and NMC batteries for home storage safety.",
  "thread_id": "optional-existing-thread-id",
  "constraints": "Use only selected safe tools and local memory visible in the control plane.",
  "allowed_tools": ["web_search", "http_get", "calculator"],
  "success_criteria": ["Produce a final answer", "Persist timeline events", "Create an eval record"],
  "expected_output": "Markdown answer with source links when web evidence is used."
}
```

Omit `thread_id` to create a new thread automatically. Provide `thread_id` to continue an existing thread and include recent thread messages in the model context.

PowerShell:

```powershell
$body = @{
  goal = "Compare LFP and NMC batteries for home storage safety."
  thread_id = "optional-existing-thread-id"
  allowed_tools = @("web_search", "http_get", "calculator")
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8003/runs" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

### List Runs

```http
GET /runs
```

### Get Run

```http
GET /runs/{run_id}
```

### Retry Run

```http
POST /runs/{run_id}/retry
```

### Cancel Run

```http
POST /runs/{run_id}/cancel
```

### Stream Run Events

```http
GET /runs/{run_id}/events
```

This is an SSE endpoint.

### Get Run Events As JSON

```http
GET /runs/{run_id}/events.json
```

### Get Memory Used By A Run

```http
GET /runs/{run_id}/memory
```

### List/Search Memory

```http
GET /memory
GET /memory?query=battery+safety
```

### Create Memory

```http
POST /memory
```

Body:

```json
{
  "text": "LFP batteries are generally preferred for residential safety-sensitive storage.",
  "source": "manual",
  "tags": ["battery", "safety"],
  "importance": 0.8
}
```

### Update Memory

```http
PUT /memory/{memory_id}
```

### Delete Memory

```http
DELETE /memory/{memory_id}
```

### List/Search Skills

```http
GET /skills
GET /skills?query=openclaw
GET /skills?enabled_only=true
```

### Create Skill

```http
POST /skills
```

Body:

```json
{
  "name": "OpenClaw Model Run",
  "description": "Run OpenClaw model inference from WSL.",
  "instructions": "Use openclaw infer model run --prompt for one-shot model replies.",
  "trigger_terms": ["openclaw", "model run", "inference"],
  "tool_names": ["run_terminal_command"],
  "enabled": true
}
```

### Update Skill

```http
PUT /skills/{skill_id}
```

### Delete Skill

```http
DELETE /skills/{skill_id}
```

### Export Memory And Skills

```http
GET /knowledge/export
```

Response:

```json
{
  "kind": "mnemosyne_core_knowledge_export",
  "schema_version": 1,
  "exported_at": "2026-06-01T12:00:00+00:00",
  "counts": {
    "memories": 2,
    "skills": 1
  },
  "memories": [],
  "skills": []
}
```

### Import Memory And Skills

```http
POST /knowledge/import
```

Merge import:

```json
{
  "mode": "merge",
  "memories": [
    {
      "text": "OpenClaw model inference should use openclaw infer model run --prompt.",
      "source": "backup",
      "tags": ["openclaw"],
      "importance": 0.8
    }
  ],
  "skills": []
}
```

Replace import:

```json
{
  "mode": "replace",
  "confirm_replace": true,
  "memories": [],
  "skills": []
}
```

Replace mode clears current local memory and skills before importing the supplied records. Merge mode updates matching ids, updates matching skill names, and skips exact duplicate memories.

## Tool Catalog

Tools are available to the agent during runs and to trusted local users through the dashboard Tool Runner or `POST /tools/{tool_name}/execute`. The model can request tool calls only if the run contract allows that tool. Direct manual execution is intended for local testing and requires confirmation for write/terminal/elevated categories.

### `read_text_file`

Permission:

```text
filesystem.read
```

Purpose:

Read a UTF-8 text file under configured `MNEMOSYNE_ALLOWED_FILE_ROOTS`.

Arguments:

```json
{
  "path": "F:/notes/example.md"
}
```

Safety:

- Blocks paths outside allowed roots.
- Requires path to be a file.
- Supports `/mnt/f/...` style conversion to Windows drive paths on Windows.

### `list_directory`

Permission:

```text
filesystem.read
```

Arguments:

```json
{
  "path": "F:/projects"
}
```

Safety:

- Blocks paths outside allowed roots.
- Requires path to be a directory.

### `write_text_file`

Permission:

```text
filesystem.write
```

Arguments:

```json
{
  "path": "F:/myagent_data.md",
  "text": "# Report\n\nContent here.\n",
  "overwrite": true
}
```

Safety:

- Writes only inside allowed roots.
- Creates parent folders inside allowed roots.
- Can refuse overwrite when `overwrite` is `false`.

### `calculator`

Permission:

```text
compute.safe
```

Arguments:

```json
{
  "expression": "(12.5 * 4) / 2"
}
```

Safety:

- Deterministic arithmetic only.
- Blocks arbitrary Python or shell execution.

### `http_get`

Permission:

```text
network.public_read
```

Arguments:

```json
{
  "url": "https://example.com"
}
```

Safety:

- Allows public HTTP/HTTPS.
- Blocks localhost/private network targets.
- Has timeout and byte-size limits.
- Does not follow redirects.

### `web_search`

Permission:

```text
network.public_search
```

Arguments:

```json
{
  "query": "AI agent memory benchmarks",
  "max_results": 5
}
```

Purpose:

Searches public web result pages and returns titles, URLs, and snippets.

Current limitation:

Search quality depends on accessible public search result HTML and may return sparse results for some queries.

### `create_skill`

Permission:

```text
skills.write
```

Arguments:

```json
{
  "name": "report_writer",
  "description": "Write concise Markdown research reports.",
  "instructions": "Use headings, bullets, source links, and a short recommendation.",
  "trigger_terms": ["report", "markdown", "summary"],
  "tool_names": ["web_search", "write_text_file"],
  "enabled": true
}
```

Purpose:

Lets the agent create reusable local skills when a user asks to teach or store a workflow.

### `list_skills`

Permission:

```text
skills.read
```

Arguments:

```json
{
  "query": "openclaw",
  "enabled_only": true
}
```

Purpose:

Lets the agent inspect existing reusable skills.

### `run_terminal_command`

Permission:

```text
terminal.modify
```

Arguments:

```json
{
  "shell": "wsl",
  "working_directory": "/mnt/f",
  "command": "openclaw status",
  "shell_mode": "interactive",
  "timeout_seconds": 300
}
```

PowerShell example:

```json
{
  "shell": "powershell",
  "working_directory": "F:/",
  "command": "Get-ChildItem",
  "timeout_seconds": 30
}
```

Safety:

- Disabled unless `MNEMOSYNE_TERMINAL_ENABLED=true`.
- Only allows shells listed in `MNEMOSYNE_TERMINAL_SHELLS`.
- Working directories must be inside allowed Windows roots or WSL roots.
- Has a maximum timeout.
- Truncates large output.
- Blocks dangerous command patterns such as recursive deletes against broad paths.

WSL behavior:

- Uses `wsl.exe -d <distro> --cd <dir> -- bash`.
- `shell_mode=interactive` uses `bash -ic`, loading `.bashrc`.
- This is why user-local tools such as `openclaw` can be found.

OpenClaw example goal:

```text
Run in WSL from /mnt/f:
openclaw infer model run --prompt "what were we working on today?"
```

The model should call `run_terminal_command` with WSL, `/mnt/f`, and a timeout up to `300`.

For commands expected to run longer than a normal tool call, use **Terminal Jobs** in the dashboard instead of `run_terminal_command`. Jobs are not tied to the agent run timeout and keep their stdout/stderr logs after refresh.

### `run_elevated_powershell`

Permission:

```text
terminal.elevated
```

Arguments:

```json
{
  "script_path": "F:/open_chrome.ps1",
  "working_directory": "F:/",
  "arguments": [],
  "timeout_seconds": 60
}
```

Purpose:

Launches a `.ps1` script through a Windows UAC elevation prompt.

Safety:

- Script path must be a `.ps1`.
- Script, working directory, and logs must stay inside allowed Windows roots.
- Requires local UAC approval.

### `run_elevated_wsl_command`

Permission:

```text
terminal.elevated
```

Arguments:

```json
{
  "working_directory": "/mnt/f",
  "command": "openclaw update",
  "distro": "Ubuntu",
  "shell_mode": "interactive",
  "timeout_seconds": 300
}
```

Purpose:

Launches a WSL command through Windows UAC and captures stdout/stderr/exit code logs.

Safety:

- Working directory must be under `MNEMOSYNE_WSL_ALLOWED_ROOTS`.
- Logs must stay inside allowed Windows roots.
- Requires local UAC approval.
- Returns captured output when logs complete within timeout.

## Sandboxes And Safety Boundaries

Mnemosyne Core has several practical safety layers:

### Localhost Binding

Backend and frontend bind to localhost:

```text
127.0.0.1
```

This is trusted local access only. There are no accounts or sessions in the MVP.

### Run Contract

Every run has a contract:

- Goal
- Constraints
- Allowed tools
- Success criteria
- Expected output

The backend blocks any tool not listed in the run contract.

### File Sandbox

File tools can only operate under:

```dotenv
MNEMOSYNE_ALLOWED_FILE_ROOTS
```

Example:

```dotenv
MNEMOSYNE_ALLOWED_FILE_ROOTS=["C:/Users/admin/Documents/Codex/2026-05-08/files-mentioned-by-the-user-deepseek","F:/"]
```

### WSL Working Directory Sandbox

WSL terminal commands can only start under:

```dotenv
MNEMOSYNE_WSL_ALLOWED_ROOTS
```

Example:

```dotenv
MNEMOSYNE_WSL_ALLOWED_ROOTS=["/mnt/f","/mnt/c/Users/admin/Documents/Codex/2026-05-08/files-mentioned-by-the-user-deepseek"]
```

### HTTP Network Guard

`http_get` blocks:

- `localhost`
- `127.0.0.1`
- private IP ranges
- local/private network targets

Use `web_search` for source discovery and `http_get` for public URLs only.

### Terminal Guard

Terminal tools:

- Must be explicitly enabled.
- Must use configured shells.
- Must run under allowed roots.
- Have timeouts.
- Have output truncation.
- Block dangerous command patterns.

This is controlled modifying terminal, not unrestricted autonomous shell access.

### Elevated Commands

Elevated PowerShell and elevated WSL:

- Trigger Windows UAC.
- Require local approval.
- Write wrapper/log files under allowed roots.
- Are meant for explicit admin tasks, not normal research runs.

## Memory

Memory records are local facts or notes. They have:

- `id`
- `text`
- `source`
- `tags`
- `importance`
- `created_at`

Memory is stored in SQLite and indexed using both FTS5 and local vector embeddings.

Vector retrieval is local and deterministic. Mnemosyne currently uses hashed lexical embeddings stored in SQLite, then blends vector similarity with FTS matches. If vectors are missing or produce no useful match, search falls back to FTS results.

Example use:

```powershell
$body = @{
  text = "OpenClaw model inference should use openclaw infer model run --prompt."
  source = "manual"
  tags = @("openclaw", "cli")
  importance = 0.8
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8003/memory" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## Skills

Skills are reusable workflow instructions. They are better than ordinary memory for repeatable procedures.

Skill fields:

- `id`
- `name`
- `description`
- `instructions`
- `trigger_terms`
- `tool_names`
- `enabled`
- `created_at`
- `updated_at`

Skills are retrieved during runs and included in the model prompt.

Example use cases:

- “When I ask about OpenClaw inference, use the correct `openclaw infer model run --prompt` command.”
- “When writing benchmark reports, use a standard section structure.”
- “When comparing repositories, always include URL, purpose, license if available, and last activity.”
- “When writing files, prefer `F:/...` paths in Windows tools.”

## Knowledge Backup

The dashboard has a **Knowledge Backup** panel for local memory and skills.

- **Export JSON** downloads the current memory and skills.
- **Merge** imports backup records without clearing local knowledge.
- **Replace** restores a backup by clearing current memory and skills first; the browser asks for confirmation.

The export intentionally omits generated search indexes. FTS rows and local vector embeddings are rebuilt by SQLite import/update logic, so a backup stays portable across machines and future retrieval implementations.

PowerShell export:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8003/knowledge/export" `
  | ConvertTo-Json -Depth 20 `
  | Set-Content -Path "F:\mnemosyne-knowledge.json"
```

PowerShell replace import:

```powershell
$backup = Get-Content "F:\mnemosyne-knowledge.json" -Raw | ConvertFrom-Json
$backup | Add-Member -NotePropertyName mode -NotePropertyValue "replace" -Force
$backup | Add-Member -NotePropertyName confirm_replace -NotePropertyValue $true -Force
$body = $backup | ConvertTo-Json -Depth 20

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8003/knowledge/import" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## OpenClaw Workflow Example

Create a skill:

```powershell
$body = @{
  name = "OpenClaw Model Run"
  description = "Run OpenClaw model inference from WSL."
  instructions = "Use openclaw infer model run --prompt for one-shot model replies. Use WSL working_directory /mnt/f and shell_mode interactive."
  trigger_terms = @("openclaw", "model run", "infer")
  tool_names = @("run_terminal_command")
  enabled = $true
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8003/skills" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

Then create a run:

```powershell
$body = @{
  goal = 'Run this OpenClaw command in WSL from /mnt/f and show the result: openclaw infer model run --prompt "what were we working on today?"'
  allowed_tools = @("run_terminal_command", "list_skills")
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8003/runs" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

The agent should retrieve the skill, choose `run_terminal_command`, and use:

```json
{
  "shell": "wsl",
  "working_directory": "/mnt/f",
  "command": "openclaw infer model run --prompt \"what were we working on today?\"",
  "shell_mode": "interactive",
  "timeout_seconds": 300
}
```

## Data Persistence

SQLite stores the durable state. By default:

```text
data/mnemosyne.db
```

Tables include:

- `threads`
- `thread_messages`
- `runs`
- `run_contracts`
- `events`
- `memories`
- `memories_fts`
- `memory_vectors`
- `skills`
- `skills_fts`
- `skill_vectors`
- `run_memories`
- `tool_calls`
- `eval_results`

Do not commit the database.

## Current Limitations

- No multi-user auth.
- No role-based permissions.
- No external vector database yet; local SQLite vectors are used for MVP retrieval.
- No graph memory yet.
- No Redis/Postgres/Kubernetes.
- Web search is lightweight and may produce sparse results.
- Skills are instruction records, not separate code packages.
- Terminal execution is intentionally constrained by roots, shell allowlists, timeouts, and blocked command patterns.

## Troubleshooting

### Backend Not Starting

Check port:

```powershell
netstat -ano | Select-String ":8003"
```

Start backend:

```powershell
python -m uvicorn mnemosyne_core.main:app --reload --host 127.0.0.1 --port 8003
```

Check logs:

```powershell
Get-Content -Tail 80 backend-8003.err.log
```

### Frontend Not Pointing At Backend

Check compiled API URL:

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:5173/src/api.ts" -UseBasicParsing |
  Select-Object -ExpandProperty Content |
  Select-String "VITE_API_BASE_URL"
```

Start frontend:

```powershell
cd frontend
$env:VITE_API_BASE_URL = "http://127.0.0.1:8003"
npm run dev -- --port 5173
```

### Model Missing

Check:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8003/health"
```

If model is missing, configure:

```dotenv
MNEMOSYNE_LITELLM_MODEL=<model-name>
OPENAI_API_KEY=<provider-key>
```

### WSL Command Not Found

Use:

```json
{
  "shell_mode": "interactive"
}
```

This loads `.bashrc`, which is needed for user-local commands such as `openclaw` installed under paths like:

```text
/home/<user>/.npm-global/bin
```

### Terminal Command Times Out

Default terminal timeout is currently:

```dotenv
MNEMOSYNE_TERMINAL_TIMEOUT_SECONDS=300
MNEMOSYNE_ELEVATED_WSL_TIMEOUT_SECONDS=300
```

For long OpenClaw runs, request `timeout_seconds: 300`.

### File Tool Says Path Outside Allowed Roots

Add the target root to:

```dotenv
MNEMOSYNE_ALLOWED_FILE_ROOTS
```

For Windows file tools, prefer drive-letter paths:

```text
F:/myagent_data.md
```

For WSL terminal working directories, use WSL paths:

```text
/mnt/f
```

## Recommended Next Upgrades

- Add a first-run setup screen for `.env` validation.

