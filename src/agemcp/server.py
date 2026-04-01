import json, logging, re

from pathlib import Path
from textwrap import dedent
from typing import Annotated, Any, Dict, List

# Signals
from blinker import Namespace
from bs4 import BeautifulSoup
from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from pyvis.network import Network
from sqlalchemy import text

from agemcp.ag_graph import AgGraph
from agemcp.apache_age import AgPatch, ApacheAGE
from agemcp.embeddings import get_embedder

logger = logging.getLogger(__name__)

# =====================================================
# MCP Setup
# =====================================================
mcp = FastMCP(name="AGEService")

# =====================================================
# Apache AGE Repository
# =====================================================
age = ApacheAGE()


# =====================================================
# Blinker Signals & Handlers
# =====================================================
signals = Namespace()

mutation_signal = signals.signal("mutation")

@mutation_signal.connect
async def on_mutation(sender: Any, ctx : Context, graph: AgGraph) -> None:
    """Signal handler for mutation events."""
    await ctx.log(f"Mutation event triggered by {sender!r}.", level="info")
    try:
        html_path = await _write_visjs_single_page_html_app_to_file(graph)
        await ctx.log(f"Graph visualization HTML written to {html_path!r}.", level="info")
    except Exception as e:
        await ctx.log(f"Error writing graph visualization HTML: {e} SUPPRESSED!", level="info")
    
# =====================================================
# Constants
# =====================================================

# Updated pattern: allow alphanum, underscore, dash, dot, and slash for graph names and idents
GRAPH_NAME_PATTERN = r"^[a-zA-Z0-9_\-\+\.\@\*\=\/]+$"
IDENT_PATTERN = r"^[a-zA-Z0-9_\-\+\.\@\*\=\/]+$"





# =====================================================
# Helper Functions
# =====================================================

async def _write_visjs_single_page_html_app_to_file( graph: AgGraph ) -> Path:
    """Write a single-page HTML file using vis.js's Network to visualize the graph."""
    nt = Network( height="1000px", width="100%", bgcolor="#FFFFFF", font_color="black", select_menu=True, filter_menu=True) # type: ignore

    nt.show_buttons(filter_=['physics'])

    if not graph.vertices or not graph.edges:
        nt.add_node("empty", label="No Data", color="#FF0000", title="No vertices or edges available")
        return Path("/dev/null")  # Return a dummy path if no data is present
        

    vertex_ident_to_data: Dict[str, Any] = {
        vertex.ident: {
            "ident": vertex.ident,
            "label": vertex.label,
            "properties": vertex.properties.model_dump_json(indent=2),
        } for vertex in graph.vertices
    }
    
    edge_ident_to_data: Dict[str, Any] = {
        f"{edge.start_ident}->{edge.end_ident}": {
            "ident": edge.ident,
            "label": edge.label,
            "properties": edge.properties.model_dump_json(indent=2),
            "start_ident": edge.start_ident,
            "end_ident": edge.end_ident
        } for edge in graph.edges
    }
    
    vertex_ident_to_data_json = json.dumps(vertex_ident_to_data, indent=2)
    edge_ident_to_data_json = json.dumps(edge_ident_to_data, indent=2)

    head_script = dedent("""
        function HTMLTitle(html) {
            let div = document.createElement('div');
            div.innerHTML = html;
            return div;
        }
        
        const vertex_ident_to_data = [[[vertex_ident_to_data_json]]];
        const edge_ident_to_data = [[[edge_ident_to_data_json]]];

        // Let js generate all the html, we'll provide the data from the backend
        function table_template(ident) {
            const data = vertex_ident_to_data[ident] || edge_ident_to_data[ident];
            return HTMLTitle(`
                <div>
                    <table>
                        <tr><th>Label</th><td>${data.label}</td></tr>
                        <tr><th>ID</th><td>${data.ident}</td></tr>` + 
                        ( data.start_ident ? `<tr><th>Start ID</th><td>${data.start_ident}</td></tr>` : '' ) + 
                        ( data.end_ident ? `<tr><th>End ID</th><td>${data.end_ident}</td></tr>` : '' ) + 
                        `<tr colspan=2><th>Properties</th></tr>
                        <tr><td colspan=2>
                            <pre>${data.properties}</pre>
                        </td></tr>
                    </table>
                </div>
            `);
        }
    """)
    
    head_script = head_script.replace("[[[vertex_ident_to_data_json]]]", vertex_ident_to_data_json)
    head_script = head_script.replace("[[[edge_ident_to_data_json]]]", edge_ident_to_data_json)

    for vertex in graph.vertices:
        nt.add_node(vertex.ident, label=vertex.ident, color="#eeffa0", title=f"PLACEHOLDER{vertex.ident}PLACEHOLDER")

    for edge in graph.edges:
        ident = f"{edge.start_ident}->{edge.end_ident}"
        nt.add_edge(edge.start_ident, edge.end_ident, label=edge.label, color="#6161614D", title=f"PLACEHOLDER{ident}PLACEHOLDER")

    html_dir = Path("/tmp/agemcp")
    html_dir.mkdir(parents=True, exist_ok=True)
    
    html_path = html_dir / f"{graph.name}.html"

    nt.cdn_resources = "in_line"  # Use CDN for resources
    nt.write_html(html_path.as_posix(), local=True, notebook=False, open_browser=False)
    
    html = html_path.read_text(encoding='utf-8')
    
    soup = BeautifulSoup(html, 'html.parser')

    # Inject Script
    script_tag = soup.new_tag("script")
    script_tag.string = head_script
    if el := soup.head or soup.body:
        el.append(script_tag)

    html = str(soup)

    html = re.sub(r"""['"]PLACEHOLDER(.+?)PLACEHOLDER['"]""", r'table_template("\1")', html, flags=re.DOTALL| re.MULTILINE)

    html_path.write_text(html, encoding='utf-8')
    
    return html_path




