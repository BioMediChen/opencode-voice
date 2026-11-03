from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


async def main() -> None:
    params = StdioServerParameters(
        command=str(PYTHON),
        args=["-m", "voice_asr.mcp_server"],
        cwd=str(ROOT),
        env={"VOICE_NO_AUTOSTART": "1"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.serverInfo.name, init.serverInfo.version)
            tools = await session.list_tools()
            for tool in tools.tools:
                print(f"- {tool.name}: {(tool.description or '').splitlines()[0]}")


if __name__ == "__main__":
    asyncio.run(main())
