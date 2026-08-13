from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
)

from services.cfd_ai_service import (
    create_cfd_ai_service,
)
from services.cfd_cache_service import (
    CfdCacheService,
)
from services.collection_selection_service import (
    CollectionSelectionService,
)
from services.automation_runner_service import (
    AutomationRunnerService,
)
from services.execution_planner_service import (
    ExecutionPlannerService,
)
from services.execution_monitor_service import (
    ExecutionMonitorService,
)
from services.execution_log_service import (
    ExecutionLogService,
)
from services.issue_classifier_service import (
    IssueClassifierService,
)
from services.html_report_service import (
    HtmlReportService,
)
from services.index_service import (
    AutomationIndexService,
)
from services.recommendation_service import (
    RecommendationService,
)
from services.rocketraccoon_search_service import (
    RocketRaccoonSearchService,
)
from services.repository_service import (
    RepositoryService,
)
from services.suite_parser_service import (
    SuiteParserService,
)
from agentic_workflow.graph import (
    create_agentic_discovery_graph,
)


# ============================================================
# Environment configuration
# ============================================================
base_directory = Path(
    __file__
).resolve().parent

load_dotenv(
    base_directory / ".env"
)

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "development-only-secret",
)

automation_path = os.getenv(
    "AUTOMATION_PATH",
    "",
).strip()


# ============================================================
# Service initialization
# ============================================================
repository_service = RepositoryService(
    automation_path
)

suite_parser_service = SuiteParserService(
    automation_path
)

cfd_cache_service = CfdCacheService(
    cache_file=(
        base_directory
        / "data"
        / "cfd_cache.json"
    ),
    components_file=(
        base_directory
        / "data"
        / "cdets_components.json"
    ),
)

index_file_path = (
    base_directory
    / "data"
    / "automation_index.json"
)

automation_index_service = AutomationIndexService(
    repository_service=repository_service,
    suite_parser_service=suite_parser_service,
    index_file=index_file_path,
)

cfd_ai_service = create_cfd_ai_service()

collection_selection_service = CollectionSelectionService(
    cfd_ai_service=cfd_ai_service,
)

execution_planner_service = ExecutionPlannerService(
    automation_path=automation_path,
)

recommendation_service = RecommendationService(
    index_service=automation_index_service
)


rocketraccoon_search_service = RocketRaccoonSearchService(
    index_service=automation_index_service
)


agentic_discovery_graph = create_agentic_discovery_graph(
    repository_service=repository_service,
    cfd_ai_service=cfd_ai_service,
    rocketraccoon_search_service=rocketraccoon_search_service,
    collection_selection_service=collection_selection_service,
    execution_planner_service=execution_planner_service,
)

automation_runner_service = AutomationRunnerService(
    default_host=os.getenv(
        "AUTOMATION_RUNNER_HOST",
        "10.196.147.237",
    ).strip(),
)


execution_monitor_service = ExecutionMonitorService(
    runner_service=automation_runner_service,
    poll_interval_seconds=int(
        os.getenv(
            "EXECUTION_POLL_INTERVAL_SECONDS",
            "5",
        )
    ),
    default_timeout_seconds=int(
        os.getenv(
            "EXECUTION_TIMEOUT_SECONDS",
            "7200",
        )
    ),
)

execution_log_service = ExecutionLogService(
    runner_service=automation_runner_service,
    output_directory=(
        base_directory
        / "reports"
        / "executions"
    ),
)

issue_classifier_service = IssueClassifierService(
    cfd_ai_service=cfd_ai_service,
)

html_report_service = HtmlReportService(
    output_directory=(
        base_directory
        / "reports"
        / "html"
    ),
)

e2e_jobs: dict[str, dict] = {}
e2e_jobs_lock = threading.RLock()


# ============================================================
# Main page and health APIs
# ============================================================
@app.get("/")
@app.get("/agentic-dashboard")
def agentic_dashboard():
    return render_template(
        "agentic_dashboard.html",
    )


@app.get("/api/health")
def health():
    return jsonify(
        {
            "success": True,
            "service": "E2E-AI",
            "status": "running",
        }
    )


# ============================================================
# Repository APIs
# ============================================================
@app.get("/api/repository/status")
def repository_status():
    """
    Verify whether the local automation repository is available.

    Returns:
        - Automation path
        - Total Python files
        - Total suite files
        - Available component folders
    """
    try:
        result = repository_service.repository_status()

        return jsonify(
            {
                "success": result["available"],
                "repository": result,
            }
        )

    except Exception as exc:
        app.logger.exception(
            "Repository scan failed"
        )

        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500


@app.get("/api/repository/suites")
def repository_suites():
    """
    Return automation suites.

    Optional query parameters:
        component
        search
    """
    try:
        product = request.args.get(
            "product",
            "",
        ).strip()

        component = request.args.get(
            "component",
            "",
        ).strip()

        search_text = request.args.get(
            "search",
            "",
        ).strip()

        suites = repository_service.list_suites(
            component=component,
            search_text=search_text,
        )

        return jsonify(
            {
                "success": True,
                "total": len(suites),
                "suites": suites,
            }
        )

    except Exception as exc:
        app.logger.exception(
            "Suite listing failed"
        )

        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500


@app.get("/api/repository/suite-details")
def repository_suite_details():
    """
    Read basic Python details from one suite file.

    Returns:
        - Suite name
        - Component
        - File path
        - Classes
        - Functions
        - Imports
    """
    try:
        relative_path = request.args.get(
            "path",
            "",
        ).strip()

        if not relative_path:
            return jsonify(
                {
                    "success": False,
                    "message": "Suite path is required.",
                }
            ), 400

        details = repository_service.suite_details(
            relative_path
        )

        return jsonify(
            {
                "success": True,
                "suite": details,
            }
        )

    except FileNotFoundError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 404

    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400

    except Exception as exc:
        app.logger.exception(
            "Suite-detail extraction failed"
        )

        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500


@app.get("/api/repository/parse-suite")
def parse_suite():
    """
    Parse a suite and discover:

        Suite
          -> Parent suites
          -> Collections
          -> Collection implementation files
          -> Test methods
          -> Test descriptions
    """
    try:
        relative_path = request.args.get(
            "path",
            "",
        ).strip()

        if not relative_path:
            return jsonify(
                {
                    "success": False,
                    "message": "Suite path is required.",
                }
            ), 400

        result = suite_parser_service.parse_suite(
            relative_path
        )

        return jsonify(
            {
                "success": True,
                "suite": result,
            }
        )

    except FileNotFoundError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 404

    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400

    except SyntaxError as exc:
        return jsonify(
            {
                "success": False,
                "message": (
                    "Suite file could not be parsed: "
                    f"{exc}"
                ),
            }
        ), 400

    except Exception as exc:
        app.logger.exception(
            "Suite parsing failed"
        )

        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500


