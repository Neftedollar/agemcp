-- Apache AGE extension
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- pgvector extension (for semantic search)
CREATE EXTENSION IF NOT EXISTS vector;

-- Vertex embeddings table for semantic search
CREATE TABLE IF NOT EXISTS vertex_embeddings (
    graph_name TEXT NOT NULL,
    vertex_ident TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(384) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (graph_name, vertex_ident)
);

-- HNSW index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_vertex_embeddings_hnsw
ON vertex_embeddings USING hnsw (embedding vector_cosine_ops);

-- Index for graph-scoped lookups
CREATE INDEX IF NOT EXISTS idx_vertex_embeddings_graph
ON vertex_embeddings (graph_name);
