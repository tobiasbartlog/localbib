"""HTTP routes of the fixture Add-on, mounted under ``/api/plugins/hello/``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter


def build_router(plugin: Any) -> APIRouter:
    router = APIRouter(prefix="/api/plugins/hello")

    @router.get("/greeting/{citekey}")
    def greeting(citekey: str) -> dict:
        return {"text": plugin.greeting(citekey)}

    @router.get("/history")
    def history() -> dict:
        return {"greetings": plugin.history()}

    return router
