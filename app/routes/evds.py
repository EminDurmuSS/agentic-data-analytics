"""On-demand EVDS (TCMB) observation acquisition.

Global, not workspace-scoped: acquired observations are merged into a single
shared overlay database (see data_pipeline/evds/acquisition.py) that every
workspace's lakehouse connection attaches read-only, so a series fetched from
any workspace becomes queryable from all of them on their next request.
"""
from __future__ import annotations

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from app.models import EvdsAcquireBody


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/evds/acquire")
    async def evds_acquire(body: EvdsAcquireBody):
        from data_pipeline.evds.acquisition import acquire_evds_series
        return await run_in_threadpool(acquire_evds_series, body.series_codes, body.start_date, body.end_date)

    return router
