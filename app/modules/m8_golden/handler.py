"""M8 路由：金账重建、金账明细、字段溯源、金额账本与待核对。"""
from __future__ import annotations

from fastapi import APIRouter

from ... import store
from ..m1_intake import service as intake_service
from . import service

router = APIRouter(prefix="/api", tags=["m8-golden"])


@router.post("/golden")
def rebuild_golden():
    run = intake_service.ensure_run()
    return service.ensure_golden(run["run_id"])


@router.get("/golden")
def list_golden(cluster_id: str = ""):
    run = intake_service.ensure_run()
    run_id = run["run_id"]
    service.ensure_golden(run_id)
    if cluster_id:
        return {
            "run_id": run_id,
            "summary": service.summary(run_id),
            "golden": store.get_golden(run_id, cluster_id),
            "provenance": store.list_provenance(run_id, cluster_id),
            "amount_ledger": store.list_amount_ledger(run_id, cluster_id),
        }
    return {
        "run_id": run_id,
        "summary": service.summary(run_id),
        "items": store.list_golden(run_id),
        "pending": store.list_golden_pending(run_id),
    }


@router.get("/golden/provenance")
def provenance(cluster_id: str = ""):
    run = intake_service.ensure_run()
    run_id = run["run_id"]
    service.ensure_golden(run_id)
    return {
        "run_id": run_id,
        "items": store.list_provenance(run_id, cluster_id),
    }


@router.get("/golden/amount-ledger")
def amount_ledger(cluster_id: str = ""):
    run = intake_service.ensure_run()
    run_id = run["run_id"]
    service.ensure_golden(run_id)
    return {
        "run_id": run_id,
        "items": store.list_amount_ledger(run_id, cluster_id),
        "summary": service.summary(run_id),
    }


@router.get("/golden/pending")
def pending_items():
    run = intake_service.ensure_run()
    run_id = run["run_id"]
    service.ensure_golden(run_id)
    return {
        "run_id": run_id,
        "items": store.list_golden_pending(run_id),
        "summary": service.summary(run_id),
    }
