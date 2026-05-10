# Mnemosyne Core

Local-first agent harness MVP with a FastAPI backend, SQLite memory, safe tools, LiteLLM model adapter, eval records, and a React/Vite run console.

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
