from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from services.cfd_ai_service import CfdAiService
from services.collection_selection_service import CollectionSelectionService
from services.execution_planner_service import ExecutionPlannerService
from services.repository_service import RepositoryService
from services.rocketraccoon_search_service import RocketRaccoonSearchService
from agentic_workflow.nodes import AgenticDiscoveryNodes
from agentic_workflow.state import AgenticDiscoveryState


def create_agentic_discovery_graph(
    repository_service: RepositoryService,
    cfd_ai_service: CfdAiService,
    rocketraccoon_search_service: RocketRaccoonSearchService,
    collection_selection_service: CollectionSelectionService,
    execution_planner_service: ExecutionPlannerService,
):
    nodes = AgenticDiscoveryNodes(
        repository_service=repository_service,
        cfd_ai_service=cfd_ai_service,
        rocketraccoon_search_service=rocketraccoon_search_service,
        collection_selection_service=collection_selection_service,
        execution_planner_service=execution_planner_service,
    )

    builder = StateGraph(AgenticDiscoveryState)
    builder.add_node(
        "load_repository_context",
        nodes.load_repository_context,
    )
    builder.add_node("analyze_defect", nodes.analyze_defect)
    builder.add_node(
        "discover_regression_collections",
        nodes.discover_regression_collections,
    )
    builder.add_node(
        "select_collection",
        nodes.select_collection,
    )
    builder.add_node(
        "create_execution_plan",
        nodes.create_execution_plan,
    )

    builder.add_edge(START, "load_repository_context")
    builder.add_edge(
        "load_repository_context",
        "analyze_defect",
    )
    builder.add_edge(
        "analyze_defect",
        "discover_regression_collections",
    )
    builder.add_edge(
        "discover_regression_collections",
        "select_collection",
    )
    builder.add_edge(
        "select_collection",
        "create_execution_plan",
    )
    builder.add_edge("create_execution_plan", END)

    return builder.compile()
