"""Resolve server configuration once when building an application, never in routers."""

import os
from dataclasses import dataclass
from pathlib import Path

API_CONTRACT = "2"


@dataclass(frozen=True)
class Settings:
    project_root: Path
    backend_root: Path
    skills_dir: Path
    database_path: Path
    output_root: Path
    origins: tuple[str, ...]

    @classmethod
    def from_environment(cls):
        project = Path(__file__).resolve().parents[2]
        backend = project / "bakend"
        database = Path(os.getenv("LOCALFLOW_DATABASE_PATH", "data/localflow.db"))
        output = Path(os.getenv("LOCALFLOW_OUTPUT_ROOT", str(project / "outputs")))
        origins = tuple(
            v.strip()
            for v in os.getenv(
                "LOCALFLOW_CORS_ORIGINS",
                "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:5173,http://localhost:5173",
            ).split(",")
            if v.strip()
        )
        return cls(
            project,
            backend,
            project / "skills",
            database if database.is_absolute() else (backend / database).resolve(),
            output if output.is_absolute() else (project / output).resolve(),
            origins,
        )