# =====================================================
# =====================================================
# Tools
# =====================================================
# =====================================================

# ====================================================================
# TOOL: generate_visualization
# ====================================================================
@mcp.tool(tags={"graph", "visualization", "pyvis", "vis.js"}, annotations=ToolAnnotations(idempotentHint=True))
async def generate_visualization(
    ctx: Context, 
    graph_name: Annotated[str, Field( description="Name of the graph to visualize", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN )]
) -> str:
    """Generate a single page html file using vis.js's Network to visualize any graph.

    Args:
        ctx: The request context.
        graph_name: The name of the graph to visualize.

    Returns:
        The path to the generated HTML file.
    """
    
    graph = await age.get_graph(graph_name)

    file_path = await _write_visjs_single_page_html_app_to_file(graph)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    return f"Run this from the command line to view the network web page: `open -a \"Google Chrome\" file://{file_path}`"


# ====================================================================
# TOOL: drop_graphs
# ====================================================================

@mcp.tool(tags={"graph", "drop", "mutation"}, annotations=ToolAnnotations(idempotentHint=False))
async def drop_graphs(
    ctx: Context,
    graph_names: Annotated[ List[str], Field( 
        description="A list of exact graph names to drop. ", 
        json_schema_extra={ "type": "array", "items": { "type": "string" } } 
    )]
) -> dict:
    """
    Drop one or more graphs by graph_name.

    Args:
        graph_names: Names of the graphs to drop.

    LLM Usage:
    - Use to remove graphs and all their associated data.
    - Returns: Confirmation of the graph drop operation.
    """
    for graph_name in graph_names:
        await age.drop_graph(graph_name)
    return {"status": "success", "graph_names": graph_names}

# ====================================================================
# TOOL: list_graphs
# ====================================================================

@mcp.tool(tags={"graph", "list", "metadata"})
async def list_graphs(ctx: Context) -> list[str]:
    """
    List all graph names in the database.
    
    LLM Usage:
    - Use to enumerate all available graph names managed by Apache AGE/PostgreSQL.
    - Returns: List of graph name strings.
    - Typical next step: select a graph for further operations (e.g., get_graph, add_vertex).
    """
    return await age.get_graph_names()

# ====================================================================
# TOOL: get_or_create_graph
# ====================================================================

@mcp.tool(tags={"graph", "create", "mutation"}, annotations=ToolAnnotations( idempotentHint=True ))
async def get_or_create_graph(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)]
) -> dict:
    """
    Get or create a graph with the specified name.
    
    Args:
        graph_name: Name of the graph to retrieve or create.
    
    LLM Usage:
    - Use to ensure a graph exists before performing operations on it.
    - Returns: Graph metadata as a dict (name, idents, etc.).
    - If the graph does not exist, it will be created.
    """
    
    graph = await age.get_or_create_graph(graph_name)
    
    await mutation_signal.send_async("get_or_create_graph", ctx=ctx, graph=graph)
    
    return graph.model_dump()

# ====================================================================
# TOOL: upsert_vertex
# ====================================================================