# ============================================================
# Automation index APIs
# ============================================================
@app.post("/api/index/build")
def build_automation_index():
    """
    Start automation-index creation in a background thread.

    Input:
        {
            "component": "NVM"
        }

    Empty component means all components.
    """
    try:
        data = request.get_json(
            silent=True
        ) or {}

        component = str(
            data.get(
                "component",
                "",
            )
        ).strip()

        if automation_index_service.is_building():
            return jsonify(
                {
                    "success": False,
                    "message": (
                        "Automation index creation "
                        "is already running."
                    ),
                    "status": (
                        automation_index_service
                        .get_status()
                    ),
                }
            ), 409

        def background_build() -> None:
            try:
                automation_index_service.build_index(
                    component=component
                )

            except Exception:
                app.logger.exception(
                    "Background index creation failed"
                )

        worker = threading.Thread(
            target=background_build,
            daemon=True,
            name="automation-index-builder",
        )

        worker.start()

        return jsonify(
            {
                "success": True,
                "message": (
                    "Automation index creation started."
                ),
                "status": (
                    automation_index_service
                    .get_status()
                ),
            }
        ), 202

    except Exception as exc:
        app.logger.exception(
            "Could not start index creation"
        )

        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500


@app.get("/api/index/status")
def automation_index_status():
    """
    Return current background index-building status.

    The UI calls this API repeatedly to update progress.
    """
    try:
        return jsonify(
            {
                "success": True,
                "status": (
                    automation_index_service
                    .get_status()
                ),
            }
        )

    except Exception as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500


@app.get("/api/index/search")
def search_automation_index():
    """
    Perform the original keyword-based index search.

    Query parameters:
        q
        component
        limit
    """
    try:
        query = request.args.get(
            "q",
            "",
        ).strip()

        component = request.args.get(
            "component",
            "",
        ).strip()

        limit_value = request.args.get(
            "limit",
            "100",
        ).strip()

        try:
            limit = int(limit_value)

        except ValueError:
            limit = 100

        result = automation_index_service.search(
            query=query,
            component=component,
            limit=limit,
        )

        return jsonify(
            {
                "success": True,
                **result,
            }
        )

    except FileNotFoundError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 404

    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400

    except Exception as exc:
        app.logger.exception(
            "Automation index search failed"
        )

        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500


# ============================================================
# Agentic workflow - Stage 2: LangGraph defect/RCA analysis
# ============================================================
@app.post("/api/agentic/analyze-defect")
def agentic_analyze_defect():
    """
    Agentic AI backend flow.

    Current LangGraph stages:
        1. Load repository context.
        2. Analyze defect using Cisco AI.
        3. Search RocketRaccoon Sanity.
        4. Fall back to RocketRaccoon Regression when needed.

    No suite is triggered.
    """
    try:
        data = request.get_json(
            silent=True
        ) or {}

        defect_text = str(
            data.get(
                "defect_text",
                "",
            )
            or ""
        ).strip()

        selected_component = str(
            data.get(
                "component",
                "",
            )
            or ""
        ).strip()

        if not defect_text:
            return jsonify(
                {
                    "success": False,
                    "message": (
                        "Defect description is required."
                    ),
                }
            ), 400

        initial_state = {
            "defect_text": defect_text,
            "selected_component": selected_component,
            "repository_components": [],
            "analysis": {},
            "suite_search_result": {},
            "selected_suite_type": "",
            "fallback_used": False,
            "recommendations": [],
            "collection_selection": {},
            "execution_plan": {},
            "workflow_status": "started",
        }

        result = agentic_discovery_graph.invoke(
            initial_state
        )

        return jsonify(
            {
                "success": True,
                "stage": "collection_execution_planning",
                "workflow_status": result.get(
                    "workflow_status",
                    "",
                ),
                "message": (
                    "LangGraph analysis, Regression collection "
                    "selection and execution planning completed. "
                    "Nothing was triggered yet."
                ),
                "defect_text": defect_text,
                "selected_component": selected_component,
                "analysis": result.get(
                    "analysis",
                    {},
                ),
                "selected_suite_type": result.get(
                    "selected_suite_type",
                    "",
                ),
                "fallback_used": result.get(
                    "fallback_used",
                    False,
                ),
                "recommendations": result.get(
                    "recommendations",
                    [],
                ),
                "suite_search_result": result.get(
                    "suite_search_result",
                    {},
                ),
                "collection_selection": result.get(
                    "collection_selection",
                    {},
                ),
                "execution_plan": result.get(
                    "execution_plan",
                    {},
                ),
                "next_stage": (
                    "automatic_runner_trigger"
                ),
            }
        )

    except FileNotFoundError as exc:
        return jsonify(
            {
                "success": False,
                "message": (
                    "Automation index is unavailable. "
                    f"{exc}"
                ),
            }
        ), 404

    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400

    except RuntimeError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500

    except Exception as exc:
        app.logger.exception(
            "Agentic suite discovery failed"
        )

        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500




# ============================================================
# AI defect analysis and recommendation API
# ============================================================
@app.post("/api/ai/recommend-tests")
def recommend_tests_for_defect():
    """
    Analyze a defect using AI and recommend related automation tests.

    Input example:
        {
            "defect_text": "NVM browser plugin is not reporting...",
            "component": "NVM"
        }

    Process:
        1. AI understands the defect.
        2. AI extracts component, feature and technical terms.
        3. A focused search query is generated.
        4. The local automation index is searched.
        5. Candidates are reranked.
        6. Repeated test cases from different suites are grouped.
        7. Top recommendations are returned.
    """
    try:
        data = request.get_json(
            silent=True
        ) or {}

        defect_text = str(
            data.get(
                "defect_text",
                "",
            )
        ).strip()

        selected_component = str(
            data.get(
                "component",
                "",
            )
        ).strip()

        if not defect_text:
            return jsonify(
                {
                    "success": False,
                    "message": (
                        "Defect description is required."
                    ),
                }
            ), 400

        # Get valid component folders from the repository.
        repository_result = (
            repository_service.repository_status()
        )

        if not repository_result.get(
            "available",
            False,
        ):
            return jsonify(
                {
                    "success": False,
                    "message": (
                        repository_result.get(
                            "message",
                            "Automation repository is unavailable.",
                        )
                    ),
                }
            ), 400

        repository_components = (
            repository_result.get(
                "components",
                [],
            )
        )

        # Step 1:
        # Ask AI to understand and structure the defect.
        analysis = cfd_ai_service.analyze(
            defect_text=defect_text,
            selected_component=selected_component,
            repository_components=repository_components,
        )

        try:
            recommendation_limit = int(
                os.getenv(
                    "AI_RECOMMENDATION_LIMIT",
                    "10",
                )
            )

        except ValueError:
            recommendation_limit = 10

        try:
            candidate_limit = int(
                os.getenv(
                    "AI_INDEX_CANDIDATE_LIMIT",
                    "100",
                )
            )

        except ValueError:
            candidate_limit = 100

        # Step 2:
        # Search and rerank automation tests.
        recommendation_result = (
            recommendation_service.recommend(
                analysis=analysis,
                selected_component=selected_component,
                limit=recommendation_limit,
                candidate_limit=candidate_limit,
            )
        )

        recommendations = (
            recommendation_result.get(
                "recommendations",
                [],
            )
        )

        high_confidence_count = sum(
            1
            for recommendation in recommendations
            if int(
                recommendation.get(
                    "score",
                    0,
                )
                or 0
            ) >= 75
        )

        coverage_gap = (
            len(recommendations) == 0
            or high_confidence_count == 0
        )

        if not recommendations:
            recommendation_message = (
                "No related automation tests were found."
            )

        elif coverage_gap:
            recommendation_message = (
                "Only weak or indirect automation matches "
                "were found. Manual review is required."
            )

        else:
            recommendation_message = (
                "Related automation tests were identified."
            )

        return jsonify(
            {
                "success": True,
                "message": recommendation_message,
                "coverage_gap": coverage_gap,
                "high_confidence_count": (
                    high_confidence_count
                ),
                "analysis": analysis,
                **recommendation_result,
            }
        )

    except FileNotFoundError as exc:
        return jsonify(
            {
                "success": False,
                "message": (
                    "Automation index is not available. "
                    f"{exc}"
                ),
            }
        ), 404

    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400

    except RuntimeError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500

    except Exception as exc:
        app.logger.exception(
            "AI test recommendation failed"
        )

        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500



