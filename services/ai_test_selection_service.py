from __future__ import annotations

import json
from typing import Any

from services.cfd_ai_service import CfdAiService


class AiTestSelectionService:
    """
    Uses Cisco AI to review discovered RocketRaccoon candidates.

    The AI selects only candidate IDs supplied by the backend.
    The backend then validates every selected ID and reconstructs
    the final test details from the original candidate list.
    """

    def __init__(
        self,
        cfd_ai_service: CfdAiService,
    ) -> None:
        self.cfd_ai_service = cfd_ai_service

    @staticmethod
    def _candidate_id(index: int) -> str:
        return f"C{index + 1}"

    @staticmethod
    def _clean_text(value: Any) -> str:
        return str(value or "").strip()

    def _prepare_candidates(
        self,
        candidates: list[dict[str, Any]],
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, dict[str, Any]],
    ]:
        prompt_candidates: list[dict[str, Any]] = []
        candidate_lookup: dict[str, dict[str, Any]] = {}

        for index, candidate in enumerate(candidates):
            candidate_id = self._candidate_id(index)

            candidate_lookup[candidate_id] = candidate

            prompt_candidates.append(
                {
                    "candidate_id": candidate_id,
                    "suite_name": self._clean_text(
                        candidate.get("suite_name")
                    ),
                    "collection_name": self._clean_text(
                        candidate.get("collection_name")
                    ),
                    "test_name": self._clean_text(
                        candidate.get("test_name")
                    ),
                    "description": self._clean_text(
                        candidate.get("description")
                    )[:4000],
                    "score": int(
                        candidate.get("score", 0) or 0
                    ),
                }
            )

        return prompt_candidates, candidate_lookup

    def _request_selection(
        self,
        defect_text: str,
        analysis: dict[str, Any],
        prompt_candidates: list[dict[str, Any]],
        maximum_tests: int,
    ) -> dict[str, Any]:
        prompt = f"""
You are selecting existing automated test cases for a software
defect. Review technical meaning, not only matching words.

Return only valid JSON. Do not return markdown.

Defect:
{defect_text}

Defect analysis:
{json.dumps(analysis, ensure_ascii=False)}

Existing RocketRaccoon candidates:
{json.dumps(prompt_candidates, ensure_ascii=False)}

Required JSON:
{{
  "selection_summary": "",
  "selected": [
    {{
      "candidate_id": "C1",
      "priority": 1,
      "selection_reason": "",
      "coverage_type": "primary"
    }}
  ],
  "rejected": [
    {{
      "candidate_id": "C2",
      "rejection_reason": ""
    }}
  ]
}}

Rules:
1. Select only candidate_id values from the supplied candidates.
2. Never invent a suite, collection, testcase or candidate ID.
3. Select only tests that directly validate the defect or provide
   necessary covering scenarios.
4. Reject candidates that match only broad words but validate a
   different behavior, such as licensing, unrelated UI state or an
   unrelated module.
5. Select between 1 and {maximum_tests} tests.
6. Use coverage_type "primary" for the closest testcase and
   "covering" for useful additional scenarios.
7. Priority must start at 1 and increase.
8. Give a clear technical reason for every selection and rejection.
"""

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a senior automation test architect. "
                    "Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        try:
            response = (
                self.cfd_ai_service._create_completion(
                    messages=messages
                )
            )
        except Exception:
            response = (
                self.cfd_ai_service._create_completion(
                    messages=messages,
                    force_token_refresh=True,
                )
            )

        if (
            not response.choices
            or not response.choices[0].message
        ):
            raise RuntimeError(
                "Cisco AI returned an empty testcase selection."
            )

        raw_content = (
            response.choices[0].message.content
            or ""
        )

        cleaned = (
            self.cfd_ai_service._strip_json_fence(
                raw_content
            )
        )

        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Cisco AI returned invalid testcase-selection JSON: "
                f"{cleaned[:1000]}"
            ) from exc

        if not isinstance(result, dict):
            raise RuntimeError(
                "AI testcase selection must be a JSON object."
            )

        return result

    def select_tests(
        self,
        defect_text: str,
        analysis: dict[str, Any],
        candidates: list[dict[str, Any]],
        maximum_tests: int = 3,
    ) -> dict[str, Any]:
        if not candidates:
            raise ValueError(
                "No testcase candidates were provided."
            )

        safe_maximum = max(
            1,
            min(int(maximum_tests), 5),
        )

        (
            prompt_candidates,
            candidate_lookup,
        ) = self._prepare_candidates(candidates)

        ai_result = self._request_selection(
            defect_text=defect_text,
            analysis=analysis,
            prompt_candidates=prompt_candidates,
            maximum_tests=safe_maximum,
        )

        raw_selected = ai_result.get("selected") or []
        raw_rejected = ai_result.get("rejected") or []

        if not isinstance(raw_selected, list):
            raw_selected = []

        if not isinstance(raw_rejected, list):
            raw_rejected = []

        selected_tests: list[dict[str, Any]] = []
        used_ids: set[str] = set()
        invalid_ids: list[str] = []

        for item in raw_selected:
            if not isinstance(item, dict):
                continue

            candidate_id = self._clean_text(
                item.get("candidate_id")
            ).upper()

            if (
                not candidate_id
                or candidate_id not in candidate_lookup
            ):
                if candidate_id:
                    invalid_ids.append(candidate_id)
                continue

            if candidate_id in used_ids:
                continue

            original = candidate_lookup[candidate_id]
            used_ids.add(candidate_id)

            selected_tests.append(
                {
                    "candidate_id": candidate_id,
                    "priority": len(selected_tests) + 1,
                    "coverage_type": (
                        self._clean_text(
                            item.get("coverage_type")
                        )
                        or (
                            "primary"
                            if not selected_tests
                            else "covering"
                        )
                    ),
                    "selection_reason": self._clean_text(
                        item.get("selection_reason")
                    ),
                    "suite_name": self._clean_text(
                        original.get("suite_name")
                    ),
                    "suite_path": self._clean_text(
                        original.get("suite_path")
                    ),
                    "collection_name": self._clean_text(
                        original.get("collection_name")
                    ),
                    "collection_path": self._clean_text(
                        original.get("collection_path")
                    ),
                    "test_name": self._clean_text(
                        original.get("test_name")
                    ),
                    "description": self._clean_text(
                        original.get("description")
                    ),
                    "score": int(
                        original.get("score", 0) or 0
                    ),
                }
            )

            if len(selected_tests) >= safe_maximum:
                break

        if not selected_tests:
            raise RuntimeError(
                "AI did not select any valid existing testcase."
            )

        rejected_tests: list[dict[str, Any]] = []
        rejected_ids: set[str] = set()

        for item in raw_rejected:
            if not isinstance(item, dict):
                continue

            candidate_id = self._clean_text(
                item.get("candidate_id")
            ).upper()

            if (
                candidate_id not in candidate_lookup
                or candidate_id in used_ids
                or candidate_id in rejected_ids
            ):
                continue

            original = candidate_lookup[candidate_id]
            rejected_ids.add(candidate_id)

            rejected_tests.append(
                {
                    "candidate_id": candidate_id,
                    "test_name": self._clean_text(
                        original.get("test_name")
                    ),
                    "collection_name": self._clean_text(
                        original.get("collection_name")
                    ),
                    "suite_name": self._clean_text(
                        original.get("suite_name")
                    ),
                    "rejection_reason": self._clean_text(
                        item.get("rejection_reason")
                    ),
                }
            )

        # Include candidates omitted by AI as unselected candidates.
        for candidate_id, original in candidate_lookup.items():
            if (
                candidate_id in used_ids
                or candidate_id in rejected_ids
            ):
                continue

            rejected_tests.append(
                {
                    "candidate_id": candidate_id,
                    "test_name": self._clean_text(
                        original.get("test_name")
                    ),
                    "collection_name": self._clean_text(
                        original.get("collection_name")
                    ),
                    "suite_name": self._clean_text(
                        original.get("suite_name")
                    ),
                    "rejection_reason": (
                        "Not selected by AI for the final "
                        "execution plan."
                    ),
                }
            )

        return {
            "selection_summary": self._clean_text(
                ai_result.get("selection_summary")
            ),
            "candidate_count": len(candidates),
            "selected_count": len(selected_tests),
            "rejected_count": len(rejected_tests),
            "selected_tests": selected_tests,
            "rejected_tests": rejected_tests,
            "invalid_ai_candidate_ids": list(
                dict.fromkeys(invalid_ids)
            ),
            "validation_passed": not invalid_ids,
        }
