"""Settings and paths. Importing this module never creates files."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib


def default_home():
    if os.environ.get("QUOTA_DESK_HOME"):
        return Path(os.environ["QUOTA_DESK_HOME"]).expanduser()
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "QuotaDesk"


@dataclass
class Settings:
    home: Path = field(default_factory=default_home)
    refresh_seconds: int = 180
    codex_auth_path: Path = field(default_factory=lambda: Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json")
    antigravity_exe: Path = field(default_factory=lambda: Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Programs/antigravity/Antigravity.exe")
    codex_launch: list[str] = field(default_factory=lambda: ["explorer.exe", r"shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App"])
    codex_process_names: list[str] = field(default_factory=lambda: ["ChatGPT.exe", "Codex.exe"])
    google_client_id: str = ""
    google_client_secret: str = ""
    openai_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"

    @classmethod
    def load(cls, home=None):
        settings = cls(home=Path(home).expanduser()) if home else cls()
        path = settings.home / "config.toml"
        if not path.exists():
            return settings
        with path.open("rb") as f:
            doc = tomllib.load(f)
        app = doc.get("app", {})
        settings.refresh_seconds = int(app.get("refresh_seconds", settings.refresh_seconds))
        if not 30 <= settings.refresh_seconds <= 3600:
            raise ValueError("refresh_seconds 必须在 30 到 3600 秒之间。")
        clients = doc.get("clients", {})
        for name in ("codex_auth_path", "antigravity_exe"):
            if name in clients:
                setattr(settings, name, Path(os.path.expandvars(clients[name])).expanduser())
        if "codex_launch" in clients:
            value = clients["codex_launch"]
            if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
                raise ValueError("codex_launch 必须是非空的命令参数数组。")
            settings.codex_launch = value
        if "codex_process_names" in clients:
            settings.codex_process_names = list(clients["codex_process_names"])
        oauth = doc.get("oauth", {})
        for name in ("google_client_id", "google_client_secret", "openai_client_id"):
            if name in oauth:
                setattr(settings, name, str(oauth[name]))
        return settings
