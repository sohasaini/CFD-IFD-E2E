from __future__ import annotations

import re
from typing import Any

from services.index_service import AutomationIndexService


class RocketRaccoonSearchService:
    """
    Searches only RocketRaccoon automation suites.

    Search order:
    1. RocketRaccoon Sanity
    2. RocketRaccoon Regression

    Other suites such as Orion, Phoenix, Negasonic and
    Quicksilver are never returned.
    """

    def __init__(
        self,
        index_service: AutomationIndexService,
    ) -> None:
        self.index_service = index_service

    @staticmethod
    def _normalize(value: Any) -> str:
        text = str(value or "")

        text = re.sub(
            r"([a-z])([A-Z])",
            r"\1 \2",
            text,
        )

        text = text.replace("_", " ")
        text = text.replace("-", " ")

        text = re.sub(
            r"[^a-zA-Z0-9\s]+",
            " ",
            text,
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        )

        return text.strip().lower()

    @classmethod
    def _is_rocketraccoon_sanity(
        cls,
        suite_name: str,
    ) -> bool:
        normalized = cls._normalize(
            suite_name
        )

        return (
            "rocket raccoon" in normalized
            and "sanity" in normalized
        )

    @classmethod
    def _is_rocketraccoon_regression(
        cls,
        suite_name: str,
    ) -> bool:
        normalized = cls._normalize(
            suite_name
        )

        return (
            "rocket raccoon" in normalized
            and "regression" in normalized
        )

    @classmethod
    def _candidate_text(
        cls,
        result: dict[str, Any],
    ) -> str:
        return cls._normalize(
            " ".join(
                [
                    str(
                        result.get(
                            "component",
                            "",
                        )
                        or ""
                    ),
                    str(
                        result.get(
                            "suite_name",
                            "",
                        )
                        or ""
                    ),
                    str(
                        result.get(
                            "collection_name",
                            "",
                        )
                        or ""
                    ),
                    str(
                        result.get(
                            "test_name",
                            "",
                        )
                        or ""
                    ),
                    str(
                        result.get(
                            "description",
                            "",
                        )
                        or ""
                    ),
                    str(
                        result.get(
                            "expected_result",
                            "",
                        )
                        or ""
                    ),
                ]
            )
        )

    @classmethod
    def _calculate_match_score(
        cls,
        result: dict[str, Any],
        analysis: dict[str, Any],
    ) -> tuple[int, list[str]]:
        searchable_text = cls._candidate_text(
            result
        )

        score = int(
            result.get("score", 0)
            or 0
        )

        reasons: list[str] = []

        repository_component = cls._normalize(
            analysis.get(
                "repository_component",
                "",
            )
        )

        result_component = cls._normalize(
            result.get(
                "component",
                "",
            )
        )

        if (
            repository_component
            and repository_component
            == result_component
        ):
            score += 20
            reasons.append(
                "Same component"
            )

        feature = cls._normalize(
            analysis.get(
                "feature",
                "",
            )
        )

        if (
            feature
            and feature in searchable_text
        ):
            score += 20
            reasons.append(
                f"Feature match: {feature}"
            )

        operation = cls._normalize(
            analysis.get(
                "operation",
                "",
            )
        )

        if (
            operation
            and operation in searchable_text
        ):
            score += 15
            reasons.append(
                f"Operation match: {operation}"
            )

        strong_keywords = (
            analysis.get(
                "strong_keywords"
            )
            or []
        )

        for keyword in strong_keywords:
            normalized_keyword = cls._normalize(
                keyword
            )

            if (
                normalized_keyword
                and normalized_keyword
                in searchable_text
            ):
                score += 10
                reasons.append(
                    f"Keyword match: {keyword}"
                )

        failure_signatures = (
            analysis.get(
                "failure_signatures"
            )
            or []
        )

        for signature in failure_signatures:
            normalized_signature = (
                cls._normalize(signature)
            )

            if (
                normalized_signature
                and normalized_signature
                in searchable_text
            ):
                score += 15
                reasons.append(
                    f"Failure match: {signature}"
                )

        score = max(
            0,
            min(score, 100),
        )

        return (
            score,
            list(
                dict.fromkeys(reasons)
            )[:8],
        )

    @staticmethod
    def _canonical_key(
        result: dict[str, Any],
    ) -> tuple[str, str]:
        """
        Used to remove duplicate copies of the same test.
        """
        return (
            str(
                result.get(
                    "collection_name",
                    "",
                )
                or ""
            ).strip().lower(),
            str(
                result.get(
                    "test_name",
                    "",
                )
                or ""
            ).strip().lower(),
        )

    @classmethod
    def _filter_suite_results(
        cls,
        results: list[dict[str, Any]],
        suite_type: str,
    ) -> list[dict[str, Any]]:
        filtered: list[
            dict[str, Any]
        ] = []

        for result in results:
            suite_name = str(
                result.get(
                    "suite_name",
                    "",
                )
                or ""
            )

            if suite_type == "sanity":
                allowed = (
                    cls._is_rocketraccoon_sanity(
                        suite_name
                    )
                )
            else:
                allowed = (
                    cls._is_rocketraccoon_regression(
                        suite_name
                    )
                )

            if allowed:
                filtered.append(result)

        return filtered

    @classmethod
    def _rank_and_deduplicate(
        cls,
        results: list[dict[str, Any]],
        analysis: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        deduplicated: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}

        for result in results:
            score, reasons = (
                cls._calculate_match_score(
                    result=result,
                    analysis=analysis,
                )
            )

            if score <= 0:
                continue

            recommendation = dict(
                result
            )

            recommendation[
                "score"
            ] = score

            recommendation[
                "ai_match_reasons"
            ] = reasons

            key = cls._canonical_key(
                recommendation
            )

            existing = deduplicated.get(
                key
            )

            if (
                existing is None
                or score
                > int(
                    existing.get(
                        "score",
                        0,
                    )
                    or 0
                )
            ):
                deduplicated[key] = (
                    recommendation
                )

        ranked = list(
            deduplicated.values()
        )

        ranked.sort(
            key=lambda item: (
                -int(
                    item.get(
                        "score",
                        0,
                    )
                    or 0
                ),
                str(
                    item.get(
                        "collection_name",
                        "",
                    )
                    or ""
                ).lower(),
                str(
                    item.get(
                        "test_name",
                        "",
                    )
                    or ""
                ).lower(),
            )
        )

        return ranked[:limit]

    def search(
        self,
        analysis: dict[str, Any],
        selected_component: str = "",
        limit: int = 20,
        candidate_limit: int = 1000,
        minimum_score: int = 20,
    ) -> dict[str, Any]:
        """
        Search only RocketRaccoon Regression.

        Sanity suites are intentionally excluded.

        Results are still returned at testcase level because the next
        workflow stage groups them by collection and selects the best
        regression collection for execution.
        """
        search_query = str(
            analysis.get(
                "focused_search_query",
                "",
            )
            or ""
        ).strip()

        if not search_query:
            raise ValueError(
                "Focused search query is missing."
            )

        component = (
            selected_component.strip()
            or str(
                analysis.get(
                    "repository_component",
                    "",
                )
                or ""
            ).strip()
        )

        safe_limit = max(
            1,
            min(int(limit), 100),
        )

        safe_candidate_limit = max(
            100,
            min(
                int(candidate_limit),
                2000,
            ),
        )

        search_result = self.index_service.search(
            query=search_query,
            component=component,
            limit=safe_candidate_limit,
        )

        all_results = (
            search_result.get("results")
            or []
        )

        regression_candidates = (
            self._filter_suite_results(
                results=all_results,
                suite_type="regression",
            )
        )

        ranked_regression = (
            self._rank_and_deduplicate(
                results=regression_candidates,
                analysis=analysis,
                limit=safe_limit,
            )
        )

        suitable_regression = [
            item
            for item in ranked_regression
            if int(
                item.get("score", 0)
                or 0
            ) >= minimum_score
        ]

        collection_names = list(
            dict.fromkeys(
                str(
                    item.get(
                        "collection_name",
                        "",
                    )
                    or ""
                ).strip()
                for item in suitable_regression
                if str(
                    item.get(
                        "collection_name",
                        "",
                    )
                    or ""
                ).strip()
            )
        )

        return {
            "success": bool(
                suitable_regression
            ),
            "selected_suite_type": "regression",
            "fallback_used": False,
            "sanity_excluded": True,
            "execution_scope": "collection",
            "search_query": search_query,
            "component": component,
            "raw_candidate_count": len(
                all_results
            ),
            "suite_candidate_count": len(
                regression_candidates
            ),
            "returned_count": len(
                suitable_regression
            ),
            "candidate_collections": (
                collection_names
            ),
            "recommendations": (
                suitable_regression
            ),
            "message": (
                "RocketRaccoon regression candidates "
                "were found."
                if suitable_regression
                else
                "No suitable RocketRaccoon regression "
                "collection was found."
            ),
        }