# ============================================================
# Windows automation runner APIs
# ============================================================
@app.post("/api/runner/trigger")
def trigger_collection_execution():
    """
    Trigger one selected collection on the Windows POC machine.

    Input:
        {
            "host": "10.196.147.237",
            "collection": "SWGFeatureTestTND"
        }
    """
    try:
        data = request.get_json(silent=True) or {}

        host = str(
            data.get(
                "host",
                automation_runner_service.default_host,
            )
            or automation_runner_service.default_host
        ).strip()

        collection = str(
            data.get(
                "collection",
                "",
            )
            or ""
        ).strip()

        if not collection:
            return jsonify(
                {
                    "success": False,
                    "message": "Collection name is required.",
                }
            ), 400

        result = automation_runner_service.start_collection(
            host=host,
            collection=collection,
        )

        return jsonify(
            {
                "success": True,
                "message": "Collection execution started.",
                "runner": result,
            }
        ), 202

    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400

    except RuntimeError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 409

    except Exception as exc:
        app.logger.exception(
            "Collection trigger failed"
        )

        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500


@app.get("/api/runner/status/<session_id>")
def get_runner_session_status(session_id: str):
    """
    Return live status for a collection execution session.
    """
    result = automation_runner_service.get_session(
        session_id
    )

    if result is None:
        return jsonify(
            {
                "success": False,
                "message": "Runner session was not found.",
            }
        ), 404

    return jsonify(
        {
            "success": True,
            "runner": result,
        }
    )


@app.post("/api/runner/cancel/<session_id>")
def cancel_runner_session(session_id: str):
    """
    Cancel a running collection execution.
    """
    try:
        result = automation_runner_service.cancel_session(
            session_id
        )

        return jsonify(
            {
                "success": True,
                "message": "Cancellation requested.",
                "runner": result,
            }
        )

    except KeyError:
        return jsonify(
            {
                "success": False,
                "message": "Runner session was not found.",
            }
        ), 404

@app.get("/api/runner/testcase-details")
def get_runner_testcase_details():
    """
    Fetch complete testcase data from CiscoAutomationRunner.

    Query parameters:
        host
        testcase
    """
    try:
        host = str(
            request.args.get(
                "host",
                automation_runner_service.default_host,
            )
            or automation_runner_service.default_host
        ).strip()

        testcase = str(
            request.args.get(
                "testcase",
                "",
            )
            or ""
        ).strip()

        if not testcase:
            return jsonify(
                {
                    "success": False,
                    "message": (
                        "Testcase name is required."
                    ),
                }
            ), 400

        result = (
            automation_runner_service
            .get_testcase_details(
                host=host,
                testcase=testcase,
            )
        )

        return jsonify(
            {
                "success": True,
                "testcase_details": result,
            }
        )

    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400

    except RuntimeError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500

    except Exception as exc:
        app.logger.exception(
            "Testcase detail retrieval failed"
        )

        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500


# ============================================================
# Cached CFD APIs
# ============================================================
@app.get("/api/cfds/products")
def get_cfd_products():
    try:
        products = (
            cfd_cache_service
            .get_products()
        )

        return jsonify({
            "success": True,
            "total": len(products),
            "products": products,
        })

    except Exception as exc:
        app.logger.exception(
            "Could not load CFD products"
        )

        return jsonify({
            "success": False,
            "message": str(exc),
        }), 500


@app.get("/api/cfds/components")
def get_cfd_components():
    try:
        product = request.args.get(
            "product",
            "",
        ).strip()

        components = (
            cfd_cache_service
            .get_components(
                product=product,
            )
        )

        return jsonify({
            "success": True,
            "product": product,
            "total": len(components),
            "components": components,
        })

    except Exception as exc:
        app.logger.exception(
            "Could not load CFD components"
        )

        return jsonify({
            "success": False,
            "message": str(exc),
        }), 500


@app.get("/api/cfds/metadata")
def get_cfd_cache_metadata():
    try:
        metadata = cfd_cache_service.get_metadata()

        return jsonify({
            "success": True,
            "metadata": metadata,
        })

    except Exception as exc:
        app.logger.exception(
            "Could not load CFD cache metadata"
        )

        return jsonify({
            "success": False,
            "message": str(exc),
        }), 500


@app.get("/api/cfds/search")
def search_cached_cfds():
    try:
        product = request.args.get(
            "product",
            "",
        ).strip()

        component = request.args.get(
            "component",
            "",
        ).strip()

        from_date = request.args.get(
            "from",
            "",
        ).strip()

        to_date = request.args.get(
            "to",
            "",
        ).strip()

        query_text = request.args.get(
            "q",
            "",
        ).strip()

        try:
            limit = int(
                request.args.get(
                    "limit",
                    500,
                )
            )
        except (TypeError, ValueError):
            limit = 500

        result = cfd_cache_service.search(
            product=product,
            component=component,
            from_date=from_date,
            to_date=to_date,
            text=query_text,
            limit=limit,
        )

        return jsonify({
            "success": True,
            **result,
        })

    except ValueError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400

    except Exception as exc:
        app.logger.exception(
            "Cached CFD search failed"
        )

        return jsonify({
            "success": False,
            "message": str(exc),
        }), 500


