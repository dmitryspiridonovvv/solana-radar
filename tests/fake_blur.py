"""A local stand-in for wss://ws.solami.dev/data/subscribe used by the tests."""

import asyncio
import json
from urllib.parse import parse_qs, urlparse

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response


class FakeBlur:
    def __init__(self, valid_key="good-key"):
        self.valid_key = valid_key
        self.connections = []  # list of (query dict, websocket)
        self.received = []  # text frames sent by clients
        self.queue = asyncio.Queue()
        self.server = None

    @property
    def url(self):
        host, port = self.server.sockets[0].getsockname()[:2]
        return f"ws://{host}:{port}/data/subscribe"

    async def __aenter__(self):
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0, process_request=self._check_key)
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    def _check_key(self, connection, request):
        query = parse_qs(urlparse(request.path).query)
        if query.get("api_key", [""])[0] != self.valid_key:
            return Response(403, "Forbidden", Headers(), b"missing required permission: DataApi")
        return None

    async def _handler(self, ws):
        query = {k: v[0] for k, v in parse_qs(urlparse(ws.request.path).query).items()}
        self.connections.append((query, ws))
        try:
            async for message in ws:
                self.received.append(message)
        except websockets.ConnectionClosed:
            pass

    async def wait_connections(self, n, timeout=5):
        async def poll():
            while len([c for c in self.connections if c[1].state.name == "OPEN"]) < n:
                await asyncio.sleep(0.02)
        await asyncio.wait_for(poll(), timeout)

    async def broadcast(self, *events, raw=None):
        for _, ws in self.connections:
            if ws.state.name != "OPEN":
                continue
            if raw is not None:
                await ws.send(raw)
            for event in events:
                await ws.send(json.dumps(event))

    async def send_to(self, stream_type, *events):
        for query, ws in self.connections:
            if ws.state.name == "OPEN" and stream_type in query.get("type", ""):
                for event in events:
                    await ws.send(json.dumps(event))

    async def drop_all(self, code=1011):
        for _, ws in self.connections:
            if ws.state.name == "OPEN":
                await ws.close(code=code)
