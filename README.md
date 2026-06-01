# Mnemosyne Core

Local-first agent harness MVP with a FastAPI backend, SQLite memory with local vector retrieval and FTS fallback, reusable agent skills, safe tools, terminal jobs, LiteLLM model adapter, rubric eval records, and a React/Vite run console.

## Quick Start

```powershell
python -m pip install -e ".[dev]"
cd frontend
npm install
cd ..
python -m uvicorn mnemosyne_core.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd frontend
npm run dev
```

Set `MNEMOSYNE_LITELLM_MODEL` and provider credentials such as `OPENAI_API_KEY` before using a real model. Without a configured model, `/health` reports the missing configuration clearly.

Copy `.env.example` to `.env` for local configuration. Do not commit `.env`; it contains local paths and provider credentials.

## Skills

Mnemosyne skills are local reusable instructions for repeatable agent workflows. They are stored in SQLite, searchable with local vectors plus FTS fallback, exposed to the model as relevant context, and manageable through the dashboard Skill Manager or API:

- `GET /skills`
- `POST /skills`
- `PUT /skills/{skill_id}`
- `DELETE /skills/{skill_id}`

The agent also has safe skill tools: `create_skill` and `list_skills`.

## Knowledge Backup

Memory and skills can be exported, imported, migrated, and shared as JSON:

- `GET /knowledge/export`
- `POST /knowledge/import`

Imports support `merge` for additive restores and `replace` for full backup restoration with explicit confirmation.

## Full User Guide

See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) for the current architecture, startup commands, API examples, dashboard workflows, tool catalog, skills, memory, sandboxes, terminal/WSL usage, and troubleshooting notes.