@app.get("/api/cfds/<defect_id>")
def get_cached_cfd(defect_id: str):
    try:
        defect = cfd_cache_service.get_defect(
            defect_id
        )

        return jsonify({
            "success": True,
            "defect": defect,
        })

    except FileNotFoundError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 404

    except ValueError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400

    except Exception as exc:
        app.logger.exception(
            "Could not load cached CFD"
        )

        return jsonify({
            "success": False,
            "message": str(exc),
        }), 500


# ============================================================
# Complete end-to-end Agentic workflow
# ============================================================
def _utc_timestamp() -> str:
    """
    Return an offset-aware India Standard Time timestamp.

    A fixed UTC+05:30 offset is used so this works on Windows even when
    the optional IANA timezone database is not installed.
    """
    ist = timezone(
        timedelta(
            hours=5,
            minutes=30,
        )
    )

    return datetime.now(ist).isoformat()


def _update_e2e_job(
    job_id: str,
    **values,
) -> None:
    with e2e_jobs_lock:
        if job_id not in e2e_jobs:
            return

        e2e_jobs[job_id].update(values)
        e2e_jobs[job_id]["updated_at"] = (
            _utc_timestamp()
        )


def _append_e2e_log(
    job_id: str,
    message: str,
    level: str = "success",
) -> None:
    entry = {
        "time": _utc_timestamp(),
        "level": str(level or "success"),
        "message": str(message or "").strip(),
    }

    with e2e_jobs_lock:
        if job_id not in e2e_jobs:
            return

        e2e_jobs[job_id].setdefault(
            "activity_logs",
            [],
        ).append(entry)

        e2e_jobs[job_id]["updated_at"] = (
            _utc_timestamp()
        )


def _get_e2e_job_copy(
    job_id: str,
) -> dict | None:
    with e2e_jobs_lock:
        job = e2e_jobs.get(job_id)

        if job is None:
            return None

        return {
            **job,
            "activity_logs": list(
                job.get(
                    "activity_logs",
                    [],
                )
            ),
        }



def _build_collection_queue(
    collection_selection: dict,
    execution_plan: dict,
) -> list[dict]:
    """
    Build an ordered list of relevant Regression collections.

    The best collection remains first. Other discovered collections are
    executed sequentially when their score is close enough to the best match.
    """
    selected = (
        collection_selection.get(
            "selected_collection",
            {},
        )
        or {}
    )
    candidates = (
        collection_selection.get(
            "candidates",
            [],
        )
        or []
    )

    selected_name = str(
        selected.get(
            "collection_name",
            "",
        )
        or execution_plan.get(
            "runner_suite_name",
            "",
        )
        or ""
    ).strip()

    maximum_score = 0
    for item in candidates:
        if not isinstance(item, dict):
            continue
        try:
            maximum_score = max(
                maximum_score,
                int(
                    item.get(
                        "maximum_score",
                        0,
                    )
                    or 0
                ),
            )
        except (TypeError, ValueError):
            continue

    score_delta = int(
        os.getenv(
            "E2E_COLLECTION_SCORE_DELTA",
            "15",
        )
        or 15
    )
    minimum_score = max(
        0,
        maximum_score - score_delta,
    )
    maximum_collections = max(
        1,
        int(
            os.getenv(
                "E2E_MAX_COLLECTIONS",
                "3",
            )
            or 3
        ),
    )

    queue: list[dict] = []
    seen: set[str] = set()

    def add_collection(
        name: str,
        item: dict | None = None,
        selected_collection: bool = False,
    ) -> None:
        clean_name = str(
            name or ""
        ).strip()

        key = clean_name.lower()
        if not clean_name or key in seen:
            return

        payload = dict(item or {})
        payload["collection_name"] = clean_name
        payload["selected_collection"] = selected_collection
        queue.append(payload)
        seen.add(key)

    add_collection(
        selected_name,
        selected,
        True,
    )

    ordered_candidates = sorted(
        (
            item
            for item in candidates
            if isinstance(item, dict)
        ),
        key=lambda item: (
            -int(
                item.get(
                    "maximum_score",
                    0,
                )
                or 0
            ),
            str(
                item.get(
                    "collection_name",
                    "",
                )
            ).lower(),
        ),
    )

    for item in ordered_candidates:
        try:
            score = int(
                item.get(
                    "maximum_score",
                    0,
                )
                or 0
            )
        except (TypeError, ValueError):
            score = 0

        name = str(
            item.get(
                "collection_name",
                "",
            )
            or ""
        ).strip()

        if not name:
            continue

        if score < minimum_score and name.lower() != selected_name.lower():
            continue

        add_collection(
            name,
            item,
            name.lower() == selected_name.lower(),
        )

        if len(queue) >= maximum_collections:
            break

    return queue[:maximum_collections]


def _release_runner_between_collections(
    job_id: str,
    host: str,
    completed_collection: str,
    has_next_collection: bool,
) -> dict:
    """
    Store the completed collection evidence, release the runner, and verify
    that the host is ready before the next collection is loaded.
    """
    if has_next_collection:
        _append_e2e_log(
            job_id,
            (
                f"Evidence from {completed_collection} stored. "
                "Finalizing and releasing the execution target before "
                "the next collection."
            ),
            "info",
        )
    else:
        _append_e2e_log(
            job_id,
            (
                f"Evidence from {completed_collection} stored. "
                "Releasing the execution target."
            ),
            "info",
        )

    release_result = (
        automation_runner_service
        .release_host(
            host=host,
            timeout=int(
                os.getenv(
                    "E2E_RUNNER_RELEASE_TIMEOUT_SECONDS",
                    "120",
                )
                or 90
            ),
            retry_count=int(
                os.getenv(
                    "E2E_RUNNER_RELEASE_RETRIES",
                    "12",
                )
                or 3
            ),
        )
    )

    _append_e2e_log(
        job_id,
        "Execution target released successfully.",
    )

    if has_next_collection:
        _append_e2e_log(
            job_id,
            "Execution target is ready. Loading the next matched collection.",
            "info",
        )

    return release_result


