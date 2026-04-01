# agemcp

Apache AGE (PostgreSQL graph extension) MCP server. Fork of [veloper/agemcp](https://github.com/veloper/agemcp), extended by Neftedollar with Cypher queries, vector search (pgvector + fastembed), Graph RAG, multi-tenancy, import/export, and an OpenBrain memory bridge.

**Stack:** Python 3.11+, FastMCP, SQLAlchemy async + asyncpg, pgvector, optional fastembed (BAAI/bge-small-en-v1.5, 384-dim).

---

## Quick Start

### 1. Start the database

The Docker image is based on `apache/age:release_PG17_1.6.0` with pgvector installed. The `init.sql` script creates the AGE and vector extensions plus the `vertex_embeddings` table.

```bash
docker compose up -d
```

### 2. Install the server

```bash
# Core install
pip install agemcp

# With vector search support (fastembed + pgvector)
pip install 'agemcp[vector]'
```

### 3. Configure

```bash
agemcp config
```

This creates a `.env` file from `.env.example` and walks you through setting the database DSN, transport, host, port, and log level.

### 4. Run

```bash
agemcp run                          # stdio (default)
agemcp run --transport sse          # SSE on 0.0.0.0:8000
agemcp run --transport streamable-http --port 9000
```

---

## Tools (19 total)

### Original (9)

| Tool | Description |
|------|-------------|
| `get_or_create_graph` | Get or create a graph by name |
| `list_graphs` | List all graphs (tenant-scoped) |
| `drop_graphs` | Drop one or more graphs by name |
| `upsert_vertex` | Insert or update a vertex non-destructively |
| `upsert_edge` | Insert or update an edge non-destructively |
| `upsert_graph` | Deep-merge vertices and edges into an existing graph |
| `drop_vertex` | Remove a vertex by ident |
| `drop_edge` | Remove an edge by ident |
| `generate_visualization` | Generate a vis.js HTML visualization of a graph |

### Added (10)

| Tool | Description |
|------|-------------|
| `cypher_query` | Execute arbitrary Cypher queries against a graph |
| `search_vertices` | Search vertices by label and/or property value |
| `search_edges` | Search edges by label |
| `get_neighbors` | N-hop traversal (1-5 hops, directional) around a vertex |
| `export_graph` | Export a graph as JSON (vertices + edges) |
| `import_graph` | Import a graph from JSON data, creating it if needed |
| `semantic_search` | Vector similarity search over vertices (requires `agemcp[vector]`) |
| `graph_context` | Graph RAG -- semantic search seeds + N-hop expansion for LLM grounding |
| `sync_to_openbrain` | Export vertices as openbrain-compatible memories payload |
| `import_from_openbrain` | Build graph vertices/edges from openbrain memories |

---

## Environment Variables

Configuration uses pydantic-settings with `__` as the nested delimiter. Set values in `.env` or as environment variables.

| Variable | Description | Default |
|----------|-------------|---------|
| `MCP__TRANSPORT` | Transport protocol (`stdio`, `sse`, `streamable-http`) | `stdio` |
| `MCP__HOST` | Server bind host | `0.0.0.0` |
| `MCP__PORT` | Server bind port | `8000` |
| `MCP__LOG_LEVEL` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) | `DEBUG` |
| `DB__DSN` | SQLAlchemy async DSN (e.g. `postgresql+asyncpg://user:pass@localhost:5435/agemcp`) | -- |
| `DB__ECHO` | Echo SQL statements | `false` |
| `DB__POOL_MIN_CONNECTIONS` | Connection pool minimum | `5` |
| `DB__POOL_MAX_CONNECTIONS` | Connection pool maximum | `10` |
| `DB__POOL_MAX_OVERFLOW` | Pool overflow limit | `20` |
| `AGE__IDENT_PROPERTY` | Vertex ident property name | `ident` |
| `AGE__START_IDENT_PROPERTY` | Edge start ident property name | `start_ident` |
| `AGE__END_IDENT_PROPERTY` | Edge end ident property name | `end_ident` |
| `APP__LOG_LEVEL` | Application log level | `INFO` |
| `TENANT_ID` | Tenant identifier for graph name scoping | `default` |

---

## Architecture

```
Client (LLM / IDE)
    |
    v
FastMCP server  (server.py, stdio / SSE / streamable-http)
    |
    +-- Apache AGE layer (apache_age.py, ag_graph.py)
    |       |
    |       v
    |   PostgreSQL 17 + AGE 1.6.0  (Cypher over SQL)
    |
    +-- Embeddings layer (embeddings.py, optional)
            |
            v
        pgvector (384-dim HNSW index, cosine similarity)
        fastembed (BAAI/bge-small-en-v1.5)
```

### Multi-tenancy

Graph names are transparently prefixed with `t_{TENANT_ID}__` so each tenant's data is isolated. Set `TENANT_ID` as an environment variable. The `vertex_embeddings` table is scoped by `graph_name`, which includes the tenant prefix.

### Vector search

Optional. Install with `pip install 'agemcp[vector]'`. On first `semantic_search` or `graph_context` call, vertices without embeddings are auto-indexed. Embeddings are stored in the `vertex_embeddings` table with an HNSW index for cosine similarity.

---

## OpenBrain Bridge

Two tools connect agemcp to the [openbrain-mcp](https://github.com/neftedollar/openbrain-mcp) semantic memory server. They do not call openbrain directly -- they produce/consume payloads that the LLM relays between the two MCP servers.

### Export: graph -> openbrain

```
1. LLM calls agemcp.sync_to_openbrain(graph_name="project", category="architecture")
2. agemcp returns a { memories: [...] } payload
3. LLM passes that payload to openbrain.store_batch(memories=...)
```

Each vertex becomes a memory with its label, properties, and edge context serialized as content text. Tags are auto-generated from the graph name and vertex label.

### Import: openbrain -> graph

```
1. LLM calls openbrain.search(query="...", limit=50)
2. LLM passes the returned memories to agemcp.import_from_openbrain(graph_name="kb", memories=[...])
3. agemcp creates vertices from memories; if connect_by_tags=True, memories sharing tags get SHARES_TAG edges
```

---

## Client Configuration

### Claude Code / Roo / Cline (stdio)

```json
{
  "mcpServers": {
    "agemcp": {
      "command": "agemcp",
      "args": ["run"]
    }
  }
}
```

### Claude Code / Roo / Cline (HTTP)

```json
{
  "mcpServers": {
    "agemcp": {
      "url": "http://localhost:8000/mcp/",
      "type": "streamable-http"
    }
  }
}
```

### VS Code

1. Open Command Palette (`Cmd+Shift+P` / `Ctrl+Shift+P`)
2. Select **MCP: Add Server...**
3. Choose **HTTP**, enter `http://localhost:8000/mcp/`
4. Set server ID to `agemcp`, scope to **Global**

---

## Development

```bash
# Install with dev dependencies
pip install -e '.[vector]'
pip install -r requirements-dev.txt  # or: uv sync --group dev

# Run tests
pytest
```

## License

See upstream [veloper/agemcp](https://github.com/veloper/agemcp) for license terms.
