"""Versioned PostgreSQL substring index shared by create_all and migration 0030.

Keep this v1 function stable: changing an IMMUTABLE indexed function's behavior
requires a new function/index migration, not CREATE OR REPLACE on existing data.
"""

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.sql.visitors import InternalTraversal

FUNCTION_NAME = "library_annotation_bigrams_v1"
INDEX_NAME = "ix_media_annotation_text_bigrams_v1"


class AnnotationBigrams(FunctionElement):
    type = ARRAY(Text())
    inherit_cache = True
    _traverse_internals = FunctionElement._traverse_internals + [
        ("schema_table", InternalTraversal.dp_clauseelement)
    ]

    def __init__(self, value, *, table):
        self.schema_table = table
        super().__init__(value)


@compiles(AnnotationBigrams, "postgresql")
def compile_bigrams(element, compiler, **kwargs):
    # Use the same schema translation as the table. Test schemas must never
    # depend on (or create/replace) a function in the application's schema.
    schema = compiler.preparer.schema_for_object(element.schema_table)
    prefix = compiler.preparer.quote_schema(schema) + "." if schema else ""
    return f"{prefix}{FUNCTION_NAME}({compiler.process(element.clauses, **kwargs)})"


def qualified_function(connection, schema=None):
    prefix = connection.dialect.identifier_preparer.quote_schema(schema) + "." if schema else ""
    return prefix + FUNCTION_NAME


def create_function(connection, schema=None):
    name = qualified_function(connection, schema)
    connection.exec_driver_sql(
        f"""CREATE FUNCTION {name}(value text) RETURNS text[]
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $function$
            SELECT COALESCE(
                pg_catalog.array_agg(DISTINCT pg_catalog.substr(normalized, position, 2)),
                ARRAY[]::text[]
            )
            FROM (SELECT pg_catalog.lower(value) AS normalized) AS normalized_text
            CROSS JOIN LATERAL
                pg_catalog.generate_series(1, pg_catalog.char_length(normalized) - 1)
                AS positions(position)
        $function$"""
    )


def drop_function(connection, schema=None):
    connection.exec_driver_sql(f"DROP FUNCTION {qualified_function(connection, schema)}(text)")


def before_annotation_create(table, connection, **_):
    if connection.dialect.name == "postgresql":
        create_function(connection, connection.schema_for_object(table))


def after_annotation_drop(table, connection, **_):
    if connection.dialect.name == "postgresql":
        drop_function(connection, connection.schema_for_object(table))
