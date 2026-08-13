from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class HtmlReportService:
    """Generate a compact, visual, standalone E2E validation report."""

    LABELS = {
        "NOT_REPRODUCED": (
            "Issue Not Reproduced",
            "#2ee6a6",
        ),
        "REPRODUCED_PRODUCT_ISSUE": (
            "Product Issue",
            "#ff6f91",
        ),
        "AUTOMATION_ISSUE": (
            "Script Issue",
            "#ffbd59",
        ),
        "ENVIRONMENT_ISSUE": (
            "Environment Issue",
            "#4da3ff",
        ),
        "OUT_OF_SCOPE": (
            "Out of Scope",
            "#9a72ff",
        ),
        "INSUFFICIENT_EVIDENCE": (
            "More Evidence Required",
            "#a7b2c5",
        ),
    }

    def __init__(
        self,
        output_directory: Path,
    ) -> None:
        self.output_directory = Path(output_directory)
        self.output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _escape(value: Any) -> str:
        return html.escape(
            str(value or ""),
            quote=True,
        )

    @staticmethod
    def _safe(value: Any) -> str:
        result = re.sub(
            r"[^A-Za-z0-9._-]+",
            "_",
            str(value or "").strip(),
        ).strip("_")
        return result or "report"

    @staticmethod
    def _short(
        value: Any,
        maximum: int = 180,
    ) -> str:
        text = re.sub(
            r"\s+",
            " ",
            str(value or "").strip(),
        )
        if len(text) <= maximum:
            return text
        return text[: maximum - 1].rstrip() + "…"

    @classmethod
    def _coverage_rows(
        cls,
        direct: Any,
        partial: Any,
    ) -> str:
        rows: list[str] = []

        def append_rows(
            values: Any,
            badge: str,
            css_class: str,
        ) -> None:
            if not isinstance(values, list):
                return

            for value in values[:4]:
                if isinstance(value, dict):
                    name = cls._clean(
                        value.get("testcase")
                    )
                    reason = cls._short(
                        value.get("coverage_reason"),
                        90,
                    )
                else:
                    name = cls._short(value, 90)
                    reason = ""

                if not name and not reason:
                    continue

                rows.append(
                    '<div class="coverage-row">'
                    f'<span class="coverage-badge {css_class}">'
                    f'{cls._escape(badge)}</span>'
                    '<div>'
                    f'<strong>{cls._escape(name or "Scenario")}</strong>'
                    f'<p>{cls._escape(reason)}</p>'
                    '</div>'
                    '</div>'
                )

        append_rows(direct, "Direct", "direct")
        append_rows(partial, "Related", "related")

        if not rows:
            return (
                '<div class="empty-state">'
                'No relevant validation scenario was confirmed.'
                '</div>'
            )

        return "".join(rows)

    @classmethod
    def _evidence_rows(
        cls,
        values: Any,
        empty: str,
    ) -> str:
        if not isinstance(values, list):
            values = []

        rows = [
            cls._short(value, 115)
            for value in values[:3]
            if cls._clean(value)
        ]

        if not rows:
            return (
                '<div class="empty-state">'
                f'{cls._escape(empty)}'
                '</div>'
            )

        return "".join(
            '<div class="evidence-row">'
            '<span>✓</span>'
            f'<p>{cls._escape(value)}</p>'
            '</div>'
            for value in rows
        )

    @classmethod
    def _decision_tile(
        cls,
        icon: str,
        title: str,
        item: dict[str, Any],
        selected: bool,
    ) -> str:
        answer = "YES" if selected else "NO"
        status_class = "selected" if selected else "clear"
        reason = cls._short(
            item.get("reason")
            or f"No {title.lower()} evidence was confirmed.",
            95,
        )

        return f"""
        <article class="decision-tile {status_class}">
            <div class="decision-icon">{cls._escape(icon)}</div>
            <div class="decision-copy">
                <span>{cls._escape(title)}</span>
                <strong>{answer}</strong>
                <p>{cls._escape(reason)}</p>
            </div>
        </article>
        """

    @classmethod
    def _build_html(
        cls,
        defect: dict[str, Any],
        defect_analysis: dict[str, Any],
        execution_summary: dict[str, Any],
        execution_logs: dict[str, Any],
        classification: dict[str, Any],
    ) -> str:
        conclusion = cls._clean(
            classification.get("conclusion")
            or classification.get("classification")
        ).upper()

        conclusion_label, accent = cls.LABELS.get(
            conclusion,
            cls.LABELS["INSUFFICIENT_EVIDENCE"],
        )

        coverage = (
            classification.get(
                "coverage_assessment",
                {},
            )
            or {}
        )
        comparison = (
            classification.get(
                "execution_comparison",
                {},
            )
            or {}
        )
        matrix = (
            classification.get(
                "decision_matrix",
                {},
            )
            or {}
        )
        original = (
            classification.get(
                "original_customer_scenario",
                {},
            )
            or {}
        )

        defect_id = cls._clean(defect.get("id"))
        headline = cls._clean(
            defect.get("headline")
            or defect.get("summary")
        )
        component = cls._clean(defect.get("component"))
        status = cls._clean(defect.get("status"))
        severity = cls._clean(defect.get("severity")) or "N/A"

        feature = cls._clean(
            defect_analysis.get("feature")
        )
        root_cause = cls._short(
            defect_analysis.get("analysis_summary"),
            220,
        )
        collection = (
            cls._clean(coverage.get("collection"))
            or cls._clean(
                execution_summary.get("collection")
            )
        )
        host = cls._clean(
            execution_summary.get("host")
        )
        total_tests = int(
            execution_summary.get("total_tests", 0)
            or 0
        )
        completed_tests = int(
            execution_summary.get("completed_tests", 0)
            or 0
        )

        coverage_percentage = max(
            0,
            min(
                int(
                    coverage.get(
                        "coverage_percentage",
                        0,
                    )
                    or 0
                ),
                100,
            ),
        )
        confidence = max(
            0,
            min(
                int(
                    classification.get(
                        "confidence",
                        0,
                    )
                    or 0
                ),
                100,
            ),
        )

        confidence_text = (
            f"{confidence}%"
            if confidence > 0
            else classification.get(
                "confidence_label",
                "Not available",
            )
        )

        coverage_html = cls._coverage_rows(
            coverage.get(
                "directly_covering_testcases"
            ),
            coverage.get(
                "partially_covering_testcases"
            ),
        )

        relevant_evidence = (
            comparison.get(
                "matching_product_evidence"
            )
            if comparison.get(
                "customer_symptom_reproduced"
            )
            else comparison.get(
                "evidence_customer_symptom_not_seen"
            )
        )

        evidence_html = cls._evidence_rows(
            relevant_evidence,
            "No direct customer-symptom evidence was confirmed.",
        )

        selected_key = {
            "REPRODUCED_PRODUCT_ISSUE": "product_issue",
            "AUTOMATION_ISSUE": "automation_issue",
            "ENVIRONMENT_ISSUE": "environment_issue",
            "OUT_OF_SCOPE": "out_of_scope",
        }.get(conclusion)

        decision_html = "".join(
            [
                cls._decision_tile(
                    "📦",
                    "Product Issue",
                    matrix.get("product_issue", {}) or {},
                    selected_key == "product_issue",
                ),
                cls._decision_tile(
                    "🧩",
                    "Script Issue",
                    matrix.get("automation_issue", {}) or {},
                    selected_key == "automation_issue",
                ),
                cls._decision_tile(
                    "🖥",
                    "Environment",
                    matrix.get("environment_issue", {}) or {},
                    selected_key == "environment_issue",
                ),
                cls._decision_tile(
                    "⊘",
                    "Out of Scope",
                    matrix.get("out_of_scope", {}) or {},
                    selected_key == "out_of_scope",
                ),
            ]
        )

        summary = cls._short(
            classification.get(
                "executive_summary"
            )
            or classification.get("summary")
            or classification.get(
                "final_reason"
            ),
            260,
        )
        action = cls._short(
            classification.get(
                "recommended_next_action"
            )
            or classification.get(
                "recommended_action"
            ),
            180,
        )
        trigger = cls._short(
            original.get("trigger"),
            140,
        )
        symptom = cls._short(
            original.get("reported_behavior"),
            140,
        )
        coverage_gap = cls._short(
            coverage.get("coverage_gap"),
            140,
        )

        ist = timezone(
            timedelta(
                hours=5,
                minutes=30,
            )
        )
        generated_at = datetime.now(ist).strftime(
            "%d %b %Y · %I:%M %p IST"
        )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Agentic AI E2E Validation - {cls._escape(defect_id)}</title>
