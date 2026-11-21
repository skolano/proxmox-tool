from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".config" / "proxmox-tool"
CONFIG_FILE = CONFIG_DIR / "config.yaml"


class ConfigError(Exception):
    pass


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {"current_context": None, "contexts": {}}
    with CONFIG_FILE.open("r") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("current_context", None)
    data.setdefault("contexts", {})
    return data


def save_config(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)
    with CONFIG_FILE.open("w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    CONFIG_FILE.chmod(0o600)


def add_context(
    name: str,
    api_url: str,
    token_id: str,
    token_secret: str,
    verify_ssl: bool,
) -> None:
    data = load_config()
    data["contexts"][name] = {
        "api_url": api_url.rstrip("/"),
        "token_id": token_id,
        "token_secret": token_secret,
        "verify_ssl": verify_ssl,
    }
    if data.get("current_context") is None:
        data["current_context"] = name
    save_config(data)


def list_contexts() -> dict:
    return load_config()["contexts"]


def get_current_context_name() -> str | None:
    return load_config().get("current_context")


def set_current_context(name: str) -> None:
    data = load_config()
    if name not in data["contexts"]:
        raise ConfigError(f"Context '{name}' does not exist.")
    data["current_context"] = name
    save_config(data)


def get_active_context() -> dict:
    data = load_config()
    name = data.get("current_context")
    if not name or name not in data["contexts"]:
        raise ConfigError(
            "No active context. Run 'proxmox-tool context add', "
            "then 'proxmox-tool context use'."
        )
    context = dict(data["contexts"][name])
    context["name"] = name
    return context
