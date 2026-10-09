"""Ask the match: a fan-facing Agent Framework agent that answers questions through our MCP server.

The agent has no data of its own. Every fact comes from the match-data MCP tools (Azure
Functions), which serve the same deterministic metrics the overlays use. Hard caps per answer:
tool calls, model round trips, wall time and output tokens.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("matchlens.ask")

ASK_INSTRUCTIONS = """You answer questions about ONE live synthetic football match for a fan.
Use the MatchLens tools to look things up; never guess. Use only numbers the tools return.
The league, clubs and players are fictional. Never discuss betting or odds.
Answer in at most 3 short sentences, in the requested language. Keep club and player names as given.
If the tools don't have the answer, say so briefly."""

LANG_NAMES = {"en": "English", "es": "Spanish", "ja": "Japanese"}


class AskTheMatch:
    def __init__(self, project_endpoint: str, deployment: str, credential, mcp_url: str, mcp_key: str):
        from agent_framework import Agent
        from agent_framework.foundry import FoundryChatClient

        self.mcp_url, self.mcp_key = mcp_url, mcp_key
        client = FoundryChatClient(
            project_endpoint=project_endpoint, model=deployment, credential=credential,
            function_invocation_configuration={"max_iterations": 4, "max_function_calls": 6, "max_duration_seconds": 30},
        )
        self.agent = Agent(client, instructions=ASK_INSTRUCTIONS, name="ask-the-match")
        self.deployment = deployment

    async def ask(self, match_id: str, question: str, lang: str = "en") -> dict:
        from agent_framework import MCPStreamableHTTPTool

        prompt = (f"Match id: {match_id}\nAnswer in {LANG_NAMES.get(lang, 'English')}.\n"
                  f"Question: {question.strip()[:300]}")
        tools_used: list[str] = []
        async with MCPStreamableHTTPTool(
            name="matchlens", url=self.mcp_url, static_headers={"x-functions-key": self.mcp_key},
            allowed_tools=["get_match_state", "get_moments", "get_overlays"],
        ) as mcp:
            resp = await asyncio.wait_for(self.agent.run(prompt, tools=mcp, options={"max_tokens": 600}), timeout=45)
        for msg in resp.messages:
            for c in getattr(msg, "contents", []) or []:
                name = getattr(c, "name", None)
                if getattr(c, "type", "") == "function_call" and name:
                    tools_used.append(name)
        return {"answer": resp.text, "tools": tools_used, "model": self.deployment}