@mcp.tool(tags={"vertex", "insert", "upsert", "mutation"}, annotations=ToolAnnotations(idempotentHint=True))
async def upsert_vertex(
    ctx: Context,
    graph_name : Annotated[str, Field(description="Unique name of the graph where the vertex exists", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    vertex_ident : Annotated[str, Field(description="Unique ident of the vertex to update", min_length=1, max_length=128, pattern=IDENT_PATTERN)],
    label: Annotated[str, Field(description="Label of the vertex", min_length=1, max_length=256)] | None = None,
    properties: Annotated[dict, Field(
        description="Properties to add or update on the vertex", 
        json_schema_extra={
            "type": "object",
            "additionalProperties": True,
            "description": "Key-value pairs to add or update on the vertex."
        } 
    )] | None = None
                                      
) -> Dict[str, Any]:
    """
    Update or insert a vertex's properties in a graph non-destructively.

    Args:
        graph_name: Name of the graph where the vertex exists.
        vertex_ident: Unique ident of the vertex to update.
        label: Label of the vertex (optional).
        properties: Properties to add or update on the vertex (optional).

    Returns:
        Full representation of the graph after the upsert operation.

    LLM Usage:
    - Use to:
        - update or insert a vertex's properties or label in a graph.
        - insert a new vertex if it does not exist.
    
    """
    graph = await age.get_graph(graph_name)

    if not (vertex := graph.get_vertex_by_ident(vertex_ident)):
        vertex = graph.add_vertex(
            label if label else "Node",
            vertex_ident,
            properties=properties or {}
        )

    plan = {}
    if properties: plan["properties"] = properties
    if label: plan["label"] = label
    vertex.upsert(**plan)
    
    updated_graph = await age.upsert_graph(graph)
    if not updated_graph:
        raise Exception(f"Graph '{graph_name}' not found after upsert operation. Possible asynchronous operation dropped or modified during this operation.")

    await mutation_signal.send_async("upsert_vertex", ctx=ctx, graph=graph)

    return updated_graph.model_dump()

# ====================================================================
# TOOL: upsert_edge
# ====================================================================

@mcp.tool(tags={"edge", "insert", "upsert", "mutation"}, annotations=ToolAnnotations(idempotentHint=True))
async def upsert_edge(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Unique name of the graph where the edge exists", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    label: Annotated[str, Field(description="Label of the edge", min_length=1, max_length=256)],
    edge_start_ident: Annotated[str, Field(description="Unique ident of the start vertex", min_length=1, max_length=128, pattern=IDENT_PATTERN)],
    edge_end_ident: Annotated[str, Field(description="Unique ident of the end vertex", min_length=1, max_length=128, pattern=IDENT_PATTERN)],
    properties: Annotated[dict, Field(
        description="Properties to add or update on the edge", 
        json_schema_extra={
            "type": "object",
            "additionalProperties": True,
            "description": "Key-value pairs to add or update on the edge."
        } 
    )] | None = None
) -> Dict[str, Any]:
    """
    Update a graph's edge's properties non-destructively (only adds or updates existing properties).
    
    Important: 
        This cannot be used to change the start or end vertex of an edge. Instead use 
        drop_edge and then you may use this to upsert a new edge _correct_ edge.
        
        This is due to the fact that this is used as a composite key for the edge specification:
        [graph_name, label, edge_start_ident, edge_end_ident] is the unique identifier for an edge 
        in Apache AGE.

    Args:
        graph_name: Name of the graph where the edge exists.
        label: Label of the edge.
        edge_start_ident: Unique ident of the start vertex.
        edge_end_ident: Unique ident of the end vertex.
        properties: Properties to add or update on the edge.

    Returns:
        Full representation of the graph after the upsert operation.

    LLM Usage:
    - Use to:
        - update or insert an edge's properties in a graph.
        - insert a new edge if it does not exist.
    """
    graph = await age.get_graph(graph_name)

    if (edge := graph.edges.start_ident(edge_start_ident).end_ident(edge_end_ident).label(label).first()):
        edge.label = label
        for key, value in (properties or {}).items():
            edge.properties[key] = value
        await ctx.log(f"Edge {edge_start_ident}->{edge_end_ident} was found to exist and updated with properties: {properties} and label: {label}", level="info")
    else:
        edge = graph.add_edge( label, edge_start_ident, edge_end_ident, properties=properties or {})
        await ctx.log(f"Edge {edge_start_ident}->{edge_end_ident} was not found, so a new edge was created with properties: {properties} and label: {label}", level="info")
        
    
    updated_graph = await age.upsert_graph(graph)
    if not updated_graph:
        raise Exception(f"Graph '{graph_name}' not found after upsert operation. Possible asynchronous operation dropped or modified during this operation.")

    await mutation_signal.send_async("upsert_edge", ctx=ctx, graph=graph)

    return updated_graph.model_dump()

# ====================================================================
# TOOL: drop_vertex
# ====================================================================

@mcp.tool(tags={"vertex", "remove", "mutation"})
async def drop_vertex(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    vertex_ident: Annotated[str, Field(description="Unique ident of the vertex to remove", min_length=1, max_length=128, pattern=IDENT_PATTERN)]
) -> str:
    """
    Remove a vertex by ident.
    
    Args:
        graph_name: Name of the graph.
        vertex_ident: Unique ident of the vertex to remove.
        
    LLM Usage:
    - Use to delete a node from the graph by its ident.
    - Returns: Confirmation string or not-found message.
    - Edges connected to this vertex may also be removed.
    """
    graph = await age.get_graph(graph_name)
    vertex = graph.get_vertex_by_ident(vertex_ident)
    if not vertex:
        raise Exception(f"Vertex with ident '{vertex_ident}' not found in graph '{graph_name}', maybe you didn't use the correct vertex ident?")
    
    graph.remove_vertex(vertex)
    
    await age.upsert_graph(graph)
    
    await mutation_signal.send_async("drop_vertex", ctx=ctx, graph=graph)
    
    return f"Vertex '{vertex_ident}' removed."
    
    

# ====================================================================
# TOOL: drop_edge
# ====================================================================

@mcp.tool(tags={"edge", "remove", "mutation"})
async def drop_edge(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    edge_ident: Annotated[str, Field(description="Unique ident of the edge to remove", min_length=1, max_length=128, pattern=IDENT_PATTERN)],
) -> str:
    """Drop an edge by its ident.
    
    Args:
        graph_name: Name of the graph.
        edge_ident: Unique ident of the edge to remove.

    LLM Usage:
    - Use to delete an edge from the graph by its ident.
    - Returns: Confirmation string or not-found message.
    """
    graph = await age.get_graph(graph_name)
    edge = graph.get_edge_by_ident(edge_ident)
    if not edge:
        return f"Edge '{edge_ident}' not found in graph '{graph_name}', maybe you didn't use the correct edge ident?"
    graph.remove_edge(edge)
    
    await age.upsert_graph(graph)
    
    await mutation_signal.send_async("drop_edge", ctx=ctx, graph=graph)
    
    return f"Edge '{edge_ident}' removed."




@mcp.tool(tags={"graph", "mutation", "upsert"}, annotations=ToolAnnotations(idempotentHint=True))
async def upsert_graph(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    vertices: Annotated[List[dict], Field(
        description="List of vertex dicts to upsert", 
        json_schema_extra={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "The _type_ of vertex akin to a model name.",
                        "minLength": 1,
                        "maxLength": 128,
                        "example": [
                            "person", "idea", "organization", "location", "event", "node", "goal", "task", "project",
                            "concept", "has_many", "has_one", "belongs_to", "part_of", "owns", "child_of", "parent_to"
                        ],
                    },
                    "ident": {
                        "type": "string",
                        "description": "Primary unique ident for the vertex. If not provided, a new one will be generated."
                    },
                    "properties": {
                        "type": "object",
                        "description": "Key-value properties for the vertex akin to a model's attributes. (MUST BE PRESENT, EVEN IF JUST AN EMPTY OBJECT)",
                        "additionalProperties": True
                    }
                },
                "required": ["label", "properties"],
                "additionalProperties": False
            },
        },
    )],
    edges: Annotated[List[dict], Field(
        description="List of edge dicts to upsert",
        json_schema_extra={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ident": {
                        "type": "string",
                        "description": "Optional unique ident for the edge",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": IDENT_PATTERN
                    },
                    "start_ident": {
                        "type": "string",
                        "description": "Identifier of the start vertex",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": IDENT_PATTERN,
                    },
                    "end_ident": {
                        "type": "string",
                        "description": "Identifier of the end vertex",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": IDENT_PATTERN
                    },
                    "label": {
                        "type": "string",
                        "description": "Edge label",
                        "example": [
                            "PARENT_TO", "CHILD_OF", "PART_OF", "OWNS", "HAS_MANY", "HAS_ONE", "LINKS_TO"
                        ],
                    },
                    "properties": {
                        "type": "object",
                        "description": "Key-value properties for the edge (MUST BE PRESENT, EVEN IF JUST AN EMPTY OBJECT)",
                        "additionalProperties": True,
                        "example": {
                            "weight": 1.0,
                            "description": "This edge represents a relationship between two vertices.",
                        }
                    }
                },
                "required": ["label", "start_ident", "end_ident", "properties"],
                "additionalProperties": False
            }
        }
    )]
) -> dict:
    """
    Upserts both Vertices and Edges into the specified graph_name.

    This is a _hammer_ operation: it will deeply merge the provided vertices and edges into the existing graph, overwriting or adding as needed. Use with care.

    LLM Instructions:
        1. Use Chain of Through to represent an internal monolog that is vocalized.
        2. Use _Speech Act Theory_ to determine the _Illocutionary Force_ of the user's utterances.
        3. Determine the _Perlocutionary Effect_ expected by the user regarding the graph and how they expect it to change.
        4. Use the insights from steps 1-3 to present a crystalized synthesis of your thinking to the user, taking the form of a plan of action.
        5. Immediately use the insights and planning of the previous steps to form the parameters you generate for this tool, and execute it.

    Important:
        - This operation does not create a new graph if it does not exist; it will raise an error instead.
        - Vertices and edges are matched by their unique identifiers and labels. Existing properties will be updated or merged, and new ones will be added.
        - Edges are uniquely identified by [graph_name, label, start_ident, end_ident]. Changing the start or end vertex of an edge requires deleting and recreating the edge.

    Args:
        graph_name: Name of the graph to upsert into.
        vertices: List of vertex dicts to upsert. Each dict should include at least 'label' and 'properties'.
        edges: List of edge dicts to upsert. Each dict should include 'label', 'start_ident', 'end_ident', and 'properties'.

    Returns:
        The updated graph metadata as a dict.

    LLM Usage:
    - Use to:
        - Merge or update multiple vertices and edges in an existing graph in a single operation.
        - Add new vertices or edges if they do not exist.
        - Deeply update properties of existing vertices and edges.
    - Does NOT create a new graph if the specified graph does not exist.
    - Returns: The updated graph metadata as a dict.
"""
    graph = await age.get_graph(graph_name)
    if not graph:
        raise ValueError(f"Graph '{graph_name}' does not exist. Make sure you're using the correct graph name.")
    
    graph = graph.deepcopy()
    
    

    for vertex_data in vertices:
        if "properties" not in vertex_data:
            vertex_data["properties"] = {}
        graph.upsert_vertex(vertex_data)

    for edge_data in edges:
        if "properties" not in edge_data:
            edge_data["properties"] = {}
        graph.upsert_edge(edge_data)

    merged_graph = await age.upsert_graph(graph)

    await mutation_signal.send_async("upsert_graph", ctx=ctx, graph=merged_graph)

    return merged_graph.model_dump()


