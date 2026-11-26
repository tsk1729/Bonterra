"""Export tool contracts without starting the server or opening a database."""

import argparse
import asyncio
import json
from pathlib import Path

from assistance.mcp_server import mcp


async def export(output):
    tools = await mcp.list_tools()
    schemas = {tool.name: {"description": tool.description, "inputSchema": tool.parameters, "outputSchema": tool.output_schema} for tool in sorted(tools, key=lambda t: t.name)}
    output.write_text(json.dumps(schemas, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/mcp_tool_schemas.json"))
    args = parser.parse_args()
    asyncio.run(export(args.output))
