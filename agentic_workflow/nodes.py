from __future__ import annotations

from typing import Any

from services.cfd_ai_service import CfdAiService
from services.collection_selection_service import CollectionSelectionService
from services.execution_planner_service import ExecutionPlannerService
from services.repository_service import RepositoryService
from services.rocketraccoon_search_service import RocketRaccoonSearchService
from agentic_workflow.state import AgenticDiscoveryState


class AgenticDiscoveryNodes:
    def __init__(
        self,
        repository_service: RepositoryService,
        cfd_ai_service: CfdAiService,
        rocketraccoon_search_service: RocketRaccoonSearchService,
        collection_selection_service: CollectionSelectionService,
        execution_planner_service: ExecutionPlannerService,
    ) -> None:
        self.repository_service = repository_service
        self.cfd_ai_service = cfd_ai_service
        self.rocketraccoon_search_service = rocketraccoon_search_service
        self.collection_selection_service = collection_selection_service
        self.execution_planner_service = execution_planner_service

    def load_repository_context(
        self,
        state: AgenticDiscoveryState,
    ) -> dict[str, Any]:
        repository = self.repository_service.repository_status()

        if not repository.get("available"):
            raise RuntimeError(
                repository.get(
                    "message",
                    "Automation repository is unavailable.",
                )
            )

        return {
            "repository_components": repository.get("components", []),
            "workflow_status": "repository_context_loaded",
        }

    def analyze_defect(
        self,
        state: AgenticDiscoveryState,
    ) -> dict[str, Any]:
        defect_text = str(state.get("defect_text", "") or "").strip()

        if not defect_text:
            raise ValueError("Defect description is required.")

        analysis = self.cfd_ai_service.analyze(
            defect_text=defect_text,
            selected_component=str(
                state.get("selected_component", "") or ""
            ).strip(),
            repository_components=state.get(
                "repository_components", []
            ),
        )

        return {
            "analysis": analysis,
            "workflow_status": "defect_analysis_completed",
        }

    def discover_regression_collections(
        self,
        state: AgenticDiscoveryState,
    ) -> dict[str, Any]:
        search_result = self.rocketraccoon_search_service.search(
            analysis=state.get("analysis") or {},
            selected_component=str(
                state.get("selected_component", "") or ""
            ).strip(),
            limit=50,
            candidate_limit=1500,
            minimum_score=20,
        )

        recommendations = list(
            search_result.get("recommendations", []) or []
        )

        if not recommendations:
            raise RuntimeError(
                search_result.get(
                    "message",
                    "No matching RocketRaccoon Regression collection found.",
                )
            )

        return {
            "suite_search_result": search_result,
            "selected_suite_type": "regression",
            "fallback_used": False,
            "recommendations": recommendations,
            "workflow_status": (
                "regression_collection_discovery_completed"
            ),
        }

    def select_collection(
        self,
        state: AgenticDiscoveryState,
    ) -> dict[str, Any]:
        selection = self.collection_selection_service.select_collection(
            defect_text=str(
                state.get("defect_text", "") or ""
            ).strip(),
            analysis=state.get("analysis") or {},
            recommendations=list(
                state.get("recommendations", []) or []
            ),
        )

        return {
            "collection_selection": selection,
            "workflow_status": "collection_selection_completed",
        }

    def create_execution_plan(
        self,
        state: AgenticDiscoveryState,
    ) -> dict[str, Any]:
        plan = self.execution_planner_service.create_collection_plan(
            defect_text=str(
                state.get("defect_text", "") or ""
            ).strip(),
            analysis=state.get("analysis") or {},
            collection_selection=state.get(
                "collection_selection", {}
            )
            or {},
            selected_suite_type=str(
                state.get("selected_suite_type", "regression")
                or "regression"
            ).strip(),
        )

        return {
            "execution_plan": plan,
            "workflow_status": (
                "collection_execution_plan_completed"
            ),
        }
