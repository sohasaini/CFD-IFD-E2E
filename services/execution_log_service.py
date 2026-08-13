from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from services.automation_runner_service import (
    AutomationRunnerService,
)


class ExecutionLogService:
    """
    Collects complete testcase evidence from CiscoAutomationRunner.

    Responsibilities:
    - Call caseinfo for every testcase in one execution summary.
    - Parse status, logs, documentation, result files and timing.
    - Save one structured JSON file for the complete execution.
    - Produce compact evidence text for AI classification.

    This service does not classify the issue and does not generate HTML.
    """

    def __init__(
        self,
        runner_service: AutomationRunnerService,
        output_directory: Path,
        maximum_ai_log_characters_per_test: int = 12000,
    ) -> None:
        self.runner_service = runner_service
        self.output_directory = Path(
            output_directory
        )

        self.output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.maximum_ai_log_characters_per_test = max(
            2000,
            int(
                maximum_ai_log_characters_per_test
            ),
        )

    @staticmethod
    def _clean(value: Any) -> str:
        return str(
            value or ""
        ).strip()

    @staticmethod
    def _local_name(tag: str) -> str:
        return str(
            tag or ""
        ).rsplit(
            "}",
            1,
        )[-1]

    @classmethod
    def _find_first_text(
        cls,
        root: ET.Element,
        names: set[str],
    ) -> str:
        normalised_names = {
            name.lower()
            for name in names
        }

        for element in root.iter():
            local_name = cls._local_name(
                element.tag
            ).lower()

            if (
                local_name
                in normalised_names
            ):
                value = cls._clean(
                    element.text
                )

                if value:
                    return value

        return ""

    @classmethod
    def _find_all_text(
        cls,
        root: ET.Element,
        names: set[str],
    ) -> list[str]:
        normalised_names = {
            name.lower()
            for name in names
        }

        values: list[str] = []

        for element in root.iter():
            local_name = cls._local_name(
                element.tag
            ).lower()

            if (
                local_name
                not in normalised_names
            ):
                continue

            value = cls._clean(
                element.text
            )

            if (
                value
                and value not in values
            ):
                values.append(
                    value
                )

        return values

    @staticmethod
    def _normalise_status(
        value: Any,
    ) -> str:
        status = str(
            value or ""
        ).strip().upper()

        return status or "UNKNOWN"

    @staticmethod
    def _normalise_log_line(
        value: Any,
    ) -> str:
        text = str(
            value or ""
        )

        text = text.replace(
            "\r",
            "\n",
        )

        text = re.sub(
            r"\x1b\[[0-9;]*m",
            "",
            text,
        )

        text = re.sub(
            r"[ \t]+",
            " ",
            text,
        )

        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        return text.strip()

    @classmethod
    def _extract_log_entries(
        cls,
        root: ET.Element,
    ) -> list[dict[str, str]]:
        entries: list[
            dict[str, str]
        ] = []

        for element in root.iter():
            if (
                cls._local_name(
                    element.tag
                ).lower()
                != "log"
            ):
                continue

            timestamp = ""
            message = ""

            for child in list(
                element
            ):
                child_name = (
                    cls._local_name(
                        child.tag
                    ).lower()
                )

                if child_name == "time":
                    timestamp = cls._clean(
                        child.text
                    )

                elif child_name in {
                    "message",
                    "text",
                    "value",
                }:
                    message = (
                        cls._normalise_log_line(
                            child.text
                        )
                    )

            if not message:
                message = (
                    cls._normalise_log_line(
                        element.text
                    )
                )

            if not message:
                continue

            entries.append(
                {
                    "time": timestamp,
                    "message": message,
                }
            )

        return entries

    @classmethod
    def _extract_result_files(
        cls,
        root: ET.Element,
    ) -> list[str]:
        result_names = {
            "resultdata",
            "result-data",
            "result_file",
            "resultfile",
            "datafile",
            "file",
        }

        files: list[str] = []

        for element in root.iter():
            local_name = cls._local_name(
                element.tag
            ).lower()

            if local_name not in result_names:
                continue

            value = cls._clean(
                element.text
            )

            if (
                value
                and value not in files
            ):
                files.append(
                    value
                )

        return files

    @staticmethod
    def _find_first_matching_message(
        logs: list[dict[str, str]],
        patterns: tuple[str, ...],
    ) -> str:
        lowered_patterns = tuple(
            pattern.lower()
            for pattern in patterns
        )

        for entry in logs:
            message = str(
                entry.get(
                    "message",
                    "",
                )
            ).strip()

            lowered = message.lower()

            if any(
                pattern in lowered
                for pattern in lowered_patterns
            ):
                return message

        return ""

    @classmethod
    def _build_failure_evidence(
        cls,
        status: str,
        logs: list[dict[str, str]],
    ) -> list[str]:
        evidence: list[str] = []

        markers = (
            "failed",
            "error",
            "exception",
            "traceback",
            "timed out",
            "timeout",
            "connection refused",
            "not found",
            "assert",
            "crash",
            "unreachable",
            "permission denied",
            "productversionerror",
        )

        for entry in logs:
            message = str(
                entry.get(
                    "message",
                    "",
                )
            ).strip()

            lowered = message.lower()

            if any(
                marker in lowered
                for marker in markers
            ):
                if message not in evidence:
                    evidence.append(
                        message
                    )

            if len(evidence) >= 25:
                break

        if (
            status
            in {
                "FAILED",
                "ERRORED",
                "CRASHED",
                "BLOCKED",
            }
            and not evidence
        ):
            evidence.append(
                "The testcase finished with "
                f"status {status}, but no explicit failure "
                "message was found in the caseinfo logs."
            )

        return evidence

    @classmethod
    def parse_caseinfo_xml(
        cls,
        raw_xml: str,
        expected_testcase: str = "",
    ) -> dict[str, Any]:
        xml_text = str(
            raw_xml or ""
        ).strip()

        if not xml_text:
            raise ValueError(
                "caseinfo XML is empty."
            )

        try:
            root = ET.fromstring(
                xml_text
            )

        except ET.ParseError as exc:
            raise RuntimeError(
                "caseinfo returned invalid XML."
            ) from exc

        testcase = (
            cls._find_first_text(
                root,
                {
                    "name",
                    "testcase",
                    "testcasename",
                    "test-case-name",
                },
            )
            or cls._clean(
                expected_testcase
            )
        )

        status = cls._normalise_status(
            cls._find_first_text(
                root,
                {
                    "status",
                    "result",
                },
            )
        )

        documentation = (
            cls._find_first_text(
                root,
                {
                    "documentation",
                    "description",
                    "doc",
                },
            )
        )

        started_at = (
            cls._find_first_text(
                root,
                {
                    "starttime",
                    "start-time",
                    "startedat",
                    "started-at",
                },
            )
        )

        completed_at = (
            cls._find_first_text(
                root,
                {
                    "endtime",
                    "end-time",
                    "finishtime",
                    "finish-time",
                    "completedat",
                    "completed-at",
                },
            )
        )

        error = (
            cls._find_first_text(
                root,
                {
                    "error",
                },
            )
        )

        logs = cls._extract_log_entries(
            root
        )

        result_files = (
            cls._extract_result_files(
                root
            )
        )

        failure_evidence = (
            cls._build_failure_evidence(
                status=status,
                logs=logs,
            )
        )

        completion_message = (
            cls._find_first_matching_message(
                logs,
                (
                    "completed test",
                    "test passed",
                    "test failed",
                    "test errored",
                ),
            )
        )

        return {
            "testcase": testcase,
            "status": status,
            "passed": status == "PASSED",
            "failed": status
            in {
                "FAILED",
                "ERRORED",
                "CRASHED",
                "BLOCKED",
            },
            "error": error,
            "started_at": started_at,
            "completed_at": completed_at,
            "documentation": (
                cls._normalise_log_line(
                    documentation
                )
            ),
            "logs": logs,
            "log_count": len(
                logs
            ),
            "result_files": result_files,
            "result_file_count": len(
                result_files
            ),
            "failure_evidence": (
                failure_evidence
            ),
            "completion_message": (
                completion_message
            ),
            "raw_xml": xml_text,
        }

    def collect_testcase(
        self,
        host: str,
        testcase: str,
    ) -> dict[str, Any]:
        details = (
            self.runner_service
            .get_testcase_details(
                host=host,
                testcase=testcase,
            )
        )

        parsed = self.parse_caseinfo_xml(
            raw_xml=details.get(
                "raw_xml",
                "",
            ),
            expected_testcase=testcase,
        )

        return {
            "host": self._clean(
                host
            ),
            **parsed,
        }

    def collect_execution_logs(
        self,
        execution_summary: dict[str, Any],
    ) -> dict[str, Any]:
        host = self._clean(
            execution_summary.get(
                "host"
            )
        )

        session_id = self._clean(
            execution_summary.get(
                "session_id"
            )
        )

        collection = self._clean(
            execution_summary.get(
                "collection"
            )
        )

        if not host:
            raise ValueError(
                "Execution host is missing."
            )

        testcase_entries = (
            execution_summary.get(
                "testcases",
                [],
            )
            or []
        )

        testcase_names: list[str] = []

        for item in testcase_entries:
            if isinstance(
                item,
                dict,
            ):
                testcase = self._clean(
                    item.get(
                        "testcase"
                    )
                )
            else:
                testcase = self._clean(
                    item
                )

            if (
                testcase
                and testcase
                not in testcase_names
            ):
                testcase_names.append(
                    testcase
                )

        if not testcase_names:
            raise ValueError(
                "No testcases were available for log collection."
            )

        collected: list[
            dict[str, Any]
        ] = []

        errors: list[
            dict[str, str]
        ] = []

        for testcase in testcase_names:
            try:
                collected.append(
                    self.collect_testcase(
                        host=host,
                        testcase=testcase,
                    )
                )

            except Exception as exc:
                errors.append(
                    {
                        "testcase": testcase,
                        "error": str(exc),
                    }
                )

        status_counts = {
            "total": len(
                collected
            ),
            "passed": 0,
            "failed": 0,
            "errored": 0,
            "crashed": 0,
            "blocked": 0,
            "unknown": 0,
        }

        for testcase in collected:
            status = self._normalise_status(
                testcase.get(
                    "status"
                )
            )

            if status == "PASSED":
                status_counts[
                    "passed"
                ] += 1

            elif status == "FAILED":
                status_counts[
                    "failed"
                ] += 1

            elif status == "ERRORED":
                status_counts[
                    "errored"
                ] += 1

            elif status == "CRASHED":
                status_counts[
                    "crashed"
                ] += 1

            elif status == "BLOCKED":
                status_counts[
                    "blocked"
                ] += 1

            else:
                status_counts[
                    "unknown"
                ] += 1

        result = {
            "session_id": session_id,
            "host": host,
            "collection": collection,
            "collected_at": (
                datetime.now()
                .isoformat()
            ),
            "requested_testcases": len(
                testcase_names
            ),
            "collected_testcases": len(
                collected
            ),
            "collection_errors": errors,
            "status_counts": status_counts,
            "testcases": collected,
            "all_caseinfo_collected": (
                len(errors) == 0
                and len(collected)
                == len(testcase_names)
            ),
            "ready_for_ai_analysis": bool(
                collected
            ),
            "next_stage": (
                "issue_classification"
            ),
        }

        result["saved_file"] = str(
            self.save_execution_logs(
                result
            )
        )

        result["ai_evidence"] = (
            self.build_ai_evidence(
                result
            )
        )

        return result

    def build_ai_evidence(
        self,
        collected_logs: dict[str, Any],
    ) -> dict[str, Any]:
        testcases = (
            collected_logs.get(
                "testcases",
                [],
            )
            or []
        )

        evidence_cases: list[
            dict[str, Any]
        ] = []

        for testcase in testcases:
            logs = (
                testcase.get(
                    "logs",
                    [],
                )
                or []
            )

            log_text = "\n".join(
                str(
                    entry.get(
                        "message",
                        "",
                    )
                ).strip()
                for entry in logs
                if isinstance(
                    entry,
                    dict,
                )
                and str(
                    entry.get(
                        "message",
                        "",
                    )
                ).strip()
            )

            if (
                len(log_text)
                > self.maximum_ai_log_characters_per_test
            ):
                start_size = (
                    self.maximum_ai_log_characters_per_test
                    // 2
                )

                end_size = (
                    self.maximum_ai_log_characters_per_test
                    - start_size
                )

                log_text = (
                    log_text[
                        :start_size
                    ]
                    + "\n\n...[middle log content omitted]...\n\n"
                    + log_text[
                        -end_size:
                    ]
                )

            evidence_cases.append(
                {
                    "testcase": testcase.get(
                        "testcase",
                        "",
                    ),
                    "status": testcase.get(
                        "status",
                        "UNKNOWN",
                    ),
                    "documentation": testcase.get(
                        "documentation",
                        "",
                    ),
                    "failure_evidence": testcase.get(
                        "failure_evidence",
                        [],
                    ),
                    "completion_message": testcase.get(
                        "completion_message",
                        "",
                    ),
                    "result_files": testcase.get(
                        "result_files",
                        [],
                    ),
                    "log_text": log_text,
                }
            )

        return {
            "session_id": collected_logs.get(
                "session_id",
                "",
            ),
            "host": collected_logs.get(
                "host",
                "",
            ),
            "collection": collected_logs.get(
                "collection",
                "",
            ),
            "status_counts": collected_logs.get(
                "status_counts",
                {},
            ),
            "testcases": evidence_cases,
        }

    def save_execution_logs(
        self,
        collected_logs: dict[str, Any],
    ) -> Path:
        session_id = self._clean(
            collected_logs.get(
                "session_id"
            )
        )

        timestamp = (
            datetime.now()
            .strftime(
                "%Y%m%d_%H%M%S"
            )
        )

        safe_session = re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            session_id or "execution",
        )

        output_file = (
            self.output_directory
            / (
                f"{timestamp}_"
                f"{safe_session}_"
                "testcase_logs.json"
            )
        )

        output_file.write_text(
            json.dumps(
                collected_logs,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return output_file