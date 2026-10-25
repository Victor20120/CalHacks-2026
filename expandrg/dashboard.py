"""The dashboard without Node: serves the built pages (expandrg/pages, made by build_package.py)
and passes /api on to the backend and /connector to the local connector, as the Vite dev server
does in a checkout (frontend/vite.config.js). Same address: http://127.0.0.1:5173.

    python -m uvicorn expandrg.dashboard:app --port 5173
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

PAGES = Path(__file__).resolve().parent / "pages"
# Headers that belong to one connection, not to the request or answer being passed on. httpx
# has already decoded the body, so its content-encoding no longer applies either.
_HOP = {"host", "connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding", "upgrade"}


def create_app(pages: Path = PAGES, backend: str | None = None, connector: str | None = None,
               transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    def forward_to(base: str, name: str):
        async def forward(request: Request) -> Response:
            url = f"{base}/{request.path_params['rest']}" + (f"?{request.url.query}" if request.url.query else "")
            headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
            async with httpx.AsyncClient(transport=transport, timeout=600) as client:
                try:
                    answer = await client.request(request.method, url, headers=headers, content=await request.body())
                except httpx.TransportError:
                    return Response(f'{{"detail": "The ExpandRG {name} isn\'t answering."}}', status_code=502,
                                    media_type="application/json")
            return Response(answer.content, status_code=answer.status_code,
                            headers={k: v for k, v in answer.headers.items() if k.lower() not in _HOP})
        return forward

    backend = (backend or os.environ.get("EXPANDRG_BACKEND") or "http://127.0.0.1:8000").rstrip("/")
    connector = (connector or os.environ.get("EXPANDRG_CONNECTOR") or "http://127.0.0.1:8001").rstrip("/")
    methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    return Starlette(routes=[
        Route("/api/{rest:path}", forward_to(backend, "backend"), methods=methods),
        Route("/connector/{rest:path}", forward_to(connector, "local connector"), methods=methods),
        Mount("/", StaticFiles(directory=pages, html=True, check_dir=False)),
    ])


app = create_app()
