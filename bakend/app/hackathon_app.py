"""Competition server entry; the retired synthetic demo has no runtime route."""
import os

from .agh_server import build_server_app


def build_app():
    if os.getenv("NEUROWEAVE_HACKATHON") != "true" or os.getenv("NEUROWEAVE_AGH_SERVER") != "true":
        raise RuntimeError("Use scripts/hackathon/server-backend.sh with a dedicated server configuration")
    return build_server_app()
