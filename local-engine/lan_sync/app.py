"""Narrow HTTPS API for paired LAN sync devices."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .bridge import ProductionBridge
from .store import AuthError, PairingRateLimitError, SyncStore, ValidationError


def create_app(
    db_path: str | Path,
    *,
    enable_production_bridge: bool = True,
    pc_instance_id: str | None = None,
) -> FastAPI:
    if pc_instance_id is not None and re.fullmatch(r"[0-9a-f]{32}", pc_instance_id) is None:
        raise ValueError("invalid PC instance identity")
    store = SyncStore(db_path)
    store.initialize()
    bridge = ProductionBridge(store) if enable_production_bridge else None
    if bridge is not None:
        bridge.initialize()
        bridge.refresh()
    app = FastAPI(title="MoaDoom LAN Sync", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sync_store = store
    app.state.production_bridge = bridge

    @app.middleware("http")
    async def require_tls(request: Request, call_next):
        if request.url.scheme != "https":
            return JSONResponse(status_code=426, content={"detail": "TLS required"})
        return await call_next(request)

    def auth(authorization: str | None):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="paired device token required")
        try:
            return store.authenticate(authorization.removeprefix("Bearer ").strip())
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.post("/v1/pair")
    async def pair(request: Request) -> dict[str, Any]:
        body = await request.json()
        source = request.client.host if request.client is not None else "unknown"
        try:
            paired = store.pair_device(str(body.get("code", "")), str(body.get("device_name", "")), source)
            response = asdict(paired)
            if pc_instance_id is not None:
                response["pc_instance_id"] = pc_instance_id
            return response
        except PairingRateLimitError as exc:
            raise HTTPException(
                status_code=429,
                detail="pairing temporarily unavailable",
                headers={"Retry-After": str(exc.retry_after)},
            ) from exc
        except (AuthError, ValidationError) as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.post("/v1/sync")
    async def sync(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        ctx = auth(authorization)
        body = await request.json()
        try:
            operations = body.get("operations", [])
            if bridge is not None and not operations:
                bridge.refresh()
            results = store.push(
                ctx,
                operations,
                apply_callback=bridge.apply_operation if bridge is not None else None,
                refresh_callback=bridge.refresh if bridge is not None else None,
            ) if operations else []
            pulled = store.pull(ctx, int(body.get("cursor", 0)), int(body.get("limit", 200)))
            if body.get("tombstone_acks"):
                store.ack_tombstones(ctx, body["tombstone_acks"])
            return {
                "results": [asdict(item) for item in results],
                "changes": pulled.items,
                "next_cursor": pulled.next_cursor,
                "has_more": pulled.has_more,
                "conflicts": store.list_conflicts(ctx),
            }
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/continuity")
    async def continuity(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        ctx = auth(authorization)
        response = store.continuity(ctx)
        if pc_instance_id is not None:
            response["pc_instance_id"] = pc_instance_id
        return response

    @app.post("/v1/conflicts/{conflict_id}/resolve")
    async def resolve(conflict_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        ctx = auth(authorization)
        body = await request.json()
        try:
            return store.resolve_conflict(
                ctx,
                conflict_id,
                body.get("resolution", {}),
                bool(body.get("deleted", False)),
                apply_callback=bridge.apply_operation if bridge is not None else None,
                refresh_callback=bridge.refresh if bridge is not None else None,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.on_event("shutdown")
    def shutdown() -> None:
        store.close()

    return app
