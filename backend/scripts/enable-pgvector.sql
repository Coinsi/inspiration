-- Enable pgvector on an existing Inspiration database.
-- Run AFTER the pgvector binary (vector.dll + control/sql) is installed into the
-- PostgreSQL instance. The initial migration was applied with ENABLE_PGVECTOR=false,
-- so neither the extension nor the embedding columns exist yet.
--
-- Usage (from backend dir, adjust db name if needed):
--   & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d inspiration -f scripts/enable-pgvector.sql
-- (or it is run automatically by scripts/install-pgvector.ps1)

CREATE EXTENSION IF NOT EXISTS vector;

-- Vector columns (dim 512, see app/models/asset.py & generation.py). Idempotent.
ALTER TABLE asset      ADD COLUMN IF NOT EXISTS embedding vector(512);
ALTER TABLE generation ADD COLUMN IF NOT EXISTS embedding vector(512);

-- (Optional) ANN indexes for similarity search once embeddings are populated:
-- CREATE INDEX IF NOT EXISTS ix_asset_embedding      ON asset      USING hnsw (embedding vector_cosine_ops);
-- CREATE INDEX IF NOT EXISTS ix_generation_embedding ON generation USING hnsw (embedding vector_cosine_ops);
