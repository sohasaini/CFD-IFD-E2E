
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests

try:
    from requests_oauthlib import OAuth1
except Exception:
    OAuth1 = None

try:
    from cdets_config import CONSUMER_KEY, CONSUMER_SECRET
except Exception:
    CONSUMER_KEY = None
    CONSUMER_SECRET = None


CDETS_BASE = "https://cdetsng.cisco.com/wsapi/LTS/api"
DEFAULT_START_DATE = date(2023, 1, 1)

# CDETS sources supported by this E2E tool.
#
# anyconnect keeps the existing configured-component sync because that flow
# is already proven and avoids one very large product-wide query.
#
# cloud_management is discovered product-wide. This lets newly created
# cloud-management components (for example cm_endpoint-win) appear in the UI
# without hard-coding every component name.
CDETS_PRODUCT_CONFIG = {
    "anyconnect": {
        "project": "CSC.security",
        "component_mode": "configured",
        # Preserve the existing AnyConnect behavior.
        "customer_use_only": True,
    },
    "cloud_management": {
        "project": "CSC.security",
        "component_mode": "discover",
        # Cloud Management defects such as CSCvw89716 can be filed from
        # internal sources, so do not restrict them to Found=customer-use.
        "customer_use_only": False,
    },
}

FIELD_ALIASES = {
    "project": "project",
    "product": "product",
    "headline": "headline",
    "summary": "summary",
    "description": "description",
    "component": "component",
    "severity": "severity",
    "priority": "priority",
    "status": "status",
    "status-desc": "status_description",
    "submitted-on": "submitted_on",
    "resolved-on": "resolved_on",
    "verified-on": "verified_on",
    "version": "version",
    "first-found-version": "first_found_version",
    "to-be-fixed": "to_be_fixed",
    "failed-release": "failed_release",
    "symptoms": "symptoms",
    "conditions": "conditions",
    "workarounds": "workarounds",
    "further-problem-description": "further_problem_description",
    "further problem description": "further_problem_description",
}


