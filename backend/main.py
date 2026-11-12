"""Thin entry point so `uvicorn backend.main:app` works.

The FastAPI app is created by backend.api.main:create_app(); this module
exposes it as the module-level `app` object.
"""

from backend.api.main import create_app

app = create_app()
