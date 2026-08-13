from __future__ import annotations

import re
from typing import Any

from services.index_service import (
    AutomationIndexService,
)


class RecommendationService:
    """
    Converts AI defect analysis into ranked automation recommendations.

    Suite preference order for duplicate test cases:

    1. RocketRaccoon Sanity
    2. RocketRaccoon Regression
    3. Other RocketRaccoon suites
    4. Quicksilver suites
    5. Other suites

    Technical relevance remains the main score. Suite preference is
    used to select the preferred executable suite when the same test
    exists in multiple suites.
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
    def _contains_phrase(
        cls,
        searchable_text: str,
        phrase: str,
    ) -> bool:
        normalized_phrase = cls._normalize(
            phrase
        )

        if not normalized_phrase:
            return False

        return normalized_phrase in searchable_text

    @classmethod
    def _suite_priority(
        cls,
        result: dict[str, Any],
    ) -> int:
        """
        Return the execution preference for a suite.

        Higher value means higher preference.
        """
        suite_name = str(
            result.get("suite_name", "")
            or ""
        ).strip().lower()

        if (
            "rocketraccoon" in suite_name
            and "sanity" in suite_name
        ):
            return 500

        if (
            "rocketraccoon" in suite_name
            and "regression" in suite_name
        ):
            return 400

        if "rocketraccoon" in suite_name:
            return 300

        if "quicksilver" in suite_name:
            return 200

        return 100

    @classmethod
    def _suite_preference_reason(
        cls,
        result: dict[str, Any],
    ) -> str:
        suite_name = str(
            result.get("suite_name", "")
            or ""
        ).strip().lower()

        if (
            "rocketraccoon" in suite_name
            and "sanity" in suite_name
        ):
            return (
                "Selected from preferred "
                "RocketRaccoon sanity suite"
            )

        if (
            "rocketraccoon" in suite_name
            and "regression" in suite_name
        ):
            return (
                "RocketRaccoon sanity was unavailable; "
                "selected RocketRaccoon regression suite"
            )

        if "rocketraccoon" in suite_name:
            return "Selected from a RocketRaccoon suite"

        if "quicksilver" in suite_name:
            return (
                "RocketRaccoon suite was unavailable; "
                "selected Quicksilver suite"
            )

        return (
            "RocketRaccoon and Quicksilver suites were "
            "unavailable; selected another available suite"
        )

    @classmethod
    def _sort_available_suites(
        cls,
        suite_names: list[str],
    ) -> list[str]:
        """
        Sort available suites using the configured suite preference.
        """
        unique_suites = list(
            dict.fromkeys(
                suite
                for suite in suite_names
                if str(suite or "").strip()
            )
        )

        return sorted(
            unique_suites,
            key=lambda suite: (
                -cls._suite_priority(
                    {"suite_name": suite}
                ),
                suite.lower(),
            ),
        )

    @classmethod
    def _adjust_score(
        cls,
        result: dict[str, Any],
        analysis: dict[str, Any],
    ) -> tuple[int, list[str]]:
        """
        Calculate technical relevance.

        Suite preference is intentionally not added to this score,
        because a preferred suite should not make an unrelated test
        appear technically relevant.
        """
        base_score = int(
            result.get("score", 0)
            or 0
        )

        combined_text = cls._normalize(
            " ".join(
                [
                    str(result.get("suite_name", "") or ""),
                    str(result.get("component", "") or ""),
                    str(result.get("collection_name", "") or ""),
                    str(result.get("test_name", "") or ""),
                    str(result.get("description", "") or ""),
                ]
            )
        )

        score = round(base_score * 0.45)
        reasons: list[str] = []

        repository_component = str(
            analysis.get("repository_component", "")
            or ""
        ).strip()

        result_component = str(
            result.get("component", "")
            or ""
        ).strip()

        if (
            repository_component
            and repository_component.lower()
            == result_component.lower()
        ):
            score += 18
            reasons.append("Same repository component")

        feature = str(
            analysis.get("feature", "")
            or ""
        ).strip()

        if (
            feature
            and cls._contains_phrase(
                combined_text,
                feature,
            )
        ):
            score += 20
            reasons.append(f"Same feature: {feature}")

        operation = str(
            analysis.get("operation", "")
            or ""
        ).strip()

        if (
            operation
            and cls._contains_phrase(
                combined_text,
                operation,
            )
        ):
            score += 15
            reasons.append(f"Same operation: {operation}")

        strong_keywords = analysis.get("strong_keywords") or []
        failure_signatures = analysis.get("failure_signatures") or []
        technologies = analysis.get("technology") or []

        for keyword in strong_keywords:
            if cls._contains_phrase(
                combined_text,
                str(keyword),
            ):
                score += 10
                reasons.append(f"Technical match: {keyword}")

        for signature in failure_signatures:
            if cls._contains_phrase(
                combined_text,
                str(signature),
            ):
                score += 16
                reasons.append(f"Failure match: {signature}")

        for technology in technologies:
            if cls._contains_phrase(
                combined_text,
                str(technology),
            ):
                score += 12
                reasons.append(f"Technology match: {technology}")

        excluded = analysis.get("exclude_keywords") or []

        for excluded_keyword in excluded:
            if cls._contains_phrase(
                combined_text,
                str(excluded_keyword),
            ):
                score -= 18
                reasons.append(
                    "Possible unrelated area: "
                    f"{excluded_keyword}"
                )

        score = max(0, min(score, 100))

        return (
            score,
            list(dict.fromkeys(reasons))[:8],
        )

    @staticmethod
    def _canonical_key(
        result: dict[str, Any],
    ) -> tuple[str, str]:
        """
        The same collection/test can be included in many suites.

        Deduplicate using the collection implementation path and
        test method name. If collection_path is unavailable,
        collection_name is used as the fallback.
        """
        # Use collection name instead of collection path because the same
        # collection may be copied or referenced through different suite paths.
        # This groups the same test across Negasonic, Orion, Phoenix,
        # Quicksilver and RocketRaccoon suites.
        collection_identity = result.get(
            "collection_name",
            "",
        )

        return (
            str(collection_identity).strip().lower(),
            str(
                result.get("test_name", "")
                or ""
            ).strip().lower(),
        )

    @classmethod
    def _should_replace_existing(
        cls,
        existing: dict[str, Any],
        candidate: dict[str, Any],
        candidate_score: int,
    ) -> bool:
        """
        Decide which suite copy should represent a duplicate test.

        Preference:
        1. Better suite priority
        2. Higher technical score when suite priority is equal
        """
        existing_priority = cls._suite_priority(existing)
        candidate_priority = cls._suite_priority(candidate)

        if candidate_priority > existing_priority:
            return True

        if candidate_priority < existing_priority:
            return False

        existing_score = int(
            existing.get("score", 0)
            or 0
        )

        return candidate_score > existing_score

    def recommend(
        self,
        analysis: dict[str, Any],
        selected_component: str = "",
        limit: int = 10,
        candidate_limit: int = 100,
    ) -> dict[str, Any]:
        search_query = str(
            analysis.get("focused_search_query", "")
            or ""
        ).strip()

        if not search_query:
            raise ValueError(
                "AI did not generate a focused search query."
            )

        component = (
            selected_component.strip()
            or str(
                analysis.get("repository_component", "")
                or ""
            ).strip()
        )

        # Search a larger candidate pool so RocketRaccoon entries are not
        # excluded when many duplicate release-suite results score highly.
        effective_candidate_limit = max(
            int(candidate_limit),
            500,
        )

        search_result = self.index_service.search(
            query=search_query,
            component=component,
            limit=effective_candidate_limit,
        )

        deduplicated: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}

        for result in search_result.get("results") or []:
            adjusted_score, reasons = self._adjust_score(
                result=result,
                analysis=analysis,
            )

            if adjusted_score <= 0:
                continue

            key = self._canonical_key(result)
            existing = deduplicated.get(key)

            suite_name = str(
                result.get("suite_name", "")
                or ""
            ).strip()

            if existing is None:
                recommendation = dict(result)
                recommendation["original_keyword_score"] = (
                    result.get("score", 0)
                )
                recommendation["score"] = adjusted_score
                recommendation["ai_match_reasons"] = list(reasons)
                recommendation["available_suites"] = (
                    [suite_name] if suite_name else []
                )
                recommendation["suite_priority"] = (
                    self._suite_priority(recommendation)
                )
                recommendation["suite_selection_reason"] = (
                    self._suite_preference_reason(recommendation)
                )

                deduplicated[key] = recommendation
                continue

            available_suites = list(
                existing.get("available_suites", [])
                or []
            )

            if (
                suite_name
                and suite_name not in available_suites
            ):
                available_suites.append(suite_name)

            existing["available_suites"] = (
                self._sort_available_suites(
                    available_suites
                )
            )

            if self._should_replace_existing(
                existing=existing,
                candidate=result,
                candidate_score=adjusted_score,
            ):
                existing["score"] = adjusted_score
                existing["original_keyword_score"] = (
                    result.get("score", 0)
                )
                existing["ai_match_reasons"] = list(reasons)
                existing["suite_name"] = suite_name
                existing["suite_path"] = result.get(
                    "suite_path",
                    "",
                )
                existing["suite_priority"] = (
                    self._suite_priority(result)
                )
                existing["suite_selection_reason"] = (
                    self._suite_preference_reason(result)
                )

        recommendations = list(deduplicated.values())

        for recommendation in recommendations:
            recommendation["available_suites"] = (
                self._sort_available_suites(
                    list(
                        recommendation.get(
                            "available_suites",
                            [],
                        )
                        or []
                    )
                )
            )

            recommendation["suite_priority"] = (
                self._suite_priority(recommendation)
            )

            recommendation["suite_selection_reason"] = (
                self._suite_preference_reason(recommendation)
            )

            match_reasons = list(
                recommendation.get(
                    "ai_match_reasons",
                    [],
                )
                or []
            )

            suite_reason = recommendation[
                "suite_selection_reason"
            ]

            if (
                suite_reason
                and suite_reason not in match_reasons
            ):
                match_reasons.append(suite_reason)

            recommendation["ai_match_reasons"] = (
                match_reasons[:9]
            )

        # User requirement: show RocketRaccoon Sanity first, then
        # RocketRaccoon Regression. Technical score orders tests within
        # the same suite-preference group.
        recommendations.sort(
            key=lambda item: (
                -self._suite_priority(item),
                -int(item.get("score", 0) or 0),
                str(
                    item.get("collection_name", "")
                    or ""
                ).lower(),
                str(
                    item.get("test_name", "")
                    or ""
                ).lower(),
            )
        )

        try:
            requested_limit = int(limit)
        except (TypeError, ValueError):
            requested_limit = 10

        safe_limit = max(
            1,
            min(requested_limit, 100),
        )

        final_recommendations = recommendations[:safe_limit]

        preferred_sanity_count = sum(
            1
            for item in final_recommendations
            if self._suite_priority(item) == 500
        )

        regression_fallback_count = sum(
            1
            for item in final_recommendations
            if self._suite_priority(item) == 400
        )

        return {
            "search_query": search_query,
            "component": component,
            "suite_preference": [
                "RocketRaccoon Sanity",
                "RocketRaccoon Regression",
                "Other RocketRaccoon suites",
                "Quicksilver suites",
                "Other suites",
            ],
            "raw_candidate_count": search_result.get(
                "total_matches",
                0,
            ),
            "deduplicated_count": len(recommendations),
            "returned_count": len(final_recommendations),
            "preferred_sanity_count": preferred_sanity_count,
            "regression_fallback_count": (
                regression_fallback_count
            ),
            "recommendations": final_recommendations,
        }