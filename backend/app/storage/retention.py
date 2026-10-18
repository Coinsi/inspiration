"""Conservative, global CAS retention audit. This does not authorize physical deletion.

Foreign-key users and JSON document/task references are discovered from registered
models so a new document type does not silently disappear from the retention roots.
Run inside a read-only repeatable-read transaction for an inventory snapshot.
Deletion still requires fencing new references and writers, not a prior audit result.
"""

import re

from sqlalchemy import JSON, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH

from app.models import Base
from app.models.storage import Blob


def sources():
    result = []
    for table in Base.metadata.sorted_tables:
        if table.name in {"blob", "blob_deletion"}:
            continue
        for column in table.columns:
            if any(fk.target_fullname == "blob.hash" for fk in column.foreign_keys):
                result.append((table, column, "foreign_key"))
            elif isinstance(column.type, JSON):
                result.append((table, column, "document_or_task"))
    return result


def inspect_blob(db, digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Expected a SHA-256 content hash")
    blob = db.get(Blob, digest)
    reasons = []
    for table, column, kind in sources():
        predicate = column == digest
        if kind != "foreign_key":
            value = cast(column, JSONB)
            variables = cast({"digest": digest}, JSONB)
            predicate = or_(
                func.jsonb_path_exists(value, cast("$.** ? (@ == $digest)", JSONPATH), variables),
                func.jsonb_path_exists(
                    value,
                    cast('$.** ? (@.type() == "object").keyvalue().key ? (@ == $digest)', JSONPATH),
                    variables,
                ),
            )
        # Existence avoids expensive counts and exposes no other tenant's identities.
        if db.scalar(select(literal(True)).select_from(table).where(predicate).limit(1)):
            reasons.append({"table": table.name, "field": column.name, "kind": kind})
    return {
        "hash": digest,
        "registered": blob is not None,
        "size_bytes": blob.size_bytes if blob else None,
        "retained": bool(reasons),
        "reasons": reasons,
        "physical_delete_allowed": False,
        "status": "retained" if reasons else "unreferenced_at_audit_time",
    }


def main():
    import argparse
    import json

    from sqlalchemy import text

    from app.core.database import SessionLocal

    parser = argparse.ArgumentParser(description="Read-only global content retention audit")
    parser.add_argument("hash", help="SHA-256 hash, never a filename or path")
    args = parser.parse_args()
    with SessionLocal() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout = '15000ms'"))
        print(json.dumps(inspect_blob(db, args.hash), ensure_ascii=False, indent=2))
        db.rollback()


if __name__ == "__main__":
    main()
