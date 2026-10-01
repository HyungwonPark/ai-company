"""Pure LangGraph stage transitions; all external effects remain in the ledger."""

from typing import TypedDict
from langgraph.graph import StateGraph, START, END


class Progress(TypedDict, total=False):
    stage: str
    verdict: str
    checks_passed: bool
    repairs: int
    max_repairs: int
    next_stage: str
    status: str


def build_graph(checkpointer):
    graph = StateGraph(Progress)

    def pm(state):
        return {"next_stage": "developer", "status": "READY"} if state["verdict"] == "PASS" else {
            "next_stage": "pm", "status": "BLOCKED"}

    def developer(state):
        return {"next_stage": "check", "status": "READY"}

    def repair(state):
        if state["repairs"] >= state["max_repairs"]:
            return {"next_stage": "developer", "status": "STOPPED"}
        return {"next_stage": "developer", "status": "READY", "repairs": state["repairs"] + 1}

    def check(state):
        return {"next_stage": "reviewer", "status": "READY"} if state["checks_passed"] else repair(state)

    def reviewer(state):
        if state["verdict"] == "REVISE":
            return repair(state)
        return {"next_stage": "final", "status": "READY" if state["verdict"] == "PASS" else "BLOCKED"}

    def final(state):
        if state["verdict"] == "REVISE":
            return repair(state)
        return {"next_stage": "gate", "status": "READY" if state["verdict"] == "PASS" else "BLOCKED"}

    for name, node in (("pm", pm), ("developer", developer), ("check", check),
                       ("reviewer", reviewer), ("final", final)):
        graph.add_node(name, node)
        graph.add_edge(name, END)
    graph.add_conditional_edges(START, lambda state: state["stage"])
    return graph.compile(checkpointer=checkpointer)
