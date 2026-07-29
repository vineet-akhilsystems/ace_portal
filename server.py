"""ACE MCP entry point.

This thin shim keeps the path registered with Claude Code / Claude Desktop
(`py .../ace_mcp/server.py`) working after the code moved into the `ace`
package. All logic lives in ace/.

Run:
    py server.py
"""
from ace.server import main

if __name__ == "__main__":
    main()
