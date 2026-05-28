"""
config.py — Load settings dan environment variables.
"""

import os
import sys
import yaml
from pathlib import Path
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def load_settings() -> dict:
    """Load settings.yaml dari folder config/."""
    settings_path = PROJECT_ROOT / "config" / "settings.yaml"
    if not settings_path.exists():
        print(f"[ERROR] settings.yaml tidak ditemukan: {settings_path}")
        sys.exit(1)
    with open(settings_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_apify_tokens(settings: dict) -> list:
    """Load semua token Apify yang valid dari .env."""
    token_envs = settings["apify"].get("token_envs", [])
    tokens = []
    for env_key in token_envs:
        token = os.getenv(env_key, "").strip()
        if token and not token.startswith("#"):
            tokens.append(token)
    if not tokens:
        print("[ERROR] Tidak ada APIFY_TOKEN yang valid di .env.")
        print(f"        Cek variabel: {token_envs}")
        sys.exit(1)
    return tokens


def get_paths(settings: dict) -> dict:
    """Resolve semua path output relatif terhadap PROJECT_ROOT."""
    return {
        "seen_comments": PROJECT_ROOT / settings["output"]["seen_comments"],
        "new_comments_csv": PROJECT_ROOT / settings["output"]["new_comments_csv"],
        "monitoring_log_csv": PROJECT_ROOT / settings["output"]["monitoring_log_csv"],
        "data_dir": PROJECT_ROOT / "data",
    }
