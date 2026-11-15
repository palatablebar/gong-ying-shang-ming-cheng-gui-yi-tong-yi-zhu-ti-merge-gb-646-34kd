"""M7 导出编排：主数据、成员分摊、隔离区、冲突、运行清单。"""
from __future__ import annotations

import json

from ... import store
from ...errors import NotFound
from ...manifest import build_manifest
from ...reasons import code_table as reason_code_table
from ...reasons import V_UNKNOWN_RUN
from ..m5_clusters import service as cluster_service
from ..m8_golden import service as golden_service
from . import golden_out, manifest_out, master_out, quarantine_out


def _context(run_id: str) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise NotFound(V_UNKNOWN_RUN, run_id)
    cluster_service.ensure_clusters(run_id)
    records = {record["supplier_id"]: record for record in store.list_records(run_id)}
    clusters = store.list_clusters(run_id)
    members = store.list_cluster_members(run_id)
    return {"run": run, "records": records, "clusters": clusters, "members": members}


def master_csv(run_id: str) -> str:
    context = _context(run_id)
    return master_out.render(context["clusters"], context["members"], context["records"])


def members_csv(run_id: str) -> str:
    context = _context(run_id)
    return master_out.render_members(context["clusters"], context["members"], context["records"])


def quarantine_csv(run_id: str) -> str:
    run = store.get_run(run_id)
    if not run:
        raise NotFound(V_UNKNOWN_RUN, run_id)
    return quarantine_out.render(store.list_quarantine(run_id))


def conflicts_csv(run_id: str) -> str:
    run = store.get_run(run_id)
    if not run:
        raise NotFound(V_UNKNOWN_RUN, run_id)
    return quarantine_out.render_conflicts(store.list_conflicts(run_id))


def golden_csv(run_id: str) -> str:
    run = store.get_run(run_id)
    if not run:
        raise NotFound(V_UNKNOWN_RUN, run_id)
    golden_service.ensure_golden(run_id)
    return golden_out.render_golden(store.list_golden(run_id),
                                    store.list_provenance(run_id),
                                    store.list_amount_ledger(run_id))


def provenance_csv(run_id: str) -> str:
    run = store.get_run(run_id)
    if not run:
        raise NotFound(V_UNKNOWN_RUN, run_id)
    golden_service.ensure_golden(run_id)
    return golden_out.render_provenance(store.list_provenance(run_id))


def amount_ledger_csv(run_id: str) -> str:
    run = store.get_run(run_id)
    if not run:
        raise NotFound(V_UNKNOWN_RUN, run_id)
    golden_service.ensure_golden(run_id)
    return golden_out.render_amount_ledger(store.list_amount_ledger(run_id))


def golden_pending_csv(run_id: str) -> str:
    run = store.get_run(run_id)
    if not run:
        raise NotFound(V_UNKNOWN_RUN, run_id)
    golden_service.ensure_golden(run_id)
    return golden_out.render_pending(store.list_golden_pending(run_id))


def manifest_json(run_id: str) -> str:
    run = store.get_run(run_id)
    if not run:
        raise NotFound(V_UNKNOWN_RUN, run_id)
    cluster_service.ensure_clusters(run_id)
    golden_service.ensure_golden(run_id)
    ledger = cluster_service.reconcile(run_id)
    manifest = build_manifest(run, json.loads(run["files_json"]), ledger, reason_code_table())
    return manifest_out.render(manifest)
