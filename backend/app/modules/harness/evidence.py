"""Authoritative task edges for result aggregation and evidence reuse.

The kernel's context may mention other preserved work for navigation. That does
not make it an output dependency. Only current database edges authorize evidence.
"""

import json
import uuid

from sqlalchemy import select

from app.models.harness import ProductionTask


def evidence_tasks(db, run, task):
    rows = list(db.scalars(select(ProductionTask).where(ProductionTask.run_id == run.id)))
    by_id = {str(row.id): row for row in rows}
    if str(task.id) not in by_id or task.run_id != run.id:
        return []
    found, pending = {}, [task]
    while pending:
        current = pending.pop()
        if current.id in found:
            continue
        found[current.id] = current
        # A retained old revision is valid while the authoritative edge still
        # points to its succeeded node. Replaced nodes are never traversed.
        for ident in current.dependencies:
            dependency = by_id.get(str(ident))
            if dependency and dependency.status == "succeeded":
                pending.append(dependency)
        for ident in current.state.get("children", []):
            child = by_id.get(str(ident))
            if child and child.parent_id == current.id and child.status == "succeeded":
                pending.append(child)
    return list(found.values())


def collect_receipts(db, run, task, *, state=None):
    """Collect real current receipts, never suggested IDs or cached child snapshots."""
    result = {}
    for source in evidence_tasks(db, run, task):
        current = state if source.id == task.id and state is not None else source.state
        values = [step.get("receipt") for step in current.get("steps", [])]
        values.append(current.get("job_receipt"))
        if source.status == "succeeded":
            values.append(source.result)
            values.extend(source.result.get("receipts", []))
        for receipt in values:
            if not isinstance(receipt, dict) or receipt.get("status") != "succeeded":
                continue
            data = receipt.get("data")
            if not isinstance(data, dict):
                continue
            job = data.get("job_id") or data.get("preview_job_id")
            if job:
                try:
                    key = "job:" + str(uuid.UUID(str(job)))
                except (ValueError, TypeError):
                    continue
            else:
                key = json.dumps(receipt, sort_keys=True, ensure_ascii=False, default=str)
            result[key] = receipt
    return list(result.values())
