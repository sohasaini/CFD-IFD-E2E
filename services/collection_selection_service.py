from __future__ import annotations

import json
from typing import Any

from services.cfd_ai_service import CfdAiService


class CollectionSelectionService:
    def __init__(self, cfd_ai_service: CfdAiService) -> None:
        self.cfd_ai_service = cfd_ai_service

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    def _group_candidates(
        self,
        recommendations: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}

        for item in recommendations:
            suite_name = self._clean(item.get("suite_name"))
            collection_name = self._clean(item.get("collection_name"))
            if not collection_name:
                continue

            key = (suite_name.lower(), collection_name.lower())
            group = grouped.setdefault(
                key,
                {
                    "suite_name": suite_name,
                    "suite_path": self._clean(item.get("suite_path")),
                    "collection_name": collection_name,
                    "collection_path": self._clean(item.get("collection_path")),
                    "component": self._clean(item.get("component")),
                    "maximum_score": 0,
                    "matching_tests": [],
                },
            )

            score = int(item.get("score", 0) or 0)
            group["maximum_score"] = max(group["maximum_score"], score)

            test_name = self._clean(item.get("test_name"))
            if test_name and test_name not in group["matching_tests"]:
                group["matching_tests"].append(test_name)

        candidates = []
        lookup = {}

        for index, group in enumerate(
            sorted(
                grouped.values(),
                key=lambda row: (
                    -int(row["maximum_score"]),
                    row["collection_name"].lower(),
                ),
            ),
            start=1,
        ):
            collection_id = f"COL-{index:03d}"
            full_group = dict(group)
            full_group["collection_id"] = collection_id
            full_group["test_count"] = len(full_group["matching_tests"])
            lookup[collection_id] = full_group

            candidates.append(
                {
                    "collection_id": collection_id,
                    "suite_name": full_group["suite_name"],
                    "collection_name": full_group["collection_name"],
                    "component": full_group["component"],
                    "maximum_score": full_group["maximum_score"],
                    "test_count": full_group["test_count"],
                    "matching_tests": full_group["matching_tests"][:20],
                }
            )

        return candidates, lookup

    def _ask_ai(
        self,
        defect_text: str,
        analysis: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = f"""
Select one existing RocketRaccoon Regression collection.

Return strict JSON only.

Defect:
{defect_text}

Defect analysis:
{json.dumps(analysis, ensure_ascii=False)}

Available collections:
{json.dumps(candidates, ensure_ascii=False)}

Required JSON:
{{
  "selected_collection_id": "COL-001",
  "selection_reason": "",
  "confidence": 0
}}

Rules:
1. Select exactly one supplied collection_id.
2. Never invent a collection, suite, or ID.
3. Select based on feature and operation relevance.
4. confidence must be from 0 to 100.
"""

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a senior Cisco Secure Client automation "
                    "architect. Return strict JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        try:
            response = self.cfd_ai_service._create_completion(messages=messages)
        except Exception:
            response = self.cfd_ai_service._create_completion(
                messages=messages,
                force_token_refresh=True,
            )

        if not response.choices or not response.choices[0].message:
            raise RuntimeError("Cisco AI returned an empty collection selection.")

        cleaned = self.cfd_ai_service._strip_json_fence(
            response.choices[0].message.content or ""
        )

        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Cisco AI returned invalid collection JSON: {cleaned[:1000]}"
            ) from exc

        if not isinstance(result, dict):
            raise RuntimeError("Collection selection must be a JSON object.")

        return result

    def select_collection(
        self,
        defect_text: str,
        analysis: dict[str, Any],
        recommendations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        candidates, lookup = self._group_candidates(recommendations)

        if not candidates:
            raise RuntimeError("No regression collections were available.")

        if len(candidates) == 1:
            selected_id = candidates[0]["collection_id"]
            ai_result = {
                "selected_collection_id": selected_id,
                "selection_reason": (
                    "Only one relevant RocketRaccoon Regression "
                    "collection was discovered."
                ),
                "confidence": 100,
            }
            selection_mode = "automatic_single_candidate"
        else:
            ai_result = self._ask_ai(defect_text, analysis, candidates)
            selection_mode = "cisco_ai"

        selected_id = self._clean(
            ai_result.get("selected_collection_id")
        ).upper()

        if selected_id not in lookup:
            raise RuntimeError("AI selected an unknown collection ID.")

        selected = lookup[selected_id]

        try:
            confidence = int(ai_result.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            confidence = 0

        confidence = max(0, min(confidence, 100))

        return {
            "selection_mode": selection_mode,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "selected_collection": {
                "collection_id": selected_id,
                "suite_name": selected["suite_name"],
                "suite_path": selected["suite_path"],
                "collection_name": selected["collection_name"],
                "collection_path": selected["collection_path"],
                "component": selected["component"],
                "matching_tests": selected["matching_tests"],
                "maximum_score": selected["maximum_score"],
                "selection_reason": self._clean(
                    ai_result.get("selection_reason")
                ),
                "confidence": confidence,
            },
            "validation_passed": True,
        }
