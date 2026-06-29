from pathlib import Path
from typing import Dict, Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = PROJECT_ROOT / "config"
MODELS_DIR = PROJECT_ROOT / "models"
DATA_DIR = PROJECT_ROOT / "data"
RUNTIME_DIR = DATA_DIR / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_RULE_PATH = CONFIG_DIR / "rules.yaml"
DEFAULT_TRACKER_PATH = CONFIG_DIR / "tracker.yaml"
DEFAULT_VISION_BACKEND_PATH = CONFIG_DIR / "vision_backend.yaml"
DEFAULT_DB_PATH = RUNTIME_DIR / "ai_platform.db"


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_rules(path: Path = DEFAULT_RULE_PATH) -> Dict[str, Any]:
    return _load_yaml(path)


def load_tracker(path: Path = DEFAULT_TRACKER_PATH) -> Dict[str, Any]:
    return _load_yaml(path).get("tracker", {})


def load_vision_backend(path: Path = DEFAULT_VISION_BACKEND_PATH) -> Dict[str, Any]:
    return _load_yaml(path)
