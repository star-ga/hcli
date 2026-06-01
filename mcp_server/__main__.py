"""Entry point for `python -m mcp_server`."""
from mcp_server.server import mcp
import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
