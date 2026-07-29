"""List all MCP tools for the authenticated user."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from typing import Any

import httpx

TOKEN = sys.argv[1]
SSE_URL = "http://localhost:3001/sse"


async def main() -> None:
    async with (
        httpx.AsyncClient(timeout=30) as client,
        client.stream(
            "GET", SSE_URL, headers={"Authorization": f"Bearer {TOKEN}"}
        ) as response,
    ):
        response.raise_for_status()
        endpoint: str | None = None
        pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        message_queue: asyncio.Queue[str] = asyncio.Queue()

        async def read_sse() -> None:
            nonlocal endpoint
            async for line in response.aiter_lines():
                if line.startswith("data: /messages/"):
                    endpoint = line.replace("data: ", "").strip()
                elif line.startswith("data: "):
                    await message_queue.put(line.replace("data: ", "").strip())

        sse_task = asyncio.create_task(read_sse())

        for _ in range(50):
            if endpoint:
                break
            await asyncio.sleep(0.1)
        if endpoint is None:
            raise RuntimeError("No endpoint event received")

        post_url = f"http://localhost:3001{endpoint}"

        async def rpc(
            method: str, params: dict[str, Any], req_id: int
        ) -> dict[str, Any]:
            fut: asyncio.Future[dict[str, Any]] = (
                asyncio.get_event_loop().create_future()
            )
            pending[req_id] = fut
            payload = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }
            await client.post(
                post_url,
                json=payload,
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            return await asyncio.wait_for(fut, timeout=5)

        async def process_messages() -> None:
            while True:
                data = await message_queue.get()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue
                req_id = msg.get("id")
                if req_id in pending:
                    pending.pop(req_id).set_result(msg)

        proc_task = asyncio.create_task(process_messages())

        await rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            },
            1,
        )
        await rpc("notifications/initialized", {}, 0)

        tools = await rpc("tools/list", {}, 2)
        names = sorted(t["name"] for t in tools.get("result", {}).get("tools", []))
        for name in names:
            print(name)

        proc_task.cancel()
        sse_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await proc_task
        with contextlib.suppress(asyncio.CancelledError):
            await sse_task


if __name__ == "__main__":
    asyncio.run(main())
