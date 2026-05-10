from __future__ import annotations

from mnemosyne_core.agent import AgentRuntime
from mnemosyne_core.api import create_app
from mnemosyne_core.config import Settings
from mnemosyne_core.db import Database
from mnemosyne_core.memory import MemoryStore
from mnemosyne_core.model_client import LiteLLMModelClient
from mnemosyne_core.tools import ToolRegistry


def build_runtime() -> AgentRuntime:
    settings = Settings()
    db = Database(settings.database_path)
    db.initialize()
    memory = MemoryStore(db)
    registry = ToolRegistry.safe_defaults(settings)
    model_client = LiteLLMModelClient(settings)
    return AgentRuntime(db, memory, registry, model_client)


app = create_app(build_runtime())
