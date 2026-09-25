"""Shared paths for the repository layout used locally and under /app in Docker."""

from pathlib import Path


def find_project_root(source: Path) -> Path:
    for candidate in source.resolve().parents:
        if (candidate / "src/backend/arena").is_dir():
            return candidate
    raise RuntimeError("Deployment must contain src/backend/arena under its project root")


PROJECT_ROOT = find_project_root(Path(__file__))
BACKEND_DIR = PROJECT_ROOT / "src/backend"
CONFIG_DIR = BACKEND_DIR / "arena/config"
EVALUATION_DIR = BACKEND_DIR / "evaluation"


def project_path(value: str | Path) -> Path:
    """Configuration paths are relative to the project, never the working directory."""
    return (PROJECT_ROOT / Path(value).expanduser()).resolve()


def sqlite_path(url: str) -> str:
    if not url.startswith("sqlite:///"):
        raise ValueError("DATABASE_URL must use sqlite:///relative/path or sqlite:////absolute/path")
    value = url.removeprefix("sqlite:///")
    if not value:
        raise ValueError("DATABASE_URL must include a database path")
    return value if value == ":memory:" else str(project_path(value))