# ====================================================================
# TOOL: cypher_query
# ====================================================================

@mcp.tool(tags={"graph", "cypher", "query"}, annotations=ToolAnnotations(idempotentHint=True))
async def cypher_query(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph to query", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    query: Annotated[str, Field(description="Cypher query to execute (e.g. MATCH (n) RETURN n LIMIT 10)")],
) -> list[dict]:
    """Execute an arbitrary Cypher query on a graph and return results.

    Args:
        graph_name: Name of the graph.
        query: Cypher query string.

    Returns:
        List of decoded agtype records.

    LLM Usage:
    - Use for custom queries, aggregations, path-finding, or anything not covered by other tools.
    - Results are returned as decoded dictionaries.
    """
    records = await age.cypher_fetch(graph_name, query)
    return [r.to_dict() for r in records]


# ====================================================================
# TOOL: search_vertices
# ====================================================================

@mcp.tool(tags={"vertex", "search", "query"}, annotations=ToolAnnotations(idempotentHint=True))
async def search_vertices(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    label: Annotated[str | None, Field(description="Filter by vertex label")] = None,
    property_key: Annotated[str | None, Field(description="Property key to filter on")] = None,
    property_value: Annotated[str | None, Field(description="Property value to match (exact)")] = None,
    limit: Annotated[int, Field(description="Max results", ge=1, le=500)] = 50,
) -> dict:
    """Search vertices by label and/or property value.

    Args:
        graph_name: Name of the graph.
        label: Optional label filter.
        property_key: Optional property key to filter on.
        property_value: Optional value to match.
        limit: Maximum results.

    Returns:
        Matching vertices.
    """
    if label:
        cypher = f"MATCH (n:{label})"
    else:
        cypher = "MATCH (n)"

    if property_key and property_value is not None:
        safe_val = str(property_value).replace("'", "\\'")
        cypher += f" WHERE n.{property_key} = '{safe_val}'"

    cypher += f" RETURN n LIMIT {limit}"

    records = await age.cypher_fetch(graph_name, cypher)
    results = [r.to_dict() for r in records]
    return {"total": len(results), "vertices": results}


