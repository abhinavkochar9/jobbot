"""Configuration loading: config.yaml + profile.yaml + .env."""

import logging
import os
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

ROOT = Path(os.environ.get("JOBBOT_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = ROOT / "data"
MATERIALS_DIR = ROOT / "materials"
EVIDENCE_DIR = ROOT / "evidence"
REPORTS_DIR = ROOT / "reports"
LOGS_DIR = ROOT / "logs"
DB_PATH = DATA_DIR / "jobbot.db"
STOP_FILE = ROOT / "STOP"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_config() -> dict:
    _load_dotenv(ROOT / ".env")
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_profile() -> dict:
    with open(ROOT / "profile.yaml") as f:
        return yaml.safe_load(f)


def dry_run() -> bool:
    return os.environ.get("DRY_RUN", "true").strip().lower() != "false"


def stop_requested() -> bool:
    """Kill switch: `touch STOP` in the project root halts all applying."""
    return STOP_FILE.exists()


def ensure_dirs() -> None:
    for d in (DATA_DIR, MATERIALS_DIR, EVIDENCE_DIR, REPORTS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def setup_logging(name: str = "jobbot") -> None:
    ensure_dirs()
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOGS_DIR / f"{name}.log"),
        ],
    )