def _aggregate_collection_results(
    collection_runs: list[dict],
) -> tuple[dict, dict]:
    """
    Merge sequential collection results into one classifier input.
    """
    summaries: list[dict] = []
    all_testcases: list[dict] = []
    all_evidence_testcases: list[dict] = []
    total_tests = 0
    completed_tests = 0
    collection_names: list[str] = []
    hosts: list[str] = []

    for run in collection_runs:
        if not isinstance(run, dict):
            continue

        summary = run.get(
            "execution_summary",
            {},
        ) or {}
        logs = run.get(
            "execution_logs",
            {},
        ) or {}

        summaries.append(summary)

        collection_name = str(
            run.get(
                "collection_name",
                "",
            )
            or summary.get(
                "collection",
                "",
            )
            or ""
        ).strip()

        if (
            collection_name
            and collection_name not in collection_names
        ):
            collection_names.append(
                collection_name
            )

        host = str(
            summary.get(
                "host",
                "",
            )
            or ""
        ).strip()

        if host and host not in hosts:
            hosts.append(host)

        total_tests += int(
            summary.get(
                "total_tests",
                0,
            )
            or 0
        )
        completed_tests += int(
            summary.get(
                "completed_tests",
                0,
            )
            or 0
        )

        source_testcases = logs.get(
            "testcases",
            [],
        ) or []
        for testcase in source_testcases:
            if isinstance(testcase, dict):
                enriched = dict(testcase)
                enriched.setdefault(
                    "collection",
                    collection_name,
                )
                all_testcases.append(
                    enriched
                )

        ai_source = logs.get(
            "ai_evidence",
            {},
        ) or {}
        ai_testcases = ai_source.get(
            "testcases",
            [],
        ) or []

        for testcase in ai_testcases:
            if isinstance(testcase, dict):
                enriched = dict(testcase)
                enriched.setdefault(
                    "collection",
                    collection_name,
                )
                all_evidence_testcases.append(
                    enriched
                )

    aggregate_summary = {
        "collection": ", ".join(
            collection_names
        ),
        "collections": collection_names,
        "host": ", ".join(hosts),
        "hosts": hosts,
        "state": "COMPLETED",
        "total_tests": total_tests,
        "completed_tests": completed_tests,
        "collection_count": len(collection_names),
        "collection_summaries": summaries,
    }

    aggregate_logs = {
        "collection_count": len(collection_names),
        "collections": collection_names,
        "collected_testcases": len(
            all_evidence_testcases
            or all_testcases
        ),
        "testcases": all_testcases,
        "ai_evidence": {
            "testcases": (
                all_evidence_testcases
                or all_testcases
            ),
        },
    }

    return (
        aggregate_summary,
        aggregate_logs,
    )