# ====================================================================
# TOOL: search_edges
# ====================================================================

@mcp.tool(tags={"edge", "search", "query"}, annotations=ToolAnnotations(idempotentHint=True))
async def search_edges(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    label: Annotated[str | None, Field(description="Filter by edge label")] = None,
    limit: Annotated[int, Field(description="Max results", ge=1, le=500)] = 50,
) -> dict:
    """Search edges by label.

    Args:
        graph_name: Name of the graph.
        label: Optional edge label filter.
        limit: Maximum results.

    Returns:
        Matching edges.
    """
    if label:
        cypher = f"MATCH ()-[e:{label}]->() RETURN e LIMIT {limit}"
    else:
        cypher = f"MATCH ()-[e]->() RETURN e LIMIT {limit}"

    records = await age.cypher_fetch(graph_name, cypher)
    results = [r.to_dict() for r in records]
    return {"total": len(results), "edges": results}


# ====================================================================
# TOOL: get_neighbors
# ====================================================================

@mcp.tool(tags={"graph", "traversal", "query"}, annotations=ToolAnnotations(idempotentHint=True))
async def get_neighbors(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    vertex_ident: Annotated[str, Field(description="Ident of the starting vertex", min_length=1, max_length=128, pattern=IDENT_PATTERN)],
    depth: Annotated[int, Field(description="Max traversal depth (hops)", ge=1, le=5)] = 1,
    direction: Annotated[str, Field(description="Traversal direction: out, in, or both")] = "both",
) -> dict:
    """Get N-hop neighbors of a vertex — useful for exploring graph context around an entity.

    Args:
        graph_name: Name of the graph.
        vertex_ident: Ident of the starting vertex.
        depth: Maximum hops (1-5).
        direction: 'out' (outgoing), 'in' (incoming), or 'both'.

    Returns:
        Subgraph of vertices and edges within the traversal radius.
    """
    ident_prop = "ident"  # AGE stores ident in properties

    if direction == "out":
        pattern = f"MATCH (start)-[e*1..{depth}]->(neighbor)"
    elif direction == "in":
        pattern = f"MATCH (start)<-[e*1..{depth}]-(neighbor)"
    else:
        pattern = f"MATCH (start)-[e*1..{depth}]-(neighbor)"

    cypher_vertices = f"{pattern} WHERE start.{ident_prop} = '{vertex_ident}' RETURN DISTINCT neighbor"
    cypher_edges = f"MATCH (start {{ident: '{vertex_ident}'}})-[e]-(neighbor) RETURN e"

    vertex_records = await age.cypher_fetch(graph_name, cypher_vertices)
    edge_records = await age.cypher_fetch(graph_name, cypher_edges)

    vertices = [r.to_dict() for r in vertex_records]
    edges = [r.to_dict() for r in edge_records]

    return {
        "center": vertex_ident,
        "depth": depth,
        "direction": direction,
        "vertices": vertices,
        "edges": edges,
    }


