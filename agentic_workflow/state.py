from __future__ import annotations

from typing import Any, TypedDict


class AgenticDiscoveryState(TypedDict, total=False):
    defect_text: str
    selected_component: str
    repository_components: list[str]
    analysis: dict[str, Any]
    suite_search_result: dict[str, Any]
    selected_suite_type: str
    fallback_used: bool
    recommendations: list[dict[str, Any]]
    collection_selection: dict[str, Any]
    execution_plan: dict[str, Any]
    workflow_status: str
