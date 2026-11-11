import asyncio
import logging
import socket
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import ExitStack, asynccontextmanager, contextmanager

import uvicorn

from a2a_local.backends import AGENTS, Backend
from a2a_local.server import create_app

logger = logging.getLogger(__name__)


class LocalServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        # asyncio.run owns interruption for the three servers in this process.
        yield


@asynccontextmanager
async def local_agents(
    backends: Mapping[str, Backend], token: str
) -> AsyncIterator[dict[str, str]]:
    servers: list[LocalServer] = []
    tasks: list[asyncio.Task[None]] = []
    endpoints: dict[str, str] = {}
    with ExitStack() as stack:
        try:
            for agent in AGENTS:
                sock = stack.enter_context(socket.socket())
                sock.bind(("127.0.0.1", 0))
                sock.listen(128)
                sock.setblocking(False)
                url = f"http://127.0.0.1:{sock.getsockname()[1]}"
                endpoints[agent] = url
                app = create_app(agent, backends[agent], url, token)
                server = LocalServer(
                    uvicorn.Config(
                        app,
                        host="127.0.0.1",
                        log_level="warning",
                        access_log=False,
                        timeout_graceful_shutdown=2,
                    )
                )
                servers.append(server)
                tasks.append(asyncio.create_task(server.serve(sockets=[sock])))
            async with asyncio.timeout(10):
                while not all(server.started for server in servers):
                    for task in tasks:
                        if task.done():
                            task.result()
                            raise RuntimeError("An A2A server stopped during startup")
                    await asyncio.sleep(0.01)
            yield endpoints
        finally:
            for server in servers:
                server.should_exit = True
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=5)
                for task in pending:
                    task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.error("A2A server failed during shutdown: %s", result)
