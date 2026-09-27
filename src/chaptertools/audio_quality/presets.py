"""Named parameter sets. Shipped presets live in the package as JSON;
the user's own presets and the last session live under the config dir."""

from __future__ import annotations

import json
import os
from pathlib import Path

_SHIPPED = Path(__file__).resolve().parent / "preset_data" / "presets.json"


def config_dir() -> Path:
    override = os.environ.get("CHAPTERFIT_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "chapterfit"


def user_presets_path() -> Path:
    return config_dir() / "quality-presets.json"


def session_path() -> Path:
    return config_dir() / "quality-session.json"


def load_shipped() -> dict[str, dict]:
    with open(_SHIPPED, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("shipped presets must be a JSON object")
    return data


def load_user() -> dict[str, dict]:
    path = user_presets_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def all_presets() -> dict[str, dict]:
    """Shipped presets, then the user's, so a saved name replaces a shipped one."""
    merged = load_shipped()
    merged.update(load_user())
    return merged


def save_user_preset(name: str, snapshot: dict) -> Path:
    current = load_user()
    current[name] = snapshot
    _write_user(current)
    return user_presets_path()


def rename_user_preset(old: str, new: str) -> bool:
    """Rename a saved preset. Shipped names are not in this file, so they
    cannot be renamed. Refuses a name that is already saved."""
    current = load_user()
    if old not in current or not new or new == old or new in current:
        return False
    current[new] = current.pop(old)
    _write_user(current)
    return True


def delete_user_preset(name: str) -> bool:
    """Drop a saved preset. A shipped preset with no saved override stays."""
    current = load_user()
    if name not in current:
        return False
    del current[name]
    _write_user(current)
    return True


def _write_user(current: dict) -> None:
    path = user_presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")


def save_session(snapshot: dict) -> Path:
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return path


def load_session() -> dict | None:
    path = session_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