# ====================================================================
# TOOL: export_graph
# ====================================================================

@mcp.tool(tags={"graph", "export", "json"}, annotations=ToolAnnotations(idempotentHint=True))
async def export_graph(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph to export", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
) -> dict:
    """Export a graph as a JSON-serializable dict (vertices + edges + metadata).

    Args:
        graph_name: Name of the graph.

    Returns:
        Full graph representation as dict, suitable for backup or import_graph.

    LLM Usage:
    - Use to get a complete snapshot of a graph.
    - Output can be saved to a file or passed to import_graph to restore.
    """
    graph = await age.get_graph(graph_name)
    return graph.model_dump()


# ====================================================================
# TOOL: import_graph
# ====================================================================

@mcp.tool(tags={"graph", "import", "json", "mutation"}, annotations=ToolAnnotations(idempotentHint=False))
async def import_graph(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name for the imported graph", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    vertices: Annotated[List[dict], Field(
        description="List of vertex dicts with at least 'label' and 'properties' (including 'ident')",
        json_schema_extra={"type": "array", "items": {"type": "object"}},
    )],
    edges: Annotated[List[dict], Field(
        description="List of edge dicts with 'label', 'start_ident', 'end_ident', 'properties'",
        json_schema_extra={"type": "array", "items": {"type": "object"}},
    )],
) -> dict:
    """Import a graph from JSON data (vertices + edges). Creates the graph if it doesn't exist.

    Args:
        graph_name: Name for the graph.
        vertices: List of vertex dicts.
        edges: List of edge dicts.

    Returns:
        The imported graph metadata.

    LLM Usage:
    - Use to restore a graph from export_graph output or construct a graph from external data.
    - Vertices need at minimum: label, properties (with ident inside).
    - Edges need at minimum: label, start_ident, end_ident, properties.
    """
    graph = await age.get_or_create_graph(graph_name)
    graph = graph.deepcopy()

    for v in vertices:
        if "properties" not in v:
            v["properties"] = {}
        graph.upsert_vertex(v)

    for e in edges:
        if "properties" not in e:
            e["properties"] = {}
        graph.upsert_edge(e)

    merged = await age.upsert_graph(graph)
    await mutation_signal.send_async("import_graph", ctx=ctx, graph=merged)
    return merged.model_dump()


# ====================================================================
# TOOL: semantic_search (requires fastembed + pgvector)
# ====================================================================

@mcp.tool(tags={"graph", "search", "vector", "semantic"}, annotations=ToolAnnotations(idempotentHint=True))
async def semantic_search(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph to search", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    query: Annotated[str, Field(description="Natural language search query")],
    limit: Annotated[int, Field(description="Max results", ge=1, le=100)] = 10,
) -> dict:
    """Semantic similarity search over graph vertices using embeddings.

    Requires: fastembed installed (pip install 'agemcp[vector]') and pgvector extension.
    Vertices are auto-embedded on first search if not yet indexed.

    Args:
        graph_name: Name of the graph.
        query: Natural language query.
        limit: Max results.

    Returns:
        Matching vertices ranked by similarity.
    """
    embedder = get_embedder()
    if embedder is None:
        return {"error": "Vector search not available. Install with: pip install 'agemcp[vector]'"}

    # Ensure vertices are embedded
    await _sync_embeddings(graph_name, embedder)

    # Embed query
    query_embedding = embedder.embed(query)

    from agemcp.settings import get_settings
    dbs = get_settings().db.get_primary()

    async with dbs.sqlalchemy_transaction() as session:
        result = await session.execute(
            text("""
                SELECT vertex_ident, content, 1 - (embedding <=> :qvec::vector) AS similarity
                FROM vertex_embeddings
                WHERE graph_name = :graph_name
                ORDER BY embedding <=> :qvec::vector
                LIMIT :limit
            """),
            {"graph_name": graph_name, "qvec": str(query_embedding), "limit": limit}
        )
        rows = result.mappings().all()

    return {
        "query": query,
        "total": len(rows),
        "results": [{"vertex_ident": r["vertex_ident"], "content": r["content"], "similarity": round(float(r["similarity"]), 4)} for r in rows],
    }


