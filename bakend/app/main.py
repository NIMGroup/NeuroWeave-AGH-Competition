"""ASGI entry point. Use application.create_app for an isolated service graph in tests."""

from .application import create_app

app = create_app()
