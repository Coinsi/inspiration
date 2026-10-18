"""Database reference guards, installed with migrations and verified before GC."""

from sqlalchemy import text

from app.models import Base

EXCLUDED = {"blob", "blob_deletion"}


def install_v1(connection, schema=None):
    schema = schema or connection.scalar(text("SELECT current_schema()"))
    quote = connection.dialect.identifier_preparer.quote
    namespace = quote(schema)
    connection.execute(
        text(f"""
    CREATE OR REPLACE FUNCTION {namespace}.guard_blob_reference() RETURNS trigger
    LANGUAGE plpgsql AS $body$
    DECLARE blocked_hash text;
    BEGIN
      SELECT b.hash INTO blocked_hash FROM {namespace}.blob b
      JOIN {namespace}.blob_deletion d ON d.storage_uri = b.storage_uri
      WHERE d.status IN ('deleting','deleted') AND (
        jsonb_path_exists(to_jsonb(NEW), '$.** ? (@ == $digest)',
          jsonb_build_object('digest', b.hash))
        OR jsonb_path_exists(to_jsonb(NEW),
          '$.** ? (@.type() == "object").keyvalue().key ? (@ == $digest)',
          jsonb_build_object('digest', b.hash))
      ) LIMIT 1;
      IF blocked_hash IS NOT NULL THEN
        RAISE EXCEPTION 'Object is being reclaimed; restore before publishing references'
          USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END;
    $body$;
    """)
    )
    tables = connection.scalars(
        text("SELECT tablename FROM pg_tables WHERE schemaname=:schema"), {"schema": schema}
    )
    for name in sorted(tables):
        if name in EXCLUDED or name == "alembic_version":
            continue
        qualified = namespace + "." + quote(name)
        connection.execute(text(f"DROP TRIGGER IF EXISTS guard_blob_reference ON {qualified}"))
        connection.execute(
            text(f"""CREATE TRIGGER guard_blob_reference
            BEFORE INSERT OR UPDATE ON {qualified} FOR EACH ROW
            EXECUTE FUNCTION {namespace}.guard_blob_reference()""")
        )


def verify_and_lock(db):
    schema = db.connection().get_execution_options().get("schema_translate_map", {}).get(None)
    schema = schema or db.scalar(text("SELECT current_schema()"))
    tables = set(
        db.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname=:schema"), {"schema": schema}
        )
    )
    expected = {t.name for t in Base.metadata.sorted_tables}
    if tables - {"alembic_version"} != expected:
        raise RuntimeError("Storage GC schema coverage is incomplete")
    guarded = set(
        db.scalars(
            text("""SELECT c.relname FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=:schema AND t.tgname='guard_blob_reference' AND t.tgenabled='O'"""),
            {"schema": schema},
        )
    )
    if not expected - EXCLUDED <= guarded:
        raise RuntimeError("Storage reference guards are not installed")
    quote = db.bind.dialect.identifier_preparer.quote
    for name in sorted(expected - EXCLUDED):
        db.execute(
            text(f"LOCK TABLE {quote(schema)}.{quote(name)} IN SHARE ROW EXCLUSIVE MODE NOWAIT")
        )
