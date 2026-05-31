-- LINA — Migration 007: pgvector semantic memory
-- Habilita búsqueda por similitud de coseno en la tabla memories.
--
-- Requiere: imagen pgvector/pgvector:pg16 (ya incluye la extensión).
-- La extensión se crea si no existe (idempotente).
--
-- Rollback:
--   DROP INDEX IF EXISTS idx_memories_embedding;
--   ALTER TABLE memories DROP COLUMN IF EXISTS embedding;
--   DROP EXTENSION IF EXISTS vector;

-- 1. Habilitar extensión pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Agregar columna de embedding (1536 = dimensión de text-embedding-3-small)
ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- 3. Índice IVFFlat para búsqueda aproximada (ANN) por cosine similarity
--    lists = 100 es adecuado para hasta ~100k filas.
--    El índice se entrena con datos existentes al crearse.
CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON memories
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);