"""Durable queue and publication point. One worker process owns this database."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "bridge.sqlite3"
        with self.connect() as conn:
            migrate_snapshots = not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'"
            ).fetchone()
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                    phase TEXT NOT NULL, error TEXT, result TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state, created);
                CREATE TABLE IF NOT EXISTS sources (
                    project_id TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(project_id, id));
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, revision TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (
                    revision TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    job_id TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0);
            """)
            conn.execute("BEGIN IMMEDIATE")
            migrations = {
                "jobs": {
                    "generation": "INTEGER NOT NULL DEFAULT 0",
                    "restore_after_job_id": "TEXT",
                    "source_job_id": "TEXT",
                },
                "sources": {
                    "generation": "INTEGER NOT NULL DEFAULT 0",
                    "last_delete_job_id": "TEXT",
                    "restore_after_job_id": "TEXT",
                },
            }
            migrate_deletions = False
            for table, fields in migrations.items():
                present = {
                    row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
                }
                for name, definition in fields.items():
                    if name not in present:
                        conn.execute(
                            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                        )
                        migrate_deletions |= (
                            table == "sources" and name == "last_delete_job_id"
                        )
            if migrate_deletions:
                for row in conn.execute(
                    "SELECT * FROM jobs WHERE kind='delete' ORDER BY created,rowid"
                ).fetchall():
                    conn.execute(
                        "INSERT INTO sources(project_id,id,payload,deleted,last_delete_job_id) VALUES(?,?,'{}',1,?) ON CONFLICT(project_id,id) DO UPDATE SET last_delete_job_id=excluded.last_delete_job_id",
                        (row["project_id"], row["source_id"], row["id"]),
                    )
            # Legacy published revisions have durable provenance. Never infer
            # ownership of other directories merely from their UUID-like name.
            if migrate_snapshots:
                for row in conn.execute(
                    "SELECT id,project_id,result FROM jobs WHERE state='ready' AND result IS NOT NULL"
                ).fetchall():
                    revision = json.loads(row["result"]).get("revision")
                    if revision:
                        conn.execute(
                            "INSERT OR IGNORE INTO snapshots(revision,project_id,job_id,published) VALUES(?,?,?,1)",
                            (revision, row["project_id"], row["id"]),
                        )

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _job(row):
        if row is None:
            return None
        out = dict(row)
        out["payload"] = json.loads(out["payload"])
        out["result"] = json.loads(out["result"]) if out["result"] else None
        return out

    def job(self, job_id):
        with self.connect() as conn:
            return self._job(
                conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            )

    def enqueue(
        self,
        job_id,
        project_id,
        source_id,
        kind,
        payload,
        max_pending=1000,
        *,
        restore_after_job_id=None,
        source_job_id=None,
        install_file=None,
    ):
        now = time.time()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._job(
                conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            )
            if existing:
                if (
                    existing["project_id"],
                    existing["source_id"],
                    existing["kind"],
                ) != (project_id, source_id, kind):
                    raise Conflict("Job id belongs to another operation")
                if existing["payload"] != payload:
                    raise Conflict("Job id has a different payload")
                if (
                    existing["restore_after_job_id"] != restore_after_job_id
                    or existing["source_job_id"] != source_job_id
                ):
                    raise Conflict("Job id has different lifecycle preconditions")
                return existing
            count = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','processing')"
            ).fetchone()[0]
            if count >= max_pending:
                raise Conflict("Processing queue is full")
            source = conn.execute(
                "SELECT * FROM sources WHERE project_id=? AND id=?",
                (project_id, source_id),
            ).fetchone()
            generation = source["generation"] if source else 0
            if kind == "upsert":
                if source:
                    previous = json.loads(source["payload"])
                    if (
                        previous.get("sha256")
                        and previous["sha256"] != payload["sha256"]
                    ):
                        raise Conflict(
                            "Source versions are immutable; use a new source id"
                        )
                    if source["deleted"]:
                        latest = conn.execute(
                            "SELECT * FROM jobs WHERE id=?",
                            (source["last_delete_job_id"],),
                        ).fetchone()
                        active_delete = conn.execute(
                            "SELECT 1 FROM jobs WHERE project_id=? AND source_id=? AND kind='delete' AND state IN ('queued','processing')",
                            (project_id, source_id),
                        ).fetchone()
                        if (
                            not restore_after_job_id
                            or not latest
                            or latest["id"] != restore_after_job_id
                            or latest["state"] != "ready"
                            or latest["generation"] != generation
                            or active_delete
                        ):
                            raise Conflict(
                                "Restore requires the latest completed deletion and no active cleanup"
                            )
                        generation += 1
                        if install_file:
                            install_file()
                        conn.execute(
                            "UPDATE sources SET generation=?,deleted=0,active=0,payload=?,restore_after_job_id=? WHERE project_id=? AND id=?",
                            (
                                generation,
                                json.dumps(payload),
                                restore_after_job_id,
                                project_id,
                                source_id,
                            ),
                        )
                    else:
                        if generation > 0 and not restore_after_job_id:
                            raise Conflict(
                                "Restored sources require a restore precondition"
                            )
                        if (
                            restore_after_job_id
                            and source["restore_after_job_id"] != restore_after_job_id
                        ):
                            raise Conflict(
                                "Restore precondition does not match this source generation"
                            )
                        if install_file:
                            install_file()
                else:
                    if restore_after_job_id:
                        raise Conflict("Restore precondition has no deleted source")
                    if install_file:
                        install_file()
                    conn.execute(
                        "INSERT INTO sources(project_id,id,payload) VALUES(?,?,?)",
                        (project_id, source_id, json.dumps(payload)),
                    )
            else:
                target = (
                    conn.execute(
                        "SELECT * FROM jobs WHERE id=?", (source_job_id,)
                    ).fetchone()
                    if source_job_id
                    else None
                )
                if source_job_id and (
                    not target
                    or target["kind"] != "upsert"
                    or target["project_id"] != project_id
                    or target["source_id"] != source_id
                    or target["generation"] != generation
                ):
                    raise Conflict(
                        "Deletion target does not match the source generation"
                    )
                if generation > 0 and not source_job_id:
                    raise Conflict(
                        "Restored sources require a source job deletion precondition"
                    )
                # Immediately exclude deleted sources even when WISE rebuild has failed.
                conn.execute(
                    "INSERT INTO sources(project_id,id,payload,deleted,last_delete_job_id) VALUES(?,?,'{}',1,?) ON CONFLICT(project_id,id) DO UPDATE SET deleted=1,active=0,last_delete_job_id=excluded.last_delete_job_id",
                    (project_id, source_id, job_id),
                )
                conn.execute(
                    "UPDATE jobs SET state='canceled',phase='source_deleted',updated=? WHERE project_id=? AND source_id=? AND kind='upsert' AND state IN ('queued','processing')",
                    (now, project_id, source_id),
                )
            conn.execute(
                "INSERT INTO jobs(id,project_id,source_id,kind,payload,generation,restore_after_job_id,source_job_id,state,phase,created,updated) VALUES(?,?,?,?,?,?,?,?,'queued','queued',?,?)",
                (
                    job_id,
                    project_id,
                    source_id,
                    kind,
                    json.dumps(payload),
                    generation,
                    restore_after_job_id,
                    source_job_id,
                    now,
                    now,
                ),
            )
        return self.job(job_id)

    def recover(self):
        with self.connect() as conn:
            # A single process lease makes these genuinely interrupted jobs.
            conn.execute(
                "UPDATE jobs SET state='queued',phase='recovering',updated=? WHERE state='processing' AND attempts<3",
                (time.time(),),
            )
            conn.execute(
                "UPDATE jobs SET state='failed',phase='failed',error='Repeated worker interruption',updated=? WHERE state='processing'",
                (time.time(),),
            )

    def claim(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            claimed = conn.execute(
                "UPDATE jobs SET state='processing',phase='preparing',attempts=attempts+1,updated=? WHERE id=? RETURNING *",
                (time.time(), row["id"]),
            ).fetchone()
        # Avoid another connection/read after committing the claim: if that read
        # failed, the loop would never receive the ID of its processing task.
        return self._job(claimed)

    def set_phase(self, job_id, phase):
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET phase=?,updated=? WHERE id=? AND state='processing'",
                (phase, time.time(), job_id),
            )

    def finish(self, job_id, state, error=None):
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET state=?,phase=?,error=?,updated=? WHERE id=? AND state='processing'",
                (state, state, error, time.time(), job_id),
            )

    def cancel(self, job_id):
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET state='canceled',phase='canceled',updated=? WHERE id=? AND state IN ('queued','processing')",
                (time.time(), job_id),
            )
        return self.job(job_id)

    def retry(self, job_id):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job and job["state"] == "failed":
                source = conn.execute(
                    "SELECT * FROM sources WHERE project_id=? AND id=?",
                    (job["project_id"], job["source_id"]),
                ).fetchone()
                if (
                    not source
                    or source["generation"] != job["generation"]
                    or bool(source["deleted"]) != (job["kind"] == "delete")
                ):
                    raise Conflict("Task belongs to a superseded source generation")
            conn.execute(
                "UPDATE jobs SET state='queued',phase='queued',error=NULL,attempts=0,updated=? WHERE id=? AND state='failed'",
                (time.time(), job_id),
            )
        return self.job(job_id)

    def sources(self, project_id, active_only=True):
        sql = "SELECT * FROM sources WHERE project_id=? AND deleted=0"
        if active_only:
            sql += " AND active=1"
        with self.connect() as conn:
            return {
                r["id"]: json.loads(r["payload"])
                for r in conn.execute(sql, (project_id,))
            }

    def head(self, project_id):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT revision FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            return row[0] if row else None

    def begin_snapshot(self, job, revision):
        """Register ownership before the first filesystem write can be interrupted."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state FROM jobs WHERE id=?", (job["id"],)
            ).fetchone()
            if not row or row["state"] != "processing":
                return False
            conn.execute(
                "INSERT INTO snapshots(revision,project_id,job_id) VALUES(?,?,?)",
                (revision, job["project_id"], job["id"]),
            )
        return True

    def snapshots(self, *, unpublished=False, project_id=None):
        conditions, values = [], []
        if unpublished:
            conditions.append("s.published=0")
        if project_id is not None:
            conditions.append("s.project_id=?")
            values.append(project_id)
        # A HEAD is protected even if an old/corrupt ownership record says otherwise.
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM projects p WHERE p.revision=s.revision)"
        )
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT s.* FROM snapshots s WHERE " + " AND ".join(conditions),
                    values,
                )
            ]

    def forget_snapshot(self, revision):
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM snapshots WHERE revision=? AND NOT EXISTS (SELECT 1 FROM projects WHERE revision=?)",
                (revision, revision),
            )

    def publish(self, job, revision, result, source_payload=None):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = conn.execute(
                "SELECT state FROM jobs WHERE id=?", (job["id"],)
            ).fetchone()[0]
            if state != "processing":
                return False
            source = conn.execute(
                "SELECT * FROM sources WHERE project_id=? AND id=?",
                (job["project_id"], job["source_id"]),
            ).fetchone()
            if (
                not source
                or source["generation"] != job["generation"]
                or bool(source["deleted"]) != (job["kind"] == "delete")
            ):
                conn.execute(
                    "UPDATE jobs SET state='canceled',phase='source_superseded',updated=? WHERE id=?",
                    (time.time(), job["id"]),
                )
                return False
            if job["kind"] == "upsert":
                conn.execute(
                    "UPDATE sources SET active=1,payload=? WHERE project_id=? AND id=?",
                    (
                        json.dumps(source_payload or job["payload"]),
                        job["project_id"],
                        job["source_id"],
                    ),
                )
            conn.execute(
                "INSERT INTO projects(id,revision) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision",
                (job["project_id"], revision),
            )
            conn.execute(
                "UPDATE snapshots SET published=1 WHERE revision=? AND project_id=? AND job_id=?",
                (revision, job["project_id"], job["id"]),
            )
            conn.execute(
                "UPDATE jobs SET state='ready',phase='ready',result=?,error=NULL,updated=? WHERE id=?",
                (json.dumps(result), time.time(), job["id"]),
            )
            return True