# ====================================================================
# TOOL: graph_context (Graph RAG — semantic search + N-hop traversal)
# ====================================================================

@mcp.tool(tags={"graph", "rag", "context", "semantic"}, annotations=ToolAnnotations(idempotentHint=True))
async def graph_context(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    query: Annotated[str, Field(description="Natural language query to find relevant context")],
    top_k: Annotated[int, Field(description="Number of seed vertices from semantic search", ge=1, le=20)] = 5,
    depth: Annotated[int, Field(description="Hops to traverse from each seed vertex", ge=0, le=3)] = 1,
) -> dict:
    """Graph RAG: find semantically relevant vertices, then expand their neighborhood.

    Combines semantic_search with get_neighbors to gather rich context from the graph.

    Args:
        graph_name: Name of the graph.
        query: Natural language query.
        top_k: How many seed vertices to retrieve.
        depth: How many hops to expand around each seed.

    Returns:
        Seed vertices with their neighborhoods — ideal for grounding LLM responses.
    """
    embedder = get_embedder()
    if embedder is None:
        return {"error": "Vector search not available. Install with: pip install 'agemcp[vector]'"}

    await _sync_embeddings(graph_name, embedder)

    query_embedding = embedder.embed(query)

    from agemcp.settings import get_settings
    dbs = get_settings().db.get_primary()

    async with dbs.sqlalchemy_transaction() as session:
        result = await session.execute(
            text("""
                SELECT vertex_ident, content, 1 - (embedding <=> :qvec::vector) AS similarity
                FROM vertex_embeddings
                WHERE graph_name = :graph_name
                ORDER BY embedding <=> :qvec::vector
                LIMIT :limit
            """),
            {"graph_name": graph_name, "qvec": str(query_embedding), "limit": top_k}
        )
        seeds = result.mappings().all()

    context_parts = []
    for seed in seeds:
        ident = seed["vertex_ident"]
        part = {
            "vertex_ident": ident,
            "content": seed["content"],
            "similarity": round(float(seed["similarity"]), 4),
        }

        if depth > 0:
            try:
                ident_prop = "ident"
                cypher = f"MATCH (start {{ident: '{ident}'}})-[*1..{depth}]-(neighbor) RETURN DISTINCT neighbor"
                records = await age.cypher_fetch(graph_name, cypher)
                part["neighbors"] = [r.to_dict() for r in records]
            except Exception as e:
                part["neighbors_error"] = str(e)

        context_parts.append(part)

    return {
        "query": query,
        "graph_name": graph_name,
        "seeds": len(context_parts),
        "depth": depth,
        "context": context_parts,
    }


# ====================================================================
# Helper: sync vertex embeddings
# ====================================================================

async def _sync_embeddings(graph_name: str, embedder) -> None:
    """Ensure all vertices in the graph have up-to-date embeddings."""
    graph = await age.get_graph(graph_name)

    from agemcp.settings import get_settings
    dbs = get_settings().db.get_primary()

    # Get existing embeddings
    async with dbs.sqlalchemy_transaction() as session:
        result = await session.execute(
            text("SELECT vertex_ident FROM vertex_embeddings WHERE graph_name = :gn"),
            {"gn": graph_name}
        )
        existing = {r["vertex_ident"] for r in result.mappings().all()}

    # Find vertices that need embedding
    to_embed = []
    for v in graph.vertices:
        if v.ident and v.ident not in existing:
            content = f"{v.label}: {json.dumps(dict(v.properties), default=str)}"
            to_embed.append((v.ident, content))

    if not to_embed:
        return

    # Batch embed
    idents, contents = zip(*to_embed)
    embeddings = embedder.embed_batch(list(contents))

    # Store
    async with dbs.sqlalchemy_transaction() as session:
        for ident, content, emb in zip(idents, contents, embeddings):
            await session.execute(
                text("""
                    INSERT INTO vertex_embeddings (graph_name, vertex_ident, content, embedding)
                    VALUES (:gn, :vi, :content, :emb::vector)
                    ON CONFLICT (graph_name, vertex_ident) DO UPDATE
                    SET content = :content, embedding = :emb::vector, updated_at = NOW()
                """),
                {"gn": graph_name, "vi": ident, "content": content, "emb": str(emb)}
            )


# ====================================================================
# TOOL: sync_to_openbrain (bridge to openbrain-mcp)
# ====================================================================