def _run_e2e_post_execution(
    job_id: str,
) -> None:
    """
    Execute relevant Regression collections sequentially.

    For every collection:
    1. Execute the complete collection.
    2. Collect and store its testcase evidence.
    3. Release the Runner and wait for a stable IDLE state.
    4. Load the next collection only after release succeeds.

    Classification is performed once using evidence from all successful
    collection runs.
    """
    try:
        job = _get_e2e_job_copy(
            job_id
        )

        if not job:
            return

        collection_queue = (
            job.get(
                "collection_queue",
                [],
            )
            or []
        )

        if not collection_queue:
            collection_queue = [
                {
                    "collection_name": (
                        job.get(
                            "execution_plan",
                            {},
                        ).get(
                            "runner_suite_name",
                            "",
                        )
                    ),
                    "selected_collection": True,
                }
            ]

        host = str(
            job.get(
                "execution_plan",
                {},
            ).get(
                "host",
                automation_runner_service.default_host,
            )
            or automation_runner_service.default_host
        ).strip()

        collection_runs: list[dict] = []
        failed_collection_runs: list[dict] = []

        for index, collection_item in enumerate(
            collection_queue
        ):
            collection_name = str(
                collection_item.get(
                    "collection_name",
                    "",
                )
                or ""
            ).strip()

            if not collection_name:
                continue

            run_number = index + 1
            total_collections = len(
                collection_queue
            )
            has_next_collection = (
                run_number < total_collections
            )

            # First collection was already started by /api/e2e/start.
            if index == 0:
                current_job = (
                    _get_e2e_job_copy(
                        job_id
                    )
                    or {}
                )
                session_id = str(
                    current_job.get(
                        "runner_session_id",
                        "",
                    )
                    or ""
                ).strip()
                runner = (
                    current_job.get(
                        "runner",
                        {},
                    )
                    or {}
                )
            else:
                _append_e2e_log(
                    job_id,
                    (
                        f"Starting validation collection "
                        f"{run_number} of {total_collections}: "
                        f"{collection_name}."
                    ),
                    "info",
                )

                # Reset duplicate suppression for the new collection.
                # Keep the previous current testcase only until a new one
                # starts so a temporary blank snapshot cannot duplicate logs.
                _update_e2e_job(
                    job_id,
                    _last_logged_collection="",
                    _last_logged_machine_state="",
                    _last_logged_runner_state="",
                    _last_logged_completed_tests=-1,
                )

                runner = (
                    automation_runner_service
                    .start_collection(
                        host=host,
                        collection=collection_name,
                    )
                )

                session_id = str(
                    runner.get(
                        "session_id",
                        "",
                    )
                    or ""
                ).strip()

                if not session_id:
                    raise RuntimeError(
                        f"Runner did not return a session ID for "
                        f"{collection_name}."
                    )

                _update_e2e_job(
                    job_id,
                    runner_session_id=session_id,
                    runner=runner,
                )

                _publish_runner_activity(
                    job_id,
                    runner,
                )

            if not session_id:
                raise RuntimeError(
                    f"Runner session ID is missing for "
                    f"{collection_name}."
                )

            base_progress = 38 + int(
                index
                * 30
                / max(
                    total_collections,
                    1,
                )
            )

            _update_e2e_job(
                job_id,
                stage="execution_monitoring",
                workflow_status="running",
                current_collection_index=index,
                current_collection=collection_name,
                progress_percentage=base_progress,
                progress_label=(
                    f"Executing collection "
                    f"{run_number} of {total_collections}"
                ),
            )

            if index == 0:
                _append_e2e_log(
                    job_id,
                    (
                        f"Executing validation collection "
                        f"{run_number} of {total_collections}: "
                        f"{collection_name}."
                    ),
                )

            execution_snapshot = (
                execution_monitor_service
                .wait_for_completion(
                    session_id=session_id,
                )
            )

            _update_e2e_job(
                job_id,
                runner=execution_snapshot,
            )

            _publish_runner_activity(
                job_id,
                execution_snapshot,
            )

            runner_state = str(
                execution_snapshot.get(
                    "state",
                    "",
                )
                or ""
            ).strip().upper()

            total_tests = int(
                execution_snapshot.get(
                    "total_tests",
                    0,
                )
                or 0
            )

            completed_tests = int(
                execution_snapshot.get(
                    "completed_tests",
                    0,
                )
                or 0
            )

            if runner_state == "MONITOR_TIMEOUT":
                raise RuntimeError(
                    execution_snapshot.get(
                        "error"
                    )
                    or (
                        "Execution monitoring timed out for "
                        f"{collection_name}."
                    )
                )

            fully_executed = (
                total_tests > 0
                and completed_tests >= total_tests
            )

            if (
                not fully_executed
                and (
                    runner_state in {
                        "FAILED",
                        "CANCELLED",
                    }
                    or total_tests <= 0
                )
            ):
                failure_reason = str(
                    execution_snapshot.get(
                        "error",
                        "",
                    )
                    or execution_snapshot.get(
                        "message",
                        "",
                    )
                    or "Collection did not execute."
                ).strip()

                failed_collection_runs.append(
                    {
                        "collection_name": collection_name,
                        "state": runner_state or "FAILED",
                        "reason": failure_reason,
                    }
                )

                _append_e2e_log(
                    job_id,
                    (
                        f"Collection {collection_name} could not be "
                        f"executed: {failure_reason}"
                    ),
                    "warning",
                )

                # Release even when loading/execution fails so another
                # matched collection can still be attempted.
                _release_runner_between_collections(
                    job_id=job_id,
                    host=host,
                    completed_collection=collection_name,
                    has_next_collection=has_next_collection,
                )
                continue

            if (
                runner_state == "FAILED"
                and fully_executed
            ):
                _append_e2e_log(
                    job_id,
                    (
                        f"Runner reported a final state of Failed for "
                        f"{collection_name}, but all {total_tests} "
                        "testcases completed. Preserving the execution "
                        "and continuing with evidence collection."
                    ),
                    "warning",
                )

            _append_e2e_log(
                job_id,
                (
                    f"Collection {collection_name} completed: "
                    f"{completed_tests}/{total_tests} "
                    "testcases finished."
                ),
            )

            execution_summary = (
                execution_monitor_service
                .build_execution_summary(
                    session_id=session_id,
                )
            )

            _update_e2e_job(
                job_id,
                stage="testcase_log_collection",
                progress_percentage=min(
                    82,
                    base_progress + 12,
                ),
                progress_label=(
                    f"Collecting evidence from "
                    f"{collection_name}"
                ),
            )

            _append_e2e_log(
                job_id,
                (
                    f"Collecting testcase documentation and "
                    f"execution evidence from {collection_name}."
                ),
                "info",
            )

            try:
                execution_logs = (
                    execution_log_service
                    .collect_execution_logs(
                        execution_summary
                    )
                )
            except Exception as exc:
                # Preserve the completed execution summary even if some
                # testcase-detail calls fail.
                execution_logs = {
                    "collection": collection_name,
                    "testcases": [],
                    "ai_evidence": {
                        "testcases": [],
                    },
                    "collection_error": str(exc),
                }

                _append_e2e_log(
                    job_id,
                    (
                        f"Execution completed for {collection_name}, "
                        "but some detailed testcase evidence could not "
                        f"be collected: {exc}"
                    ),
                    "warning",
                )

            collection_runs.append(
                {
                    "collection_name": collection_name,
                    "collection_metadata": collection_item,
                    "session_id": session_id,
                    "runner": execution_snapshot,
                    "execution_summary": execution_summary,
                    "execution_logs": execution_logs,
                }
            )

            _update_e2e_job(
                job_id,
                collection_runs=[
                    {
                        key: value
                        for key, value in run.items()
                        if key != "execution_logs"
                    }
                    for run in collection_runs
                ],
                failed_collection_runs=failed_collection_runs,
                completed_collections=len(
                    collection_runs
                ),
            )

            # This is the required release step.
            _release_runner_between_collections(
                job_id=job_id,
                host=host,
                completed_collection=collection_name,
                has_next_collection=has_next_collection,
            )

        if not collection_runs:
            failure_summary = "; ".join(
                (
                    f"{item.get('collection_name')}: "
                    f"{item.get('reason')}"
                )
                for item in failed_collection_runs
            )

            raise RuntimeError(
                "None of the matched validation collections completed."
                + (
                    f" {failure_summary}"
                    if failure_summary
                    else ""
                )
            )

        (
            aggregate_summary,
            aggregate_logs,
        ) = _aggregate_collection_results(
            collection_runs
        )

        aggregate_summary[
            "failed_collections"
        ] = failed_collection_runs
        aggregate_logs[
            "failed_collections"
        ] = failed_collection_runs

        _update_e2e_job(
            job_id,
            execution_summary=aggregate_summary,
            execution_logs={
                key: value
                for key, value
                in aggregate_logs.items()
                if key not in {
                    "testcases",
                    "ai_evidence",
                }
            },
            collected_testcases=(
                aggregate_logs.get(
                    "collected_testcases",
                    0,
                )
            ),
            stage="issue_classification",
            progress_percentage=90,
            progress_label=(
                "Comparing evidence from all completed collections"
            ),
        )

        _append_e2e_log(
            job_id,
            (
                f"Evidence collected from "
                f"{len(collection_runs)} completed collection(s). "
                "AI is comparing the original customer symptom "
                "with the combined testcase coverage and logs."
            ),
        )

        current_job = (
            _get_e2e_job_copy(
                job_id
            )
            or {}
        )

        classification = (
            issue_classifier_service
            .classify(
                defect=(
                    current_job.get(
                        "defect",
                        {},
                    )
                ),
                defect_analysis=(
                    current_job.get(
                        "analysis",
                        {},
                    )
                ),
                execution_summary=aggregate_summary,
                execution_logs=aggregate_logs,
            )
        )

        _update_e2e_job(
            job_id,
            classification=classification,
            stage="html_report_generation",
            progress_percentage=96,
            progress_label=(
                "Generating final HTML report"
            ),
        )

        _append_e2e_log(
            job_id,
            (
                "AI engineering conclusion: "
                f"{classification.get('display_name', 'Unknown')}."
            ),
        )

        _append_e2e_log(
            job_id,
            "Generating the visual engineering report.",
            "info",
        )

        report = (
            html_report_service
            .generate_report(
                defect=(
                    current_job.get(
                        "defect",
                        {},
                    )
                ),
                defect_analysis=(
                    current_job.get(
                        "analysis",
                        {},
                    )
                ),
                execution_summary=aggregate_summary,
                execution_logs=aggregate_logs,
                classification=classification,
            )
        )

        report["view_url"] = (
            f"/api/e2e/report/{job_id}/view"
        )
        report["download_url"] = (
            f"/api/e2e/report/{job_id}/download"
        )

        _update_e2e_job(
            job_id,
            report=report,
            stage="completed",
            workflow_status="completed",
            progress_percentage=100,
            progress_label="Report generated",
            completed_at=_utc_timestamp(),
        )

        _append_e2e_log(
            job_id,
            "HTML analysis report generated successfully.",
        )

    except Exception as exc:
        app.logger.exception(
            "End-to-end post-execution workflow failed"
        )

        _update_e2e_job(
            job_id,
            stage="failed",
            workflow_status="failed",
            error=str(exc),
            progress_label="Workflow failed",
            completed_at=_utc_timestamp(),
        )

        _append_e2e_log(
            job_id,
            str(exc),
            "error",
        )


