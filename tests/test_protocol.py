import asyncio
from dataclasses import dataclass, field

import httpx
import pytest
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import (
    CancelTaskRequest,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    TaskState,
)

from a2a_local.backends import AGENTS, Backend, BackendError
from a2a_local.coordinator import DiscussionError, ask_agent, discuss
from a2a_local.runtime import local_agents

TOKEN = "test-only-local-bearer-token"


@dataclass
class RecordingBackend:
    answer: str
    prompts: list[str] = field(default_factory=list)

    async def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answer


def test_real_http_discovery_and_three_agent_artifact_exchange() -> None:
    async def scenario() -> None:
        agents = {
            "antigravity": RecordingBackend("Proposal: hold a learning session."),
            "qoder": RecordingBackend("Critique: add an owner."),
            "copilot": RecordingBackend("Synthesis: a facilitator runs the session."),
        }
        async with local_agents(agents, TOKEN) as endpoints:
            turns = await discuss("Improve team learning.", endpoints, token=TOKEN)
            assert [turn.agent for turn in turns] == list(AGENTS)
            assert len({turn.task_id for turn in turns}) == 3
            assert "Proposal: hold" in agents["qoder"].prompts[0]
            assert "Proposal: hold" in agents["copilot"].prompts[0]
            assert "Critique: add" in agents["copilot"].prompts[0]
            assert turns[-1].text == "Synthesis: a facilitator runs the session."
            endpoint = endpoints["antigravity"]
            async with httpx.AsyncClient(trust_env=False) as http:
                response = await http.get(f"{endpoint}/.well-known/agent-card.json")
                assert response.status_code == 401
        async with httpx.AsyncClient(trust_env=False) as http:
            with pytest.raises(httpx.ConnectError):
                await http.get(f"{endpoint}/.well-known/agent-card.json")

    asyncio.run(scenario())


class FailingBackend:
    async def run(self, prompt: str) -> str:
        raise BackendError("Synthetic backend failure")


def test_failed_a2a_task_stops_workflow_instead_of_falling_back() -> None:
    async def scenario() -> None:
        qoder = RecordingBackend("Should not run")
        agents: dict[str, Backend] = {
            "antigravity": FailingBackend(),
            "qoder": qoder,
            "copilot": RecordingBackend("Should not run"),
        }
        async with local_agents(agents, TOKEN) as endpoints:
            with pytest.raises(DiscussionError, match="Synthetic backend failure"):
                await discuss("A topic.", endpoints, token=TOKEN)
        assert qoder.prompts == []

    asyncio.run(scenario())


def test_agent_card_identity_must_match_expected_agent() -> None:
    async def scenario() -> None:
        agents = {name: RecordingBackend("Answer") for name in AGENTS}
        async with local_agents(agents, TOKEN) as endpoints:
            with pytest.raises(DiscussionError, match="Expected copilot Agent Card"):
                await ask_agent(
                    "copilot",
                    endpoints["antigravity"],
                    "A task",
                    token=TOKEN,
                    timeout=5,
                )

    asyncio.run(scenario())


def test_nonloopback_endpoint_is_rejected_before_network_access() -> None:
    with pytest.raises(DiscussionError, match="127.0.0.1"):
        asyncio.run(
            ask_agent(
                "copilot", "https://example.com", "A task", token=TOKEN, timeout=5
            )
        )


class BlockingBackend:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def run(self, prompt: str) -> str:
        self.started.set()
        try:
            await asyncio.Event().wait()
            return "unreachable"
        finally:
            self.stopped.set()


def test_a2a_cancel_stops_the_running_backend() -> None:
    async def scenario() -> None:
        blocking = BlockingBackend()
        agents: dict[str, Backend] = {
            "antigravity": blocking,
            "qoder": RecordingBackend("unused"),
            "copilot": RecordingBackend("unused"),
        }
        async with local_agents(agents, TOKEN) as endpoints:
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {TOKEN}"},
                timeout=5,
                trust_env=False,
            ) as http:
                card = await A2ACardResolver(
                    http, endpoints["antigravity"]
                ).get_agent_card()
                client = await create_client(
                    card, ClientConfig(streaming=False, httpx_client=http)
                )
                try:
                    request = SendMessageRequest(
                        message=new_text_message("A slow task", role=Role.ROLE_USER),
                        configuration=SendMessageConfiguration(return_immediately=True),
                    )
                    task_id = ""
                    async for event in client.send_message(request):
                        if event.WhichOneof("payload") == "task":
                            task_id = event.task.id
                    assert task_id
                    await asyncio.wait_for(blocking.started.wait(), timeout=5)
                    result = await client.cancel_task(CancelTaskRequest(id=task_id))
                    assert result.status.state == TaskState.TASK_STATE_CANCELED
                    assert blocking.stopped.is_set()
                finally:
                    await client.close()

    asyncio.run(scenario())
