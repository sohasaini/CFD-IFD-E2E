from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class ExecutionPlannerService:
    def __init__(self, automation_path: str) -> None:
        self.automation_path = str(automation_path or "").strip()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    def _resolve_path(self, relative_path: str) -> dict[str, Any]:
        cleaned = self._clean(relative_path)

        if not cleaned:
            return {
                "relative_path": "",
                "absolute_path": "",
                "exists": False,
            }

        normalized = cleaned.replace("\\", os.sep).replace("/", os.sep)
        absolute = Path(self.automation_path) / normalized

        return {
            "relative_path": cleaned,
            "absolute_path": str(absolute),
            "exists": absolute.is_file(),
        }

    def create_collection_plan(
        self,
        defect_text: str,
        analysis: dict[str, Any],
        collection_selection: dict[str, Any],
        selected_suite_type: str,
    ) -> dict[str, Any]:
        selected = collection_selection.get("selected_collection") or {}
        collection_name = self._clean(selected.get("collection_name"))

        if not collection_name:
            raise ValueError("Selected regression collection is missing.")

        collection_file = self._resolve_path(
            self._clean(selected.get("collection_path"))
        )

        blockers = []
        if not collection_file["exists"]:
            blockers.append(
                "Selected collection file was not found in C:\\Automation."
            )

        host = "10.196.147.237"

        return {
            "plan_status": "ready" if not blockers else "needs_configuration",
            "ready_for_runner_trigger": not blockers,
            "execution_started": False,
            "execution_scope": "collection",
            "run_complete_regression_suite": False,
            "run_complete_collection": True,
            "defect_text": self._clean(defect_text),
            "component": self._clean(
                analysis.get("repository_component")
            ),
            "feature": self._clean(analysis.get("feature")),
            "platform": "windows",
            "host": host,
            "suite_type": self._clean(selected_suite_type),
            "runner_suite_name": collection_name,
            "selected_collection": {
                "suite_name": self._clean(selected.get("suite_name")),
                "collection_name": collection_name,
                "collection_file": collection_file,
                "matching_tests": selected.get("matching_tests") or [],
                "selection_reason": self._clean(
                    selected.get("selection_reason")
                ),
                "confidence": int(selected.get("confidence", 0) or 0),
            },
            "runner_request_preview": {
                "hosts": [host],
                "suite": collection_name,
                "auto_start": True,
            },
            "validation": {
                "collection_selection_validated": bool(
                    collection_selection.get("validation_passed", False)
                ),
                "collection_file_valid": collection_file["exists"],
                "blockers": blockers,
            },
            "next_stage": "automatic_runner_trigger",
        }