<style>
:root {{
    --bg:#f4f8fd;
    --surface:#ffffff;
    --soft:#f8fbff;
    --line:#dbe7f5;
    --text:#17263d;
    --muted:#687c96;
    --blue:#1769e0;
    --blue-soft:#eaf3ff;
    --green:#179c65;
    --shadow:0 14px 34px rgba(35,78,126,.10);
}}
*{{box-sizing:border-box}}
body{{margin:0;color:var(--text);font-family:Inter,Segoe UI,sans-serif;background:radial-gradient(circle at 0% 0%,rgba(23,105,224,.09),transparent 28%),linear-gradient(180deg,#fbfdff 0%,var(--bg) 100%)}}
.page{{width:min(1080px,calc(100% - 28px));margin:auto;padding:24px 0 34px}}
.header{{display:flex;justify-content:space-between;gap:18px;align-items:center;padding:20px 22px;border:1px solid var(--line);border-radius:20px;background:var(--surface);box-shadow:var(--shadow)}}
.kicker{{color:var(--blue);font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}}
h1{{margin:7px 0 5px;font-size:30px;letter-spacing:-.03em}}.headline{{margin:0;color:var(--muted);font-size:14px;line-height:1.5}}
.status{{padding:9px 13px;border:1px solid #b9d2ef;border-radius:999px;color:var(--blue);background:var(--blue-soft);font-size:13px;font-weight:900;white-space:nowrap}}
.top{{display:grid;grid-template-columns:1.25fr .75fr;gap:12px;margin-top:12px}}
.summary,.metric,.section{{border:1px solid var(--line);background:var(--surface);box-shadow:var(--shadow)}}
.summary{{padding:18px;border-radius:18px}}.label{{color:var(--muted);font-size:10px;font-weight:850;letter-spacing:.07em;text-transform:uppercase}}
.summary strong{{display:block;margin-top:6px;color:var(--blue);font-size:23px}}.summary p{{margin:7px 0 0;color:#415b76;font-size:14px;line-height:1.55}}
.metrics{{display:grid;grid-template-columns:1fr 1fr;gap:9px}}.metric{{display:grid;place-items:center;min-height:118px;border-radius:18px;text-align:center}}
.metric strong{{color:var(--blue);font-size:25px}}.metric span{{display:block;margin-top:4px;color:var(--muted);font-size:11px;font-weight:800;text-transform:uppercase}}
.meta{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:12px}}.meta-card{{padding:11px 12px;border:1px solid var(--line);border-radius:13px;background:var(--surface)}}
.meta-card span{{display:block;color:var(--muted);font-size:10px;font-weight:800;text-transform:uppercase}}.meta-card strong{{display:block;margin-top:4px;font-size:13px;word-break:break-word}}
.section{{margin-top:12px;padding:16px;border-radius:18px}}.section-head{{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:10px}}
.section h2{{margin:0;font-size:16px}}.section-note{{color:var(--muted);font-size:11px}}.decision-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}}
.decision-tile{{padding:12px;border:1px solid var(--line);border-radius:13px;background:var(--soft)}}.decision-tile.selected{{border-color:#8db7e7;background:var(--blue-soft)}}
.decision-icon{{display:none}}.decision-copy span{{color:var(--muted);font-size:10px;text-transform:uppercase;font-weight:850}}
.decision-copy strong{{display:block;margin-top:2px;color:#788ba3;font-size:13px}}.decision-tile.selected .decision-copy strong{{color:var(--blue)}}
.decision-copy p{{margin:3px 0 0;color:#536b84;font-size:11px;line-height:1.4}}.detail-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.detail-card{{padding:13px;border:1px solid var(--line);border-radius:13px;background:var(--soft)}}.detail-card h3{{margin:0 0 6px;font-size:13px}}
.detail-card p{{margin:0;color:#435d77;font-size:12px;line-height:1.5}}.coverage-row{{display:grid;grid-template-columns:54px 1fr;gap:8px;padding:8px 0;border-bottom:1px solid #e7eef7}}
.coverage-row:last-child{{border-bottom:0}}.coverage-badge{{display:inline-grid;place-items:center;height:22px;border-radius:999px;font-size:9px;font-weight:900}}
.coverage-badge.direct{{color:#117a50;background:#e7f8ef}}.coverage-badge.related{{color:#a06b1a;background:#fff5e5}}
.coverage-row strong{{font-size:12px}}.coverage-row p{{margin:2px 0 0;color:var(--muted);font-size:11px;line-height:1.35}}
.evidence-row{{display:grid;grid-template-columns:18px 1fr;gap:8px;margin:7px 0}}.evidence-row span{{display:grid;place-items:center;width:18px;height:18px;border-radius:50%;color:white;background:var(--green);font-size:10px}}
.evidence-row p{{margin:0;color:#425b74;font-size:12px;line-height:1.4}}.empty-state{{color:var(--muted);font-size:11px;line-height:1.4}}
.next-action{{display:flex;justify-content:space-between;gap:16px;align-items:center;padding:14px;border:1px solid #bad1ec;border-radius:14px;background:linear-gradient(135deg,#f2f7ff,#f8fcff)}}
.next-action strong{{font-size:13px}}.next-action p{{margin:4px 0 0;color:#435d77;font-size:12px;line-height:1.45}}.next-action span{{padding:7px 10px;border-radius:999px;color:white;background:var(--blue);font-size:10px;font-weight:900}}
.footer{{margin-top:12px;color:var(--muted);text-align:center;font-size:10px}}
@media(max-width:850px){{.top,.detail-grid{{grid-template-columns:1fr}}.decision-grid{{grid-template-columns:1fr}}.meta{{grid-template-columns:repeat(2,1fr)}}}}
</style>
</head>
<body><main class="page">
<section class="header"><div><span class="kicker">Agentic AI E2E Validation Report</span><h1>{cls._escape(defect_id)}</h1><p class="headline">{cls._escape(headline)}</p></div><div class="status">{cls._escape(conclusion_label)}</div></section>

<section class="top">
<article class="summary"><span class="label">Final engineering decision</span><strong>{cls._escape(conclusion_label)}</strong><p>{cls._escape(summary or "Validation completed and the available evidence was assessed.")}</p></article>
<div class="metrics"><article class="metric"><div><strong>{cls._escape(confidence_text)}</strong><span>AI confidence</span></div></article><article class="metric"><div><strong>{coverage_percentage}%</strong><span>Scenario coverage</span></div></article></div>
</section>

<section class="meta">
<article class="meta-card"><span>Component</span><strong>{cls._escape(component)}</strong></article>
<article class="meta-card"><span>Feature</span><strong>{cls._escape(feature or "Not determined")}</strong></article>
<article class="meta-card"><span>Collection</span><strong>{cls._escape(collection or "Not selected")}</strong></article>
<article class="meta-card"><span>Execution</span><strong>{completed_tests}/{total_tests} completed</strong></article>
<article class="meta-card"><span>Status</span><strong>{cls._escape(status)} · Sev {cls._escape(severity)}</strong></article>
</section>

<section class="section"><div class="section-head"><h2>Issue classification</h2><span class="section-note">One-glance decision</span></div><div class="decision-grid">{decision_html}</div></section>

<section class="section">
<div class="section-head"><h2>Validation summary</h2><span class="section-note">Only decision-driving details</span></div>
<div class="detail-grid">
<div>
<article class="detail-card"><h3>Customer trigger</h3><p>{cls._escape(trigger or "Not available")}</p></article>
<article class="detail-card" style="margin-top:8px"><h3>Reported symptom</h3><p>{cls._escape(symptom or "Not available")}</p></article>
<article class="detail-card" style="margin-top:8px"><h3>Predicted root cause</h3><p>{cls._escape(root_cause or "Not available")}</p></article>
</div>
<div>
<article class="detail-card"><h3>Validation coverage</h3>{coverage_html}</article>
<article class="detail-card" style="margin-top:8px"><h3>Coverage gap</h3><p>{cls._escape(coverage_gap or "No material gap identified.")}</p></article>
</div>
</div>
</section>

<section class="section"><div class="section-head"><h2>Evidence check</h2><span class="section-note">Customer symptom versus execution</span></div><div class="detail-card">{evidence_html}</div></section>

<section class="section"><div class="next-action"><div><strong>Recommended next action</strong><p>{cls._escape(action or "No additional action was generated.")}</p></div><span>{cls._escape(conclusion_label)}</span></div></section>

<div class="footer">Generated {cls._escape(generated_at)}</div>
</main></body></html>"""

    def generate_report(
        self,
        defect: dict[str, Any],
        defect_analysis: dict[str, Any],
        execution_summary: dict[str, Any],
        execution_logs: dict[str, Any],
        classification: dict[str, Any],
    ) -> dict[str, Any]:
        defect_id = self._clean(defect.get("id")) or "Defect"
        conclusion = self._clean(
            classification.get("conclusion")
            or classification.get("classification")
        ) or "E2E_VALIDATION"

        ist = timezone(
            timedelta(
                hours=5,
                minutes=30,
            )
        )
        timestamp = datetime.now(ist).strftime(
            "%Y%m%d_%H%M%S"
        )

        filename = (
            f"{timestamp}_"
            f"{self._safe(defect_id)}_"
            f"{self._safe(conclusion)}.html"
        )
        output_file = self.output_directory / filename

        output_file.write_text(
            self._build_html(
                defect=defect,
                defect_analysis=defect_analysis or {},
                execution_summary=execution_summary or {},
                execution_logs=execution_logs or {},
                classification=classification,
            ),
            encoding="utf-8",
        )

        return {
            "success": True,
            "filename": filename,
            "absolute_path": str(output_file),
            "classification": conclusion,
            "display_name": classification.get("display_name"),
            "generated_at": datetime.now(ist).isoformat(),
            "next_stage": "completed",
        }
