from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from services.repository_service import RepositoryService
from services.suite_parser_service import SuiteParserService


class AutomationIndexService:
    """
    Builds and searches a local, read-only automation test index.

    The source automation repository is never modified.
    Only the generated JSON file inside E2E-AI/data is written.
    """

    def __init__(
        self,
        repository_service: RepositoryService,
        suite_parser_service: SuiteParserService,
        index_file: str | Path,
    ) -> None:
        self.repository_service = repository_service
        self.suite_parser_service = suite_parser_service

        self.index_file = Path(index_file).resolve()
        self.index_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._lock = threading.Lock()

        self._status: dict[str, Any] = {
            "state": "not_created",
            "message": "Automation index has not been created.",
            "started_at": "",
            "completed_at": "",
            "current_suite": "",
            "processed_suites": 0,
            "total_suites": 0,
            "successful_suites": 0,
            "failed_suites": 0,
            "collection_count": 0,
            "test_count": 0,
            "component": "",
            "errors": [],
        }

    # ---------------------------------------------------------
    # Status helpers
    # ---------------------------------------------------------
    def _update_status(self, **values: Any) -> None:
        with self._lock:
            self._status.update(values)

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            status = dict(self._status)

        if self.index_file.exists():
            status["index_exists"] = True
            status["index_file"] = str(self.index_file)
            status["index_size_bytes"] = (
                self.index_file.stat().st_size
            )
            status["index_modified_at"] = (
                datetime.fromtimestamp(
                    self.index_file.stat().st_mtime
                ).strftime("%Y-%m-%d %H:%M:%S")
            )
        else:
            status["index_exists"] = False
            status["index_file"] = str(self.index_file)
            status["index_size_bytes"] = 0
            status["index_modified_at"] = ""

        return status

    def is_building(self) -> bool:
        return self.get_status().get("state") == "building"

    # ---------------------------------------------------------
    # Text helpers
    # ---------------------------------------------------------
    @staticmethod
    def _normalize_text(value: Any) -> str:
        text = str(value or "").lower()

        text = re.sub(
            r"([a-z])([A-Z])",
            r"\1 \2",
            str(value or ""),
        ).lower()

        text = text.replace("_", " ")
        text = text.replace("-", " ")

        text = re.sub(
            r"[^a-z0-9\s]+",
            " ",
            text,
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        )

        return text.strip()

    @classmethod
    def _tokenize(cls, value: Any) -> list[str]:
        normalized = cls._normalize_text(value)

        stop_words = {
            "a",
            "an",
            "and",
            "are",
            "as",
            "at",
            "be",
            "by",
            "for",
            "from",
            "in",
            "is",
            "it",
            "of",
            "on",
            "or",
            "that",
            "the",
            "this",
            "to",
            "with",
            "verify",
            "test",
            "case",
            "should",
        }

        return [
            token
            for token in normalized.split()
            if len(token) >= 2
            and token not in stop_words
        ]

    @staticmethod
    def _flatten_parent_names(
        suite_tree: dict[str, Any],
    ) -> list[str]:
        names: list[str] = []

        def visit(node: dict[str, Any]) -> None:
            for parent in (
                node.get("parent_suites")
                or []
            ):
                name = parent.get("suite_name")

                if name:
                    names.append(name)

                visit(parent)

        visit(suite_tree)

        return list(dict.fromkeys(names))

    # ---------------------------------------------------------
    # Index building
    # ---------------------------------------------------------
    def build_index(
        self,
        component: str = "",
    ) -> None:
        """
        Build the JSON index.

        This method is intended to run inside a background thread.
        """

        selected_component = component.strip()

        if self.is_building():
            raise RuntimeError(
                "Automation index creation is already running."
            )

        self._update_status(
            state="building",
            message="Preparing automation suites...",
            started_at=datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            completed_at="",
            current_suite="",
            processed_suites=0,
            total_suites=0,
            successful_suites=0,
            failed_suites=0,
            collection_count=0,
            test_count=0,
            component=selected_component,
            errors=[],
        )

        started_monotonic = time.monotonic()

        try:
            suites = self.repository_service.list_suites(
                component=selected_component,
                search_text="",
            )

            total_suites = len(suites)

            self._update_status(
                total_suites=total_suites,
                message=(
                    f"Found {total_suites} suites. "
                    "Starting suite analysis..."
                ),
            )

            indexed_suites: list[dict[str, Any]] = []
            all_errors: list[dict[str, str]] = []

            successful_suites = 0
            failed_suites = 0
            total_collections = 0
            total_tests = 0

            for position, suite in enumerate(
                suites,
                start=1,
            ):
                suite_name = (
                    suite.get("suite_name")
                    or "Unknown Suite"
                )

                relative_path = (
                    suite.get("relative_path")
                    or ""
                )

                self._update_status(
                    current_suite=suite_name,
                    processed_suites=position - 1,
                    message=(
                        f"Analyzing {suite_name} "
                        f"({position} of {total_suites})..."
                    ),
                )

                try:
                    parsed = (
                        self.suite_parser_service.parse_suite(
                            relative_path
                        )
                    )

                    collections: list[
                        dict[str, Any]
                    ] = []

                    for collection in (
                        parsed.get("collections")
                        or []
                    ):
                        indexed_tests: list[
                            dict[str, Any]
                        ] = []

                        for test in (
                            collection.get("tests")
                            or []
                        ):
                            test_name = (
                                test.get("name")
                                or ""
                            )

                            description = (
                                test.get("description")
                                or ""
                            )

                            searchable_text = " ".join(
                                [
                                    suite_name,
                                    suite.get(
                                        "component",
                                        "",
                                    ),
                                    collection.get(
                                        "class_name",
                                        "",
                                    ),
                                    collection.get(
                                        "module",
                                        "",
                                    ),
                                    test_name,
                                    description,
                                    collection.get(
                                        "relative_path",
                                        "",
                                    ),
                                ]
                            )

                            indexed_tests.append(
                                {
                                    "name": test_name,
                                    "description": description,
                                    "line_number": test.get(
                                        "line_number"
                                    ),
                                    "search_text": (
                                        self._normalize_text(
                                            searchable_text
                                        )
                                    ),
                                    "tokens": self._tokenize(
                                        searchable_text
                                    ),
                                }
                            )

                        collection_search_text = " ".join(
                            [
                                suite_name,
                                suite.get(
                                    "component",
                                    "",
                                ),
                                collection.get(
                                    "class_name",
                                    "",
                                ),
                                collection.get(
                                    "module",
                                    "",
                                ),
                                collection.get(
                                    "relative_path",
                                    "",
                                ),
                            ]
                        )

                        indexed_collection = {
                            "name": (
                                collection.get(
                                    "class_name"
                                )
                                or collection.get(
                                    "module"
                                )
                                or "Unknown Collection"
                            ),
                            "module": collection.get(
                                "module",
                                "",
                            ),
                            "class_name": collection.get(
                                "class_name",
                                "",
                            ),
                            "source_suite": collection.get(
                                "source_suite",
                                suite_name,
                            ),
                            "file_found": collection.get(
                                "file_found",
                                False,
                            ),
                            "relative_path": collection.get(
                                "relative_path",
                                "",
                            ),
                            "message": collection.get(
                                "message",
                                "",
                            ),
                            "test_count": len(
                                indexed_tests
                            ),
                            "tests": indexed_tests,
                            "search_text": (
                                self._normalize_text(
                                    collection_search_text
                                )
                            ),
                            "tokens": self._tokenize(
                                collection_search_text
                            ),
                        }

                        collections.append(
                            indexed_collection
                        )

                    parent_suites = (
                        self._flatten_parent_names(
                            parsed.get(
                                "suite_tree",
                                {},
                            )
                        )
                    )

                    suite_search_text = " ".join(
                        [
                            suite_name,
                            suite.get(
                                "component",
                                "",
                            ),
                            relative_path,
                            " ".join(parent_suites),
                            " ".join(
                                collection.get(
                                    "name",
                                    "",
                                )
                                for collection
                                in collections
                            ),
                        ]
                    )

                    indexed_suite = {
                        "suite_name": suite_name,
                        "component": suite.get(
                            "component",
                            "",
                        ),
                        "relative_path": relative_path,
                        "parent_suites": parent_suites,
                        "collection_count": len(
                            collections
                        ),
                        "test_count": sum(
                            collection.get(
                                "test_count",
                                0,
                            )
                            for collection
                            in collections
                        ),
                        "collections": collections,
                        "search_text": (
                            self._normalize_text(
                                suite_search_text
                            )
                        ),
                        "tokens": self._tokenize(
                            suite_search_text
                        ),
                    }

                    indexed_suites.append(
                        indexed_suite
                    )

                    successful_suites += 1
                    total_collections += (
                        indexed_suite[
                            "collection_count"
                        ]
                    )
                    total_tests += (
                        indexed_suite["test_count"]
                    )

                except Exception as exc:
                    failed_suites += 1

                    error_row = {
                        "suite_name": suite_name,
                        "relative_path": relative_path,
                        "message": str(exc),
                    }

                    all_errors.append(error_row)

                self._update_status(
                    processed_suites=position,
                    successful_suites=(
                        successful_suites
                    ),
                    failed_suites=failed_suites,
                    collection_count=(
                        total_collections
                    ),
                    test_count=total_tests,
                    errors=all_errors[-25:],
                )

            elapsed_seconds = round(
                time.monotonic()
                - started_monotonic,
                2,
            )

            index_payload = {
                "schema_version": 1,
                "generated_at": (
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                ),
                "component_filter": (
                    selected_component
                ),
                "elapsed_seconds": elapsed_seconds,
                "summary": {
                    "suite_count": len(
                        indexed_suites
                    ),
                    "successful_suites": (
                        successful_suites
                    ),
                    "failed_suites": failed_suites,
                    "collection_count": (
                        total_collections
                    ),
                    "test_count": total_tests,
                },
                "errors": all_errors,
                "suites": indexed_suites,
            }

            temporary_file = (
                self.index_file.with_suffix(
                    ".tmp"
                )
            )

            with temporary_file.open(
                "w",
                encoding="utf-8",
            ) as file_handle:
                json.dump(
                    index_payload,
                    file_handle,
                    indent=2,
                    ensure_ascii=False,
                )

            temporary_file.replace(
                self.index_file
            )

            self._update_status(
                state="completed",
                message=(
                    "Automation index created "
                    "successfully."
                ),
                completed_at=(
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                ),
                current_suite="",
                processed_suites=total_suites,
                successful_suites=(
                    successful_suites
                ),
                failed_suites=failed_suites,
                collection_count=(
                    total_collections
                ),
                test_count=total_tests,
                errors=all_errors[-25:],
            )

        except Exception as exc:
            self._update_status(
                state="failed",
                message=str(exc),
                completed_at=(
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                ),
                current_suite="",
            )

            raise

    # ---------------------------------------------------------
    # Index reading
    # ---------------------------------------------------------
    def read_index(self) -> dict[str, Any]:
        if not self.index_file.exists():
            raise FileNotFoundError(
                "Automation index has not been created."
            )

        with self.index_file.open(
            "r",
            encoding="utf-8",
        ) as file_handle:
            return json.load(file_handle)

    # ---------------------------------------------------------
    # Search
    # ---------------------------------------------------------
    @classmethod
    def _calculate_score(
        cls,
        query: str,
        suite: dict[str, Any],
        collection: dict[str, Any],
        test: dict[str, Any],
    ) -> tuple[int, list[str]]:
        query_normalized = cls._normalize_text(
            query
        )

        query_tokens = set(
            cls._tokenize(query)
        )

        if not query_tokens:
            return 0, []

        suite_name = cls._normalize_text(
            suite.get("suite_name", "")
        )

        component = cls._normalize_text(
            suite.get("component", "")
        )

        collection_name = cls._normalize_text(
            collection.get("name", "")
        )

        test_name = cls._normalize_text(
            test.get("name", "")
        )

        description = cls._normalize_text(
            test.get("description", "")
        )

        score = 0
        reasons: list[str] = []

        # Complete phrase matches
        if (
            query_normalized
            and query_normalized in test_name
        ):
            score += 80
            reasons.append(
                "Full query matches test name"
            )

        if (
            query_normalized
            and query_normalized
            in collection_name
        ):
            score += 60
            reasons.append(
                "Full query matches collection"
            )

        if (
            query_normalized
            and query_normalized in suite_name
        ):
            score += 40
            reasons.append(
                "Full query matches suite"
            )

        # Individual token matches
        for token in query_tokens:
            matched = False

            if token in test_name:
                score += 16
                matched = True

            if token in collection_name:
                score += 12
                matched = True

            if token in description:
                score += 8
                matched = True

            if token in component:
                score += 7
                matched = True

            if token in suite_name:
                score += 5
                matched = True

            if matched:
                reasons.append(
                    f"Matched keyword: {token}"
                )

        matched_token_count = sum(
            1
            for token in query_tokens
            if (
                token in test_name
                or token in collection_name
                or token in description
                or token in component
                or token in suite_name
            )
        )

        coverage = (
            matched_token_count
            / len(query_tokens)
        )

        score += round(coverage * 30)

        # Keep score understandable in UI.
        score = min(score, 100)

        unique_reasons = list(
            dict.fromkeys(reasons)
        )

        return score, unique_reasons[:6]

    def search(
        self,
        query: str,
        component: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        search_query = query.strip()

        if not search_query:
            raise ValueError(
                "Search text is required."
            )

        selected_component = (
            component.strip().lower()
        )

        safe_limit = max(
            1,
            min(int(limit), 100),
        )

        index_payload = self.read_index()

        results: list[dict[str, Any]] = []

        for suite in (
            index_payload.get("suites")
            or []
        ):
            suite_component = str(
                suite.get("component", "")
            )

            if (
                selected_component
                and suite_component.lower()
                != selected_component
            ):
                continue

            for collection in (
                suite.get("collections")
                or []
            ):
                for test in (
                    collection.get("tests")
                    or []
                ):
                    score, reasons = (
                        self._calculate_score(
                            query=search_query,
                            suite=suite,
                            collection=collection,
                            test=test,
                        )
                    )

                    if score <= 0:
                        continue

                    results.append(
                        {
                            "score": score,
                            "suite_name": (
                                suite.get(
                                    "suite_name",
                                    "",
                                )
                            ),
                            "component": (
                                suite_component
                            ),
                            "suite_path": (
                                suite.get(
                                    "relative_path",
                                    "",
                                )
                            ),
                            "collection_name": (
                                collection.get(
                                    "name",
                                    "",
                                )
                            ),
                            "collection_path": (
                                collection.get(
                                    "relative_path",
                                    "",
                                )
                            ),
                            "source_suite": (
                                collection.get(
                                    "source_suite",
                                    "",
                                )
                            ),
                            "test_name": (
                                test.get(
                                    "name",
                                    "",
                                )
                            ),
                            "description": (
                                test.get(
                                    "description",
                                    "",
                                )
                            ),
                            "line_number": (
                                test.get(
                                    "line_number"
                                )
                            ),
                            "reasons": reasons,
                        }
                    )

        results.sort(
            key=lambda item: (
                -item["score"],
                item["suite_name"].lower(),
                item[
                    "collection_name"
                ].lower(),
                item["test_name"].lower(),
            )
        )

        limited_results = results[:safe_limit]

        return {
            "query": search_query,
            "component": component,
            "total_matches": len(results),
            "returned_matches": len(
                limited_results
            ),
            "results": limited_results,
        }