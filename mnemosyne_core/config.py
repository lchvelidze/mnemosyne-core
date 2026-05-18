from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MNEMOSYNE_", env_file=".env", extra="ignore")

    database_path: str = "data/mnemosyne.db"
    allowed_file_roots: list[str] = Field(default_factory=lambda: [str(Path.cwd())])
    http_timeout_seconds: float = 5.0
    http_max_bytes: int = 1_000_000
    litellm_model: str | None = None
    terminal_enabled: bool = False
    terminal_shells: list[str] = Field(default_factory=lambda: ["powershell"])
    terminal_timeout_seconds: float = 120.0
    terminal_max_output_bytes: int = 100_000
    elevated_powershell_enabled: bool = False
    elevated_powershell_timeout_seconds: float = 60.0
    elevated_powershell_log_dir: str = "data/elevated"
    elevated_wsl_enabled: bool = False
    elevated_wsl_timeout_seconds: float = 120.0
    elevated_wsl_log_dir: str = "data/elevated-wsl"
    wsl_distro: str = "Ubuntu"
    wsl_allowed_roots: list[str] = Field(default_factory=list)
    wsl_shell_mode: str = "interactive"

    @property
    def model_configured(self) -> bool:
        return bool(self.litellm_model)