@mcp.tool(tags={"graph", "openbrain", "bridge", "export"}, annotations=ToolAnnotations(idempotentHint=True))
async def sync_to_openbrain(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph to sync", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    category: Annotated[str, Field(description="OpenBrain memory category for all synced vertices")] = "observation",
    tag_prefix: Annotated[str, Field(description="Prefix for auto-generated tags")] = "graph",
) -> dict:
    """Export graph vertices as structured text suitable for openbrain-mcp store_batch.

    Does NOT call openbrain directly (they are separate MCP servers). Instead, returns
    a ready-to-use payload that the LLM can pass to openbrain's store_batch tool.

    Args:
        graph_name: Name of the graph.
        category: OpenBrain category for all memories.
        tag_prefix: Tag prefix (e.g. 'graph' → tags include 'graph:person').

    Returns:
        A dict with 'memories' array formatted for openbrain store_batch.

    LLM Usage:
    - Call this tool, then pass the returned 'memories' array to openbrain's store_batch tool.
    - This bridges the graph database with the semantic memory system.
    """
    graph = await age.get_graph(graph_name)

    memories = []
    for v in graph.vertices:
        props = dict(v.properties) if v.properties else {}
        props_str = ", ".join(f"{k}={v}" for k, v in props.items() if k not in ("ident", "start_ident", "end_ident"))
        content = f"[{v.label}] {v.ident}"
        if props_str:
            content += f" — {props_str}"

        # Include edge context
        connected = []
        for e in graph.edges:
            if e.start_ident == v.ident:
                connected.append(f"—{e.label}→ {e.end_ident}")
            elif e.end_ident == v.ident:
                connected.append(f"←{e.label}— {e.start_ident}")
        if connected:
            content += f". Relations: {'; '.join(connected)}"

        tags = [f"{tag_prefix}:{graph_name}", f"{tag_prefix}:{v.label}"]
        memories.append({"content": content, "category": category, "tags": tags})

    return {
        "graph_name": graph_name,
        "vertex_count": len(memories),
        "memories": memories,
        "usage_hint": "Pass the 'memories' array to openbrain's store_batch tool to sync.",
    }


# ====================================================================
# TOOL: import_from_openbrain (bridge from openbrain-mcp)
# ====================================================================

@mcp.tool(tags={"graph", "openbrain", "bridge", "import", "mutation"}, annotations=ToolAnnotations(idempotentHint=False))
async def import_from_openbrain(
    ctx: Context,
    graph_name: Annotated[str, Field(description="Name of the graph to import into", min_length=1, max_length=128, pattern=GRAPH_NAME_PATTERN)],
    memories: Annotated[List[dict], Field(
        description="Array of openbrain memories (each with 'id', 'content', 'category', 'tags')",
        json_schema_extra={"type": "array", "items": {"type": "object"}},
    )],
    connect_by_tags: Annotated[bool, Field(description="Auto-create edges between memories sharing tags")] = True,
) -> dict:
    """Import openbrain memories as graph vertices, optionally connecting them by shared tags.

    LLM Usage:
    - First call openbrain's search or list_recent to get memories.
    - Then pass the results here to build a knowledge graph from memories.
    - If connect_by_tags=True, memories with shared tags get SHARES_TAG edges.

    Args:
        graph_name: Target graph name.
        memories: Array of openbrain memory objects.
        connect_by_tags: Whether to auto-create edges for shared tags.

    Returns:
        Import summary.
    """
    graph = await age.get_or_create_graph(graph_name)
    graph = graph.deepcopy()

    tag_to_idents: Dict[str, list] = {}

    for mem in memories:
        mem_id = str(mem.get("id", ""))
        if not mem_id:
            continue

        ident = f"ob_{mem_id[:12]}"
        content = mem.get("content", "")
        category = mem.get("category", "other")
        tags = mem.get("tags", [])

        graph.upsert_vertex({
            "label": category,
            "ident": ident,
            "properties": {
                "content": content,
                "openbrain_id": mem_id,
                "tags": tags,
                "source": "openbrain",
            }
        })

        for tag in tags:
            tag_to_idents.setdefault(tag, []).append(ident)

    if connect_by_tags:
        for tag, idents in tag_to_idents.items():
            for i in range(len(idents)):
                for j in range(i + 1, len(idents)):
                    graph.upsert_edge({
                        "label": "SHARES_TAG",
                        "start_ident": idents[i],
                        "end_ident": idents[j],
                        "properties": {"tag": tag},
                    })

    merged = await age.upsert_graph(graph)
    await mutation_signal.send_async("import_from_openbrain", ctx=ctx, graph=merged)

    edge_count = len(merged.edges) - len((await age.get_graph(graph_name)).edges) if connect_by_tags else 0
    return {
        "graph_name": graph_name,
        "vertices_imported": len(memories),
        "edges_created": edge_count,
        "message": "Memories imported as vertices. Use get_neighbors or graph_context to explore.",
    }


