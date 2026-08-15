"""Provider-neutral MCP server backed by local Ollama and public job pages."""

from typing import Annotated

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
except ImportError:
    raise ImportError("MCP SDK not installed. Run: pip install 'autopilot-jobhunt[mcp]'")

from pydantic import Field

from job_hunt.tools import tool_draft, tool_export, tool_scan

mcp = FastMCP("autopilot-jobs")


@mcp.tool(annotations=ToolAnnotations(
    title="Scan and score public job postings locally", readOnlyHint=False,
    destructiveHint=False, idempotentHint=False, openWorldHint=True,
))
def scan_jobs() -> str:
    """Collect public postings directly and analyze locally. Never submits applications."""
    return tool_scan()


@mcp.tool(annotations=ToolAnnotations(
    title="Draft local application files (never applies)", readOnlyHint=False,
    destructiveHint=False, idempotentHint=False, openWorldHint=True,
))
def draft_application(
    job_ref: Annotated[str, Field(description="'#1' from the last scan or an allowlisted HTTPS job URL")],
) -> str:
    """Generate local review-only files with Ollama; never uploads or submits them."""
    return tool_draft(job_ref)


@mcp.tool(annotations=ToolAnnotations(
    title="Export locally stored jobs", readOnlyHint=False,
    destructiveHint=False, idempotentHint=True, openWorldHint=False,
))
def export_jobs(
    min_score: Annotated[int, Field(description="Minimum local fit score (0 includes all)")] = 0,
    days: Annotated[int, Field(description="History window in days (0 uses last scan)")] = 0,
) -> str:
    """Export stored results to output/ without network access."""
    return tool_export(min_score=min_score, days=days)


if __name__ == "__main__":
    mcp.run()
