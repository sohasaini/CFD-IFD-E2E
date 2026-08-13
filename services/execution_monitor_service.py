from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from services.automation_runner_service import (
    AutomationRunnerService,
)


TERMINAL_SESSION_STATES = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
}

TERMINAL_TEST_STATUSES = {
    "PASSED",
    "FAILED",
    "ERRORED",
    "CRASHED",
    "BLOCKED",
    "SKIPPED",
}


class ExecutionMonitorService:
    """
    Monitors one AutomationRunnerService session.

    Responsibilities:
    - Read the current runner session.
    - Wait until the execution reaches a terminal state.
    - Calculate pass/fail/error/not-run counts.
    - Return a stable execution summary for the next services.

    This service does not trigger execution and does not fetch testcase logs.
    """

    def __init__(
        self,
        runner_service: AutomationRunnerService,
        poll_interval_seconds: int = 5,
        default_timeout_seconds: int = 7200,
    ) -> None:
        self.runner_service = runner_service
        self.poll_interval_seconds = max(
            1,
            int(poll_interval_seconds),
        )
        self.default_timeout_seconds = max(
            60,
            int(default_timeout_seconds),
        )

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _parse_datetime(
        value: Any,
    ) -> datetime | None:
        raw = str(value or "").strip()

        if not raw:
            return None

        supported_formats = (
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        )

        for date_format in supported_formats:
            try:
                return datetime.strptime(
                    raw,
                    date_format,
                )
            except ValueError:
                continue

        return None

    @classmethod
    def _duration_seconds(
        cls,
        started_at: Any,
        completed_at: Any,
    ) -> int:
        started = cls._parse_datetime(
            started_at
        )

        completed = cls._parse_datetime(
            completed_at
        )

        if not started:
            return 0

        if not completed:
            completed = datetime.utcnow()

        return max(
            0,
            int(
                (
                    completed
                    - started
                ).total_seconds()
            ),
        )

    @staticmethod
    def _normalise_status(
        value: Any,
    ) -> str:
        status = str(
            value or ""
        ).strip().upper()

        if status.startswith(
            "UNKNOWN:"
        ):
            return "UNKNOWN"

        return status or "UNKNOWN"

    def _build_testcase_results(
        self,
        session: dict[str, Any],
    ) -> list[dict[str, Any]]:
        testcase_map = (
            session.get(
                "testcases",
                {},
            )
            or {}
        )

        if not isinstance(
            testcase_map,
            dict,
        ):
            testcase_map = {}

        results: list[
            dict[str, Any]
        ] = []

        for testcase, raw_status in testcase_map.items():
            status = self._normalise_status(
                raw_status
            )

            results.append(
                {
                    "testcase": self._clean(
                        testcase
                    ),
                    "status": status,
                    "terminal": (
                        status
                        in TERMINAL_TEST_STATUSES
                    ),
                    "passed": (
                        status == "PASSED"
                    ),
                    "failed": (
                        status
                        in {
                            "FAILED",
                            "ERRORED",
                            "CRASHED",
                            "BLOCKED",
                        }
                    ),
                }
            )

        return results

    def _build_counts(
        self,
        testcase_results: list[
            dict[str, Any]
        ],
    ) -> dict[str, int]:
        counts = {
            "total": len(
                testcase_results
            ),
            "passed": 0,
            "failed": 0,
            "errored": 0,
            "crashed": 0,
            "blocked": 0,
            "skipped": 0,
            "running": 0,
            "not_run": 0,
            "unknown": 0,
            "completed": 0,
        }

        for result in testcase_results:
            status = result["status"]

            if status == "PASSED":
                counts["passed"] += 1

            elif status == "FAILED":
                counts["failed"] += 1

            elif status == "ERRORED":
                counts["errored"] += 1

            elif status == "CRASHED":
                counts["crashed"] += 1

            elif status == "BLOCKED":
                counts["blocked"] += 1

            elif status == "SKIPPED":
                counts["skipped"] += 1

            elif status == "RUNNING":
                counts["running"] += 1

            elif status in {
                "NOTRUN",
                "NOT_RUN",
                "PENDING",
                "QUEUED",
                "",
            }:
                counts["not_run"] += 1

            else:
                counts["unknown"] += 1

            if status in TERMINAL_TEST_STATUSES:
                counts["completed"] += 1

        return counts

    def get_snapshot(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        clean_session_id = self._clean(
            session_id
        )

        if not clean_session_id:
            raise ValueError(
                "Runner session ID is required."
            )

        session = (
            self.runner_service
            .get_session(
                clean_session_id
            )
        )

        if session is None:
            raise FileNotFoundError(
                "Runner session was not found."
            )

        testcase_results = (
            self._build_testcase_results(
                session
            )
        )

        counts = self._build_counts(
            testcase_results
        )

        session_state = (
            self._normalise_status(
                session.get("state")
            )
        )

        terminal = (
            session_state
            in TERMINAL_SESSION_STATES
        )

        total_tests = int(
            session.get(
                "total_tests",
                counts["total"],
            )
            or counts["total"]
        )

        completed_tests = int(
            session.get(
                "completed_tests",
                counts["completed"],
            )
            or counts["completed"]
        )

        progress = float(
            session.get(
                "progress_percentage",
                0.0,
            )
            or 0.0
        )

        if (
            total_tests > 0
            and progress <= 0
        ):
            progress = round(
                (
                    completed_tests
                    / total_tests
                )
                * 100,
                2,
            )

        return {
            "session_id": clean_session_id,
            "host": self._clean(
                session.get("host")
            ),
            "collection": self._clean(
                session.get(
                    "collection"
                )
            ),
            "state": session_state,
            "terminal": terminal,
            "successfully_completed": (
                session_state
                == "COMPLETED"
            ),
            "machine_state": self._clean(
                session.get(
                    "machine_state"
                )
            ),
            "current_test": self._clean(
                session.get(
                    "current_test"
                )
            ),
            "message": self._clean(
                session.get(
                    "message"
                )
            ),
            "error": self._clean(
                session.get(
                    "error"
                )
            ),
            "total_tests": total_tests,
            "completed_tests": (
                completed_tests
            ),
            "progress_percentage": round(
                progress,
                2,
            ),
            "counts": counts,
            "testcases": testcase_results,
            "created_at": self._clean(
                session.get(
                    "created_at"
                )
            ),
            "started_at": self._clean(
                session.get(
                    "started_at"
                )
            ),
            "completed_at": self._clean(
                session.get(
                    "completed_at"
                )
            ),
            "duration_seconds": (
                self._duration_seconds(
                    session.get(
                        "started_at"
                    ),
                    session.get(
                        "completed_at"
                    ),
                )
            ),
        }

    def wait_for_completion(
        self,
        session_id: str,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        timeout = (
            self.default_timeout_seconds
            if timeout_seconds is None
            else max(
                60,
                int(timeout_seconds),
            )
        )

        started = time.monotonic()

        while True:
            snapshot = self.get_snapshot(
                session_id
            )

            if snapshot["terminal"]:
                return snapshot

            if (
                time.monotonic()
                - started
                >= timeout
            ):
                return {
                    **snapshot,
                    "state": (
                        "MONITOR_TIMEOUT"
                    ),
                    "terminal": True,
                    "successfully_completed": False,
                    "error": (
                        "Execution monitoring timed out "
                        f"after {timeout} seconds."
                    ),
                    "message": (
                        "Execution is still running, but "
                        "the monitoring timeout was reached."
                    ),
                }

            time.sleep(
                self.poll_interval_seconds
            )

    def build_execution_summary(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        snapshot = self.get_snapshot(
            session_id
        )

        if not snapshot["terminal"]:
            raise RuntimeError(
                "Execution is still running."
            )

        counts = snapshot["counts"]

        issue_observed = bool(
            counts["failed"]
            or counts["errored"]
            or counts["crashed"]
            or counts["blocked"]
            or snapshot["state"]
            in {
                "FAILED",
                "CANCELLED",
                "MONITOR_TIMEOUT",
            }
        )

        all_tests_passed = bool(
            snapshot["state"]
            == "COMPLETED"
            and counts["total"] > 0
            and counts["passed"]
            == counts["total"]
        )

        return {
            **snapshot,
            "all_tests_passed": (
                all_tests_passed
            ),
            "issue_observed": (
                issue_observed
            ),
            "execution_result": (
                "PASSED"
                if all_tests_passed
                else "FAILED"
                if issue_observed
                else snapshot["state"]
            ),
            "ready_for_log_collection": bool(
                snapshot["testcases"]
            ),
            "next_stage": (
                "testcase_log_collection"
            ),
        }