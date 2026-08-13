from __future__ import annotations

import json
import re
from typing import Any

from services.cfd_ai_service import CfdAiService


ALLOWED_CONCLUSIONS = {
    "NOT_REPRODUCED",
    "REPRODUCED_PRODUCT_ISSUE",
    "AUTOMATION_ISSUE",
    "ENVIRONMENT_ISSUE",
    "OUT_OF_SCOPE",
    "INSUFFICIENT_EVIDENCE",
}


class IssueClassifierService:
    """
    Defect-focused AI investigation.

    Testcase status alone never determines the result. The service compares
    the original CFD scenario with testcase documentation and relevant logs.
    """

    def __init__(
        self,
        cfd_ai_service: CfdAiService,
    ) -> None:
        self.cfd_ai_service = cfd_ai_service

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _clamp(value: Any) -> int:
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            number = 0
        return max(0, min(number, 100))

    @staticmethod
    def _short(
        value: Any,
        maximum: int,
    ) -> str:
        text = re.sub(
            r"\s+",
            " ",
            str(value or "").strip(),
        )
        if len(text) <= maximum:
            return text
        return text[: maximum - 1].rstrip() + "…"

    @staticmethod
    def _normalise_conclusion(value: Any) -> str:
        raw = re.sub(
            r"[^A-Z0-9]+",
            "_",
            str(value or "").strip().upper(),
        ).strip("_")

        aliases = {
            "ALREADY_FIXED": "NOT_REPRODUCED",
            "FIXED": "NOT_REPRODUCED",
            "PRODUCT_ISSUE": "REPRODUCED_PRODUCT_ISSUE",
            "SCRIPT_ISSUE": "AUTOMATION_ISSUE",
            "INFRASTRUCTURE_ISSUE": "ENVIRONMENT_ISSUE",
        }

        result = aliases.get(raw, raw)
        if result not in ALLOWED_CONCLUSIONS:
            return "INSUFFICIENT_EVIDENCE"
        return result

    @classmethod
    def _compact_evidence(
        cls,
        execution_logs: dict[str, Any],
    ) -> list[dict[str, Any]]:
        source = execution_logs.get("ai_evidence", {}) or {}
        testcases = (
            source.get("testcases", [])
            or execution_logs.get("testcases", [])
            or []
        )

        compact: list[dict[str, Any]] = []

        for testcase in testcases:
            if not isinstance(testcase, dict):
                continue

            log_text = cls._clean(testcase.get("log_text"))
            if not log_text:
                logs = testcase.get("logs", []) or []
                log_text = "\n".join(
                    cls._clean(item.get("message"))
                    for item in logs
                    if isinstance(item, dict)
                    and cls._clean(item.get("message"))
                )

            if len(log_text) > 7000:
                log_text = (
                    log_text[:3500]
                    + "\n...[middle omitted]...\n"
                    + log_text[-3500:]
                )

            compact.append(
                {
                    "testcase": cls._clean(
                        testcase.get("testcase")
                    ),
                    "documentation": cls._short(
                        testcase.get("documentation"),
                        1200,
                    ),
                    "failure_evidence": (
                        testcase.get("failure_evidence", [])
                        or []
                    )[:8],
                    "completion_message": cls._short(
                        testcase.get("completion_message"),
                        500,
                    ),
                    "log_text": log_text,
                }
            )

        return compact

    def _build_prompt(
        self,
        defect: dict[str, Any],
        defect_analysis: dict[str, Any],
        execution_summary: dict[str, Any],
        execution_logs: dict[str, Any],
    ) -> str:
        evidence = self._compact_evidence(execution_logs)

        context = {
            "collection": execution_summary.get("collection"),
            "collections": execution_summary.get("collections", []),
            "collection_count": execution_summary.get("collection_count", 1),
            "host": execution_summary.get("host"),
            "runner_state": execution_summary.get("state"),
            "total_tests": execution_summary.get("total_tests", 0),
            "completed_tests": execution_summary.get("completed_tests", 0),
            "historical_defect_status": defect.get("status"),
        }

        return f"""
You are a senior AI defect validation architect.

Decide whether the exact ORIGINAL CUSTOMER DEFECT was covered and whether
its symptom was reproduced. Do not create a generic pass/fail report.

Return strict JSON only.

CFD:
{json.dumps(defect, ensure_ascii=False)}

ROOT-CAUSE AND FEATURE ANALYSIS:
{json.dumps(defect_analysis, ensure_ascii=False)}

EXECUTION CONTEXT:
{json.dumps(context, ensure_ascii=False)}

TESTCASE DOCUMENTATION AND RELEVANT LOG EVIDENCE:
{json.dumps(evidence, ensure_ascii=False)}

Decision rules:
1. The evidence may come from multiple sequential Regression collections.
   Evaluate all collections together before deciding coverage or conclusion.
2. Determine relevance from testcase purpose and evidence, not pass/fail.
3. Generic setup, DNS, VPN, licence, framework, or environment observations
   are unrelated unless they prevented the target scenario.
4. REPRODUCED_PRODUCT_ISSUE requires direct evidence of the same customer
   symptom or equivalent product behaviour.
5. NOT_REPRODUCED requires PARTIAL or FULL relevant coverage and no matching
   symptom.
6. Coverage NONE cannot produce NOT_REPRODUCED. Use OUT_OF_SCOPE or
   INSUFFICIENT_EVIDENCE.
7. Do not state a fixed release, build, patch, or known fix unless that exact
   release information is explicitly present in the supplied CFD fields.
8. Mark a testcase as direct coverage only when it exercises the same trigger,
   expected behaviour, and reported symptom. Otherwise mark it partial.
9. Keep all text brief and presentation-ready.

Required JSON:
{{
  "conclusion": "INSUFFICIENT_EVIDENCE",
  "confidence": 0,
  "original_customer_scenario": {{
    "trigger": "",
    "expected_behavior": "",
    "reported_behavior": ""
  }},
  "coverage_assessment": {{
    "coverage_level": "PARTIAL",
    "coverage_percentage": 0,
    "collection": "",
    "directly_covering_testcases": [
      {{
        "testcase": "",
        "coverage_reason": ""
      }}
    ],
    "partially_covering_testcases": [
      {{
        "testcase": "",
        "coverage_reason": ""
      }}
    ],
    "coverage_gap": ""
  }},
  "execution_comparison": {{
    "scenario_exercised": false,
    "customer_symptom_reproduced": false,
    "matching_product_evidence": [],
    "evidence_customer_symptom_not_seen": [],
    "unrelated_observations": []
  }},
  "decision_matrix": {{
    "product_issue": {{
      "is_issue": false,
      "reason": ""
    }},
    "automation_issue": {{
      "is_issue": false,
      "reason": ""
    }},
    "environment_issue": {{
      "is_issue": false,
      "reason": ""
    }},
    "out_of_scope": {{
      "is_issue": false,
      "reason": ""
    }}
  }},
  "primary_classification": "INSUFFICIENT_EVIDENCE",
  "secondary_findings": [],
  "executive_summary": "",
  "final_reason": "",
  "recommended_next_action": ""
}}

Length limits:
- executive_summary: maximum 28 words.
- final_reason: maximum 24 words.
- recommended_next_action: maximum 20 words.
- secondary_findings: maximum 2 items.
- each decision reason: maximum 18 words.
- each evidence item: maximum 20 words.
- coverage_gap: maximum 25 words.

Never include raw logs or long paragraphs.
"""

    def _request_ai(self, prompt: str) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a concise automation coverage and defect "
                    "validation architect. Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        try:
            response = self.cfd_ai_service._create_completion(
                messages=messages
            )
        except Exception:
            response = self.cfd_ai_service._create_completion(
                messages=messages,
                force_token_refresh=True,
            )

        if (
            not response.choices
            or not response.choices[0].message
        ):
            raise RuntimeError(
                "Cisco AI returned an empty investigation."
            )

        cleaned = self.cfd_ai_service._strip_json_fence(
            response.choices[0].message.content or ""
        )

        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Cisco AI returned invalid investigation JSON: "
                f"{cleaned[:1000]}"
            ) from exc

        if not isinstance(result, dict):
            raise RuntimeError(
                "Investigation result must be a JSON object."
            )

        return result

    def _validate(
        self,
        result: dict[str, Any],
        defect: dict[str, Any],
        execution_summary: dict[str, Any],
    ) -> dict[str, Any]:
        conclusion = self._normalise_conclusion(
            result.get("conclusion")
            or result.get("classification")
        )

        original = result.get(
            "original_customer_scenario",
            {},
        )
        if not isinstance(original, dict):
            original = {}

        coverage = result.get(
            "coverage_assessment",
            {},
        )
        if not isinstance(coverage, dict):
            coverage = {}

        comparison = result.get(
            "execution_comparison",
            {},
        )
        if not isinstance(comparison, dict):
            comparison = {}

        coverage_level = self._clean(
            coverage.get("coverage_level")
        ).upper()

        if coverage_level not in {
            "FULL",
            "PARTIAL",
            "NONE",
        }:
            coverage_level = "NONE"

        if conclusion == "NOT_REPRODUCED" and coverage_level == "NONE":
            conclusion = "OUT_OF_SCOPE"

        if (
            conclusion == "REPRODUCED_PRODUCT_ISSUE"
            and not bool(
                comparison.get("customer_symptom_reproduced")
            )
        ):
            conclusion = "INSUFFICIENT_EVIDENCE"

        labels = {
            "NOT_REPRODUCED": "Issue Not Reproduced",
            "REPRODUCED_PRODUCT_ISSUE": "Product Issue Reproduced",
            "AUTOMATION_ISSUE": "Automation Issue",
            "ENVIRONMENT_ISSUE": "Environment Issue",
            "OUT_OF_SCOPE": "Coverage Out of Scope",
            "INSUFFICIENT_EVIDENCE": "More Evidence Required",
        }

        confidence = self._clamp(
            result.get("confidence")
        )
        confidence_label = (
            "High"
            if confidence >= 80
            else "Medium"
            if confidence >= 55
            else "Low"
        )

        coverage["coverage_level"] = coverage_level
        coverage["coverage_percentage"] = self._clamp(
            coverage.get("coverage_percentage")
        )
        coverage["collection"] = (
            self._clean(coverage.get("collection"))
            or self._clean(execution_summary.get("collection"))
        )

        for key in (
            "directly_covering_testcases",
            "partially_covering_testcases",
        ):
            values = coverage.get(key, [])
            if not isinstance(values, list):
                values = []
            coverage[key] = values[:4]

        coverage["coverage_gap"] = self._short(
            coverage.get("coverage_gap"),
            180,
        )

        for key in (
            "matching_product_evidence",
            "evidence_customer_symptom_not_seen",
            "unrelated_observations",
        ):
            values = comparison.get(key, [])
            if not isinstance(values, list):
                values = []
            comparison[key] = [
                self._short(item, 150)
                for item in values[:3]
                if self._clean(item)
            ]

        selected_key = {
            "REPRODUCED_PRODUCT_ISSUE": "product_issue",
            "AUTOMATION_ISSUE": "automation_issue",
            "ENVIRONMENT_ISSUE": "environment_issue",
            "OUT_OF_SCOPE": "out_of_scope",
        }.get(conclusion)

        ai_matrix = result.get("decision_matrix", {})
        if not isinstance(ai_matrix, dict):
            ai_matrix = {}

        matrix: dict[str, dict[str, Any]] = {}
        defaults = {
            "product_issue": "No matching product symptom was confirmed.",
            "automation_issue": "Automation did not block the target investigation.",
            "environment_issue": "No environment blocker was confirmed.",
            "out_of_scope": "Relevant scenario coverage was available.",
        }

        for key in defaults:
            item = ai_matrix.get(key, {})
            if not isinstance(item, dict):
                item = {}

            is_issue = bool(
                item.get("is_issue")
            ) or key == selected_key

            reason = self._short(
                item.get("reason") or defaults[key],
                110,
            )

            matrix[key] = {
                "is_issue": is_issue,
                "reason": reason,
            }

        summary = self._short(
            result.get("executive_summary"),
            160,
        )
        reason = self._short(
            result.get("final_reason"),
            180,
        )
        action = self._short(
            result.get("recommended_next_action"),
            220,
        )

        primary_classification = self._normalise_conclusion(
            result.get("primary_classification")
            or conclusion
        )

        secondary_findings_raw = result.get(
            "secondary_findings",
            [],
        )
        if not isinstance(secondary_findings_raw, list):
            secondary_findings_raw = []

        secondary_findings = []
        for value in secondary_findings_raw[:2]:
            finding = self._normalise_conclusion(value)
            if (
                finding != "INSUFFICIENT_EVIDENCE"
                and finding != primary_classification
                and finding not in secondary_findings
            ):
                secondary_findings.append(finding)

        matrix_map = {
            "product_issue": "REPRODUCED_PRODUCT_ISSUE",
            "automation_issue": "AUTOMATION_ISSUE",
            "environment_issue": "ENVIRONMENT_ISSUE",
            "out_of_scope": "OUT_OF_SCOPE",
        }

        for matrix_key, finding in matrix_map.items():
            if (
                matrix.get(matrix_key, {}).get("is_issue")
                and finding != primary_classification
                and finding not in secondary_findings
                and len(secondary_findings) < 2
            ):
                secondary_findings.append(finding)

        return {
            "classification": conclusion,
            "conclusion": conclusion,
            "primary_classification": primary_classification,
            "secondary_findings": secondary_findings,
            "display_name": labels[conclusion],
            "confidence": confidence,
            "confidence_label": confidence_label,
            "original_customer_scenario": {
                "trigger": self._short(
                    original.get("trigger"),
                    220,
                ),
                "expected_behavior": self._short(
                    original.get("expected_behavior"),
                    220,
                ),
                "reported_behavior": self._short(
                    original.get("reported_behavior"),
                    220,
                ),
            },
            "coverage_assessment": coverage,
            "execution_comparison": comparison,
            "decision_matrix": matrix,
            "executive_summary": summary,
            "summary": summary,
            "final_reason": reason,
            "reason": reason,
            "recommended_next_action": action,
            "recommended_action": action,
            "jira_required": (
                conclusion == "REPRODUCED_PRODUCT_ISSUE"
            ),
            "script_fix_required": (
                conclusion == "AUTOMATION_ISSUE"
            ),
            "rerun_required": (
                conclusion
                in {
                    "AUTOMATION_ISSUE",
                    "ENVIRONMENT_ISSUE",
                    "INSUFFICIENT_EVIDENCE",
                }
            ),
            "alternative_collection_required": (
                conclusion == "OUT_OF_SCOPE"
                or coverage_level == "NONE"
            ),
            "defect_id": self._clean(defect.get("id")),
            "collection": self._clean(
                execution_summary.get("collection")
            ),
            "next_stage": "html_report_generation",
        }

    def classify(
        self,
        defect: dict[str, Any],
        defect_analysis: dict[str, Any],
        execution_summary: dict[str, Any],
        execution_logs: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._request_ai(
            self._build_prompt(
                defect=defect,
                defect_analysis=(
                    defect_analysis
                    if isinstance(defect_analysis, dict)
                    else {}
                ),
                execution_summary=execution_summary,
                execution_logs=execution_logs,
            )
        )

        return self._validate(
            result=result,
            defect=defect,
            execution_summary=execution_summary,
        )