class CdetsSyncService:
    def __init__(
        self,
        components_file: Path,
        cache_file: Path,
        state_file: Path,
        timeout_seconds: int = 60,
    ) -> None:
        self.components_file = Path(components_file)
        self.cache_file = Path(cache_file)
        self.state_file = Path(state_file)
        self.timeout_seconds = timeout_seconds

        # Retry individual CDETS defect fetches so one transient timeout
        # does not stop the historical synchronization.
        self.fetch_retry_count = 3
        self.fetch_retry_delay_seconds = 3

        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _auth():
        if OAuth1 is None:
            raise RuntimeError("Install requests-oauthlib.")
        if not CONSUMER_KEY or not CONSUMER_SECRET:
            raise RuntimeError("Set CDETS_CONSUMER_KEY and CDETS_CONSUMER_SECRET environment variables.")
        return OAuth1(CONSUMER_KEY, CONSUMER_SECRET)

    def _load_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_json(self, path: Path, value: Any) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(value, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp.replace(path)

    @staticmethod
    def _year_windows(start_date: date, end_date: date):
        current = start_date
        while current <= end_date:
            window_end = min(date(current.year, 12, 31), end_date)
            yield current, window_end
            current = window_end + timedelta(days=1)

    @staticmethod
    def _field_value(field: ET.Element) -> str:
        direct = str(field.text or "").strip()
        if direct:
            return direct
        values = [
            str(child.text or "").strip()
            for child in list(field)
            if str(child.text or "").strip()
        ]
        return " | ".join(values)

    @staticmethod
    def _direct_fields(root: ET.Element) -> list[ET.Element]:
        fields = root.findall("./{*}Field") or root.findall("./Field")
        if fields:
            return fields
        for child in list(root):
            name = child.tag.rsplit("}", 1)[-1]
            if name in {"Defect", "Bug"}:
                fields = child.findall("./{*}Field") or child.findall("./Field")
                if fields:
                    return fields
        return []

    def _search_ids(
        self,
        project: str,
        product: str,
        start_date: date,
        end_date: date,
        auth,
        component: str = "",
        customer_use_only: bool = True,
    ) -> list[str]:
        criteria_parts = [
            f"([Product] = '{product}')",
            f"([Project] = '{project}')",
        ]

        if customer_use_only:
            criteria_parts.insert(
                0,
                "([Found] = 'customer-use')",
            )

        component_value = str(
            component or ""
        ).strip()

        if component_value:
            criteria_parts.append(
                f"([Component] = '{component_value}')"
            )

        criteria_parts.append(
            (
                f"([Submitted-on] >= "
                f"'{start_date.strftime('%m/%d/%Y')}' AND "
                f"[Submitted-on] <= "
                f"'{end_date.strftime('%m/%d/%Y')}')"
            )
        )

        criteria = " AND ".join(
            criteria_parts
        )

        print(
            f"[CDETS] Search: project={project}, "
            f"product={product}, "
            f"component={component_value or 'ALL'}, "
            f"customer_use_only={customer_use_only}, "
            f"from={start_date.isoformat()}, "
            f"to={end_date.isoformat()}"
        )

        response = requests.get(
            f"{CDETS_BASE}/search",
            params={
                "criteria": criteria,
            },
            auth=auth,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()

        root = ET.fromstring(
            response.text
        )

        ids: list[str] = []

        for element in (
            root.findall(".//{*}Defect")
            + root.findall(".//Defect")
        ):
            bug_id = str(
                element.get("id") or ""
            ).strip().upper()

            if bug_id:
                ids.append(
                    bug_id
                )

        unique_ids = list(
            dict.fromkeys(ids)
        )

        print(
            f"[CDETS] Search returned "
            f"{len(unique_ids)} defect(s)."
        )

        return unique_ids

    def _fetch_bug(
        self,
        bug_id: str,
        auth,
    ) -> dict[str, Any]:
        """
        Fetch one CDETS bug with retries so a single timeout does not stop
        a long historical sync.
        """
        last_error: Exception | None = None

        for attempt in range(
            1,
            self.fetch_retry_count + 1,
        ):
            try:
                response = requests.get(
                    f"{CDETS_BASE}/bug/{bug_id}",
                    auth=auth,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                root = ET.fromstring(response.text)

                result: dict[str, Any] = {"id": bug_id}
                raw_fields: dict[str, str] = {}

                for field in self._direct_fields(root):
                    original_name = str(
                        field.get("name") or ""
                    ).strip()
                    normalized_name = original_name.lower()
                    value = self._field_value(field)

                    if not normalized_name or not value:
                        continue

                    raw_fields[original_name] = value

                    mapped = FIELD_ALIASES.get(normalized_name)
                    if mapped:
                        result[mapped] = value

                if result.get("status_description"):
                    result["status"] = result["status_description"]

                for key in (
                    "project",
                    "product",
                    "headline",
                    "summary",
                    "description",
                    "component",
                    "status",
                    "severity",
                    "priority",
                    "submitted_on",
                    "symptoms",
                    "conditions",
                    "workarounds",
                    "further_problem_description",
                ):
                    result.setdefault(key, "")

                result["raw_fields"] = raw_fields
                result["fetched_at"] = datetime.now().isoformat()
                return result

            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as exc:
                last_error = exc

                if attempt < self.fetch_retry_count:
                    print(
                        f"[CDETS] {bug_id}: timeout/network error "
                        f"(attempt {attempt}/{self.fetch_retry_count}). "
                        "Retrying..."
                    )
                    time.sleep(self.fetch_retry_delay_seconds)
                    continue

                raise RuntimeError(
                    f"CDETS bug {bug_id} could not be fetched after "
                    f"{self.fetch_retry_count} attempts: {exc}"
                ) from exc

            except requests.RequestException as exc:
                raise RuntimeError(
                    f"CDETS bug {bug_id} request failed: {exc}"
                ) from exc

            except ET.ParseError as exc:
                raise RuntimeError(
                    f"CDETS returned invalid XML for {bug_id}: {exc}"
                ) from exc

        raise RuntimeError(
            f"CDETS bug {bug_id} could not be fetched: {last_error}"
        )

    @staticmethod
    def _month_windows(
        start_date: date,
        end_date: date,
    ):
        """
        Yield small monthly search windows.

        CDETS search can return a limited result set for broad product-wide
        searches. Monthly windows prevent newer defects from being omitted
        when a product has more results than one search response returns.
        """
        cursor = start_date.replace(day=1)

        while cursor <= end_date:
            if cursor.month == 12:
                next_month = date(
                    cursor.year + 1,
                    1,
                    1,
                )
            else:
                next_month = date(
                    cursor.year,
                    cursor.month + 1,
                    1,
                )

            window_start = max(
                start_date,
                cursor,
            )
            window_end = min(
                end_date,
                next_month - timedelta(days=1),
            )

            if window_start <= window_end:
                yield (
                    window_start,
                    window_end,
                )

            cursor = next_month


    def sync(
        self,
        force_start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        today = end_date or date.today()

        state = self._load_json(
            self.state_file,
            {
                "last_successful_sync": "",
                "last_error": "",
            },
        )

        cache = self._load_json(
            self.cache_file,
            {
                "metadata": {},
                "defects": [],
            },
        )

        records = {
            str(item.get("id", "")).strip().upper(): item
            for item in cache.get("defects", [])
            if isinstance(item, dict)
            and item.get("id")
        }

        # Existing caches were created when only anyconnect was supported.
        # Preserve them by assigning the old source when product/project is
        # missing. New fetches will contain the values directly from CDETS.
        for record in records.values():
            record.setdefault("project", "CSC.security")
            record.setdefault("product", "anyconnect")

        if force_start_date:
            start_date = force_start_date
        elif state.get("last_successful_sync"):
            start_date = (
                datetime.strptime(
                    state["last_successful_sync"],
                    "%Y-%m-%d",
                ).date()
                + timedelta(days=1)
            )
        else:
            start_date = DEFAULT_START_DATE

        if start_date > today:
            return {
                "message": "Already up to date.",
                "records_count": len(records),
                "last_successful_sync": state.get(
                    "last_successful_sync",
                    "",
                ),
            }

        configured_components = [
            str(item.get("name", "")).strip()
            for item in self._load_json(
                self.components_file,
                [],
            )
            if isinstance(item, dict)
            and item.get("name")
        ]

        auth = self._auth()
        batch_count = 0
        found_count = 0
        product_summary: dict[str, dict[str, Any]] = {}

        try:
            for product, config in CDETS_PRODUCT_CONFIG.items():
                project = str(
                    config.get("project", "CSC.security")
                ).strip()

                component_mode = str(
                    config.get(
                        "component_mode",
                        "discover",
                    )
                ).strip().lower()

                customer_use_only = bool(
                    config.get(
                        "customer_use_only",
                        True,
                    )
                )

                product_found = 0
                product_batches = 0

                if component_mode == "configured":
                    query_components = configured_components
                else:
                    # Empty component means one product-wide CDETS query.
                    # Components are then discovered from the returned bugs.
                    query_components = [""]

                for component in query_components:
                    # AnyConnect is already queried component-by-component,
                    # so yearly windows are small enough.
                    #
                    # cloud_management is initially queried product-wide to
                    # discover components. A broad yearly query can be capped
                    # by CDETS and silently omit defects. Use monthly windows
                    # for discover-mode products so every recent defect is
                    # included (for example CSCvw89716 submitted 08/04/2026).
                    if component_mode == "discover":
                        search_windows = self._month_windows(
                            start_date,
                            today,
                        )
                    else:
                        search_windows = self._year_windows(
                            start_date,
                            today,
                        )

                    for (
                        window_start,
                        window_end,
                    ) in search_windows:
                        ids = self._search_ids(
                            project=project,
                            product=product,
                            component=component,
                            start_date=window_start,
                            end_date=window_end,
                            auth=auth,
                            customer_use_only=(
                                customer_use_only
                            ),
                        )

                        found_count += len(ids)
                        product_found += len(ids)

                        failed_bug_ids: list[str] = []

                        for bug_index, bug_id in enumerate(
                            ids,
                            start=1,
                        ):
                            try:
                                bug = self._fetch_bug(
                                    bug_id,
                                    auth,
                                )

                                bug["project"] = str(
                                    bug.get("project")
                                    or project
                                ).strip()

                                bug["product"] = str(
                                    bug.get("product")
                                    or product
                                ).strip()

                                records[bug_id] = bug

                                print(
                                    f"[CDETS] {product}"
                                    + (
                                        f"/{component}"
                                        if component
                                        else ""
                                    )
                                    + f": {bug_index}/{len(ids)} "
                                    + f"fetched {bug_id}"
                                )

                            except Exception as exc:
                                failed_bug_ids.append(bug_id)
                                print(
                                    f"[CDETS] WARNING: skipping "
                                    f"{bug_id}: {exc}"
                                )

                            if (
                                bug_index % 25 == 0
                                or bug_index == len(ids)
                            ):
                                self._save_json(
                                    self.cache_file,
                                    {
                                        "metadata": {
                                            "last_updated": datetime.now().isoformat(),
                                            "sync_start": start_date.isoformat(),
                                            "sync_end": today.isoformat(),
                                            "batch_count": batch_count,
                                            "records_count": len(records),
                                            "products": product_summary,
                                            "current_product": product,
                                            "current_component": component,
                                            "current_window_start": window_start.isoformat(),
                                            "current_window_end": window_end.isoformat(),
                                            "failed_bug_ids": failed_bug_ids,
                                        },
                                        "defects": list(records.values()),
                                    },
                                )

                        batch_count += 1
                        product_batches += 1

                        discovered_components = sorted({
                            str(
                                item.get("component", "")
                            ).strip()
                            for item in records.values()
                            if str(
                                item.get("product", "")
                            ).strip().lower()
                            == product.lower()
                            and str(
                                item.get("component", "")
                            ).strip()
                        }, key=str.lower)

                        product_summary[product] = {
                            "project": project,
                            "component_mode": component_mode,
                            "customer_use_only": (
                                customer_use_only
                            ),
                            "batches_processed": (
                                product_batches
                            ),
                            "defects_found": product_found,
                            "components": (
                                discovered_components
                            ),
                        }

                        self._save_json(
                            self.cache_file,
                            {
                                "metadata": {
                                    "last_updated": (
                                        datetime.now()
                                        .isoformat()
                                    ),
                                    "sync_start": (
                                        start_date
                                        .isoformat()
                                    ),
                                    "sync_end": (
                                        today.isoformat()
                                    ),
                                    "batch_count": (
                                        batch_count
                                    ),
                                    "records_count": (
                                        len(records)
                                    ),
                                    "products": (
                                        product_summary
                                    ),
                                },
                                "defects": list(
                                    records.values()
                                ),
                            },
                        )

            state["last_successful_sync"] = (
                today.isoformat()
            )
            state["last_error"] = ""
            self._save_json(
                self.state_file,
                state,
            )

            return {
                "message": (
                    "CDETS synchronization completed."
                ),
                "products_processed": list(
                    CDETS_PRODUCT_CONFIG.keys()
                ),
                "product_summary": product_summary,
                "yearly_batches_processed": (
                    batch_count
                ),
                "defects_found": found_count,
                "records_count": len(records),
                "last_successful_sync": (
                    today.isoformat()
                ),
                "cache_file": str(
                    self.cache_file
                ),
            }

        except Exception as exc:
            state["last_error"] = str(exc)
            self._save_json(
                self.state_file,
                state,
            )
            raise