@app.post("/api/e2e/start")
def start_complete_e2e_workflow():
    """
    Start the complete CFD-to-report workflow.

    Recommended input:
        {
            "defect_id": "CSCxxxxxx"
        }

    Optional input:
        host
    """
    try:
        data = request.get_json(
            silent=True
        ) or {}

        defect_id = str(
            data.get(
                "defect_id",
                "",
            )
            or ""
        ).strip()

        if not defect_id:
            return jsonify(
                {
                    "success": False,
                    "message": (
                        "defect_id is required."
                    ),
                }
            ), 400

        defect = cfd_cache_service.get_defect(
            defect_id
        )

        component = str(
            defect.get(
                "component",
                "",
            )
            or ""
        ).strip()

        raw_fields = (
            defect.get(
                "raw_fields",
                {},
            )
            or {}
        )

        defect_text = "\n".join(
            [
                f"CFD ID: {defect.get('id', '')}",
                f"Headline: {defect.get('headline', '')}",
                f"Description: {defect.get('description', '')}",
                f"Symptoms: {defect.get('symptoms', '')}",
                f"Conditions: {defect.get('conditions', '')}",
                (
                    "Further Problem Description: "
                    f"{defect.get('further_problem_description', '')}"
                ),
                f"Workarounds: {defect.get('workarounds', '')}",
                f"Platform: {raw_fields.get('Platform', '')}",
                f"Impact: {raw_fields.get('Impact', '')}",
                (
                    "Justification: "
                    f"{raw_fields.get('Justification', '')}"
                ),
                f"Status: {defect.get('status', '')}",
                f"Version: {defect.get('version', '')}",
            ]
        ).strip()

        job_id = str(
            uuid.uuid4()
        )

        job = {
            "job_id": job_id,
            "defect_id": defect_id,
            "defect": defect,
            "component": component,
            "stage": "defect_analysis",
            "workflow_status": "running",
            "progress_percentage": 8,
            "progress_label": (
                "Analyzing customer-found defect"
            ),
            "activity_logs": [],
            "analysis": {},
            "collection_selection": {},
            "execution_plan": {},
            "collection_queue": [],
            "collection_runs": [],
            "failed_collection_runs": [],
            "current_collection_index": 0,
            "current_collection": "",
            "completed_collections": 0,
            "runner_session_id": "",
            "runner": {},
            "execution_summary": {},
            "execution_logs": {},
            "classification": {},
            "report": {},
            "error": "",
            "created_at": _utc_timestamp(),
            "updated_at": _utc_timestamp(),
            "completed_at": "",
        }

        with e2e_jobs_lock:
            e2e_jobs[job_id] = job

        _append_e2e_log(
            job_id,
            f"Selected CFD {defect_id}.",
        )

        _append_e2e_log(
            job_id,
            "AI is analyzing the root cause and affected feature.",
            "info",
        )

        initial_state = {
            "defect_text": defect_text,
            "selected_component": component,
            "repository_components": [],
            "analysis": {},
            "suite_search_result": {},
            "selected_suite_type": "",
            "fallback_used": False,
            "recommendations": [],
            "collection_selection": {},
            "execution_plan": {},
            "workflow_status": "started",
        }

        discovery_result = (
            agentic_discovery_graph
            .invoke(
                initial_state
            )
        )

        analysis = discovery_result.get(
            "analysis",
            {},
        )

        collection_selection = (
            discovery_result.get(
                "collection_selection",
                {},
            )
        )

        execution_plan = (
            discovery_result.get(
                "execution_plan",
                {},
            )
        )

        selected_collection = (
            collection_selection.get(
                "selected_collection",
                {},
            )
            or {}
        )

        collection_queue = _build_collection_queue(
            collection_selection,
            execution_plan,
        )

        collection_name = str(
            selected_collection.get(
                "collection_name",
                "",
            )
            or execution_plan.get(
                "runner_suite_name",
                "",
            )
            or ""
        ).strip()

        if not collection_name:
            raise RuntimeError(
                "No Regression collection was selected."
            )

        if not execution_plan.get(
            "ready_for_runner_trigger",
            False,
        ):
            blockers = (
                execution_plan.get(
                    "validation",
                    {},
                ).get(
                    "blockers",
                    [],
                )
                or []
            )

            raise RuntimeError(
                "Execution plan is not ready. "
                + "; ".join(
                    str(item)
                    for item in blockers
                )
            )

        _update_e2e_job(
            job_id,
            analysis=analysis,
            collection_selection=(
                collection_selection
            ),
            execution_plan=(
                execution_plan
            ),
            collection_queue=collection_queue,
            current_collection=collection_name,
            stage="runner_trigger",
            progress_percentage=32,
            progress_label=(
                "Starting related Regression collection"
            ),
        )

        _append_e2e_log(
            job_id,
            (
                "Affected feature identified: "
                f"{analysis.get('feature', 'Not determined')}."
            ),
        )

        _append_e2e_log(
            job_id,
            (
                f"{len(collection_queue)} relevant Regression "
                "collection(s) identified: "
                + ", ".join(
                    item.get(
                        "collection_name",
                        "",
                    )
                    for item in collection_queue
                )
                + "."
            ),
        )

        host = str(
            data.get(
                "host",
                execution_plan.get(
                    "host",
                    automation_runner_service.default_host,
                ),
            )
            or automation_runner_service.default_host
        ).strip()

        runner = (
            automation_runner_service
            .start_collection(
                host=host,
                collection=collection_name,
            )
        )

        session_id = str(
            runner.get(
                "session_id",
                "",
            )
            or ""
        ).strip()

        if not session_id:
            raise RuntimeError(
                "Runner did not return a session ID."
            )

        _update_e2e_job(
            job_id,
            runner_session_id=session_id,
            runner=runner,
            stage="execution_monitoring",
            progress_percentage=40,
            progress_label=(
                "Executing related Regression tests"
            ),
        )

        _publish_runner_activity(
            job_id,
            runner,
        )

        worker = threading.Thread(
            target=_run_e2e_post_execution,
            args=(job_id,),
            daemon=True,
            name=(
                "e2e-post-execution-"
                f"{job_id[:8]}"
            ),
        )

        worker.start()

        return jsonify(
            {
                "success": True,
                "message": (
                    "End-to-end workflow started."
                ),
                "job": _get_e2e_job_copy(
                    job_id
                ),
                "status_url": (
                    f"/api/e2e/status/{job_id}"
                ),
            }
        ), 202

    except FileNotFoundError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 404

    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400

    except RuntimeError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 409

    except Exception as exc:
        app.logger.exception(
            "Could not start end-to-end workflow"
        )

        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 500



