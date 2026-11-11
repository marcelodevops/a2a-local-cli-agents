import asyncio
import hmac
import logging

from a2a.helpers import (
    new_task_from_user_message,
    new_text_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from a2a_local.backends import AGENTS, MAX_OUTPUT, MAX_PROMPT, Backend, BackendError

logger = logging.getLogger(__name__)


class BearerAuth(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, token: str) -> None:
        super().__init__(app)
        self.authorization = f"Bearer {token}"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not hmac.compare_digest(
            request.headers.get("authorization", "").encode(),
            self.authorization.encode(),
        ):
            return JSONResponse(
                {"error": "A local bearer token is required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


class CLIExecutor(AgentExecutor):
    def __init__(self, agent: str, backend: Backend) -> None:
        self.agent = agent
        self.backend = backend
        self.running: dict[str, asyncio.Task[str]] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None:
            raise ValueError("An A2A message is required")
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        prompt = context.get_user_input()
        if not prompt.strip() or len(prompt.encode()) > MAX_PROMPT:
            await updater.reject(
                new_text_message("Text is empty or exceeds input limit")
            )
            return
        await updater.start_work(new_text_message(f"{self.agent} is working"))
        invocation = asyncio.create_task(self.backend.run(prompt))
        self.running[task.id] = invocation
        try:
            text = await invocation
            if not text.strip() or len(text.encode()) > MAX_OUTPUT:
                raise BackendError("Backend text is empty or exceeds output limit")
            await updater.add_artifact(
                parts=[new_text_part(text=text, media_type="text/plain")],
                name=f"{self.agent}-response",
            )
            await updater.complete()
        except BackendError as error:
            logger.error("%s task failed: %s", self.agent, error)
            await updater.failed(new_text_message(str(error)))
        except asyncio.CancelledError:
            await updater.cancel(new_text_message("CLI invocation canceled"))
        finally:
            self.running.pop(task.id, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.task_id is None:
            raise ValueError("A task ID is required for cancellation")
        invocation = self.running.get(context.task_id)
        if invocation is None:
            raise RuntimeError("No running CLI invocation for this task")
        invocation.cancel()
        await asyncio.gather(invocation, return_exceptions=True)


def create_app(agent: str, backend: Backend, url: str, token: str) -> Starlette:
    if agent not in AGENTS:
        raise ValueError(f"Unknown agent: {agent}")
    if len(token) < 16:
        raise ValueError("Use a bearer token of at least 16 characters")
    card = AgentCard(
        name=agent,
        description=f"Local {agent} CLI discussion agent",
        version="0.1.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")
        ],
        security_schemes={
            "localBearer": SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="bearer")
            )
        },
        security_requirements=[
            SecurityRequirement(schemes={"localBearer": StringList()})
        ],
        skills=[
            AgentSkill(
                id=f"{agent}-discussion",
                name=f"{agent} discussion",
                description="Respond to a text-only discussion without project edits",
                tags=["discussion", "local-cli"],
            )
        ],
    )
    handler = DefaultRequestHandler(
        agent_executor=CLIExecutor(agent, backend),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    routes = [
        *create_agent_card_routes(card),
        *create_jsonrpc_routes(handler, "/"),
    ]
    return Starlette(routes=routes, middleware=[Middleware(BearerAuth, token=token)])
