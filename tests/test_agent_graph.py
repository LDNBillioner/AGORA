"""
Tests for agent.py graph wiring — structural checks only (no live LLM calls).
"""

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

import agent
from agent import AgentState, should_continue, process_tool_results


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    # Instantiating ChatGoogleGenerativeAI validates that an API key exists.
    # Structural tests below never make live calls, so a dummy value is fine.
    monkeypatch.setenv("GOOGLE_API_KEY", "test-dummy-key")


class TestGraphStructure:
    def test_entry_point_is_agent(self):
        # langgraph 0.1.x exposes the entry point only via the compiled graph
        graph = agent.workflow.compile()
        source_nodes = {
            edge.target for edge in graph.get_graph().edges
            if edge.source == "__start__"
        }
        assert "agent" in source_nodes

    def test_nodes_registered(self):
        nodes = agent.workflow.nodes
        assert set(nodes) >= {"agent", "action", "process_result"}

    def test_tools_bound_to_model(self):
        model = agent.get_model()
        # The Gemini LLM must have the 4 agent tools bound
        assert len(agent.agent_tools) == 4


class TestRouter:
    def test_continues_on_tool_calls(self):
        class FakeMsg:
            tool_calls = [{"name": "record_transaction", "args": {}}]

        state = {"messages": [FakeMsg()]}
        assert should_continue(state) == "continue"

    def test_ends_without_tool_calls(self):
        state = {"messages": [HumanMessage(content="halo")]}
        assert should_continue(state) == "end"


class TestProcessToolResults:
    def test_clarification_detected(self):
        state = {
            "messages": [
                HumanMessage(content="bayar gaji"),
                ToolMessage(
                    content="CLARIFICATION_NEEDED: Berapa nominal gajinya, Kak?",
                    tool_call_id="t1",
                ),
            ]
        }
        result = process_tool_results(state)
        assert result["requires_clarification"] is True
        assert "Berapa nominal gajinya" in result["final_response"]

    def test_success_detected(self):
        state = {
            "messages": [
                ToolMessage(
                    content="SUCCESS: Transaksi berhasil dicatat! ID: abc",
                    tool_call_id="t2",
                ),
            ]
        }
        result = process_tool_results(state)
        assert result["requires_clarification"] is False
        assert "berhasil" in result["final_response"].lower()

    def test_error_detected(self):
        state = {
            "messages": [
                ToolMessage(
                    content="ERROR: database unavailable",
                    tool_call_id="t3",
                ),
            ]
        }
        result = process_tool_results(state)
        assert "masalah" in result["final_response"]