def _publish_runner_activity(
    job_id: str,
    snapshot: dict,
) -> None:
    """
    Convert runner snapshots into user-friendly live workflow messages.
    Duplicate messages are suppressed using values stored in the job.
    """
    job = _get_e2e_job_copy(job_id) or {}

    host = str(
        snapshot.get("host")
        or job.get("execution_plan", {}).get("host")
        or automation_runner_service.default_host
        or ""
    ).strip()

    collection = str(
        snapshot.get("collection")
        or job.get("execution_plan", {}).get("runner_suite_name")
        or ""
    ).strip()

    machine_state = str(
        snapshot.get("machine_state", "")
        or ""
    ).strip()

    runner_state = str(
        snapshot.get("state", "")
        or ""
    ).strip()

    current_test = str(
        snapshot.get("current_test", "")
        or ""
    ).strip()

    completed_tests = int(
        snapshot.get("completed_tests", 0)
        or 0
    )

    total_tests = int(
        snapshot.get("total_tests", 0)
        or 0
    )

    previous_host = str(
        job.get("_last_logged_host", "")
        or ""
    ).strip()

    previous_collection = str(
        job.get("_last_logged_collection", "")
        or ""
    ).strip()

    previous_machine_state = str(
        job.get("_last_logged_machine_state", "")
        or ""
    ).strip()

    previous_runner_state = str(
        job.get("_last_logged_runner_state", "")
        or ""
    ).strip()

    previous_current_test = str(
        job.get("_last_logged_current_test", "")
        or ""
    ).strip()

    previous_completed_raw = job.get(
        "_last_logged_completed_tests",
        -1,
    )

    try:
        previous_completed_tests = int(
            previous_completed_raw
        )
    except (TypeError, ValueError):
        previous_completed_tests = -1

    if host and host != previous_host:
        _append_e2e_log(
            job_id,
            f"Connecting to execution target {host}.",
            "info",
        )

    if collection and collection != previous_collection:
        _append_e2e_log(
            job_id,
            f"Loading Regression collection {collection}.",
            "info",
        )

    if (
        machine_state
        and machine_state != previous_machine_state
    ):
        readable_machine_state = (
            machine_state
            .replace("_", " ")
            .title()
        )

        _append_e2e_log(
            job_id,
            f"Runner machine state: {readable_machine_state}.",
            "info",
        )

    if current_test and current_test != previous_current_test:
        _append_e2e_log(
            job_id,
            f"Executing testcase: {current_test}.",
        )

    # Log progress only when the completed count increases.
    # Runner snapshots may temporarily return zero while refreshing;
    # those regressions are ignored so the live console stays clean.
    if (
        total_tests > 0
        and completed_tests > 0
        and completed_tests > previous_completed_tests
    ):
        _append_e2e_log(
            job_id,
            (
                f"Completed {completed_tests} of "
                f"{total_tests} testcases."
            ),
            "info",
        )

    if runner_state and runner_state != previous_runner_state:
        readable_runner_state = (
            runner_state
            .replace("_", " ")
            .title()
        )

        if runner_state == "COMPLETED":
            _append_e2e_log(
                job_id,
                "Related testcase execution completed.",
            )

        elif runner_state in {
            "FAILED",
            "CANCELLED",
            "MONITOR_TIMEOUT",
        }:
            _append_e2e_log(
                job_id,
                f"Runner state changed to {readable_runner_state}.",
                "warning",
            )

        elif runner_state not in {
            "INITIALIZING",
            "RUNNING",
        }:
            _append_e2e_log(
                job_id,
                f"Runner state: {readable_runner_state}.",
                "info",
            )

    _update_e2e_job(
        job_id,
        _last_logged_host=host,
        _last_logged_collection=collection,
        _last_logged_machine_state=machine_state,
        _last_logged_runner_state=runner_state,
        _last_logged_current_test=(
            current_test
            or previous_current_test
        ),
        _last_logged_completed_tests=max(
            previous_completed_tests,
            completed_tests,
        ),
    )


@app.get("/api/e2e/status/<job_id>")
def get_complete_e2e_status(
    job_id: str,
):
    job = _get_e2e_job_copy(
        job_id
    )

    if job is None:
        return jsonify(
            {
                "success": False,
                "message": (
                    "End-to-end workflow was not found."
                ),
            }
        ), 404

    session_id = str(
        job.get(
            "runner_session_id",
            "",
        )
        or ""
    ).strip()

    if (
        session_id
        and job.get(
            "workflow_status"
        )
        == "running"
    ):
        try:
            snapshot = (
                execution_monitor_service
                .get_snapshot(
                    session_id
                )
            )

            _update_e2e_job(
                job_id,
                runner=snapshot,
            )

            _publish_runner_activity(
                job_id,
                snapshot,
            )

            job = _get_e2e_job_copy(
                job_id
            ) or job

        except Exception:
            pass

    return jsonify(
        {
            "success": True,
            "job": job,
        }
    )



@app.get("/api/e2e/report/<job_id>/view")
def view_complete_e2e_report(
    job_id: str,
):
    job = _get_e2e_job_copy(
        job_id
    )

    if job is None:
        return jsonify(
            {
                "success": False,
                "message": "End-to-end workflow was not found.",
            }
        ), 404

    report = job.get("report", {}) or {}

    report_path = Path(
        str(
            report.get("absolute_path", "")
            or ""
        )
    )

    if not report_path.is_file():
        return jsonify(
            {
                "success": False,
                "message": "HTML report is not available yet.",
            }
        ), 404

    return send_file(
        report_path,
        as_attachment=False,
        mimetype="text/html",
    )


@app.get("/api/e2e/report/<job_id>/download")
def download_complete_e2e_report(
    job_id: str,
):
    job = _get_e2e_job_copy(
        job_id
    )

    if job is None:
        return jsonify(
            {
                "success": False,
                "message": (
                    "End-to-end workflow was not found."
                ),
            }
        ), 404

    report = (
        job.get(
            "report",
            {},
        )
        or {}
    )

    report_path = Path(
        str(
            report.get(
                "absolute_path",
                "",
            )
            or ""
        )
    )

    if (
        not report_path
        or not report_path.is_file()
    ):
        return jsonify(
            {
                "success": False,
                "message": (
                    "HTML report is not available yet."
                ),
            }
        ), 404

    return send_file(
        report_path,
        as_attachment=True,
        download_name=(
            report.get(
                "filename"
            )
            or report_path.name
        ),
        mimetype="text/html",
    )

# ============================================================
# Application startup
# ============================================================
if __name__ == "__main__":
    app.run(
        host=os.getenv(
            "APP_HOST",
            "0.0.0.0",
        ),
        port=int(
            os.getenv(
                "APP_PORT",
                "5005",
            )
        ),
        debug=(
            os.getenv(
                "FLASK_DEBUG",
                "false",
            ).lower()
            == "true"
        ),
    )