from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class CfdCacheService:
    def __init__(self, cache_file: Path, components_file: Path) -> None:
        self.cache_file = Path(cache_file)
        self.components_file = Path(components_file)

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _parse_date(value: Any) -> datetime | None:
        raw = str(value or "").strip()
        for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                pass
        return None

    def _load_json(self, path: Path) -> Any:
        if not path.exists():
            raise FileNotFoundError(f"Required file was not found: {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON file: {path}") from exc

    def _load_defects(self) -> list[dict[str, Any]]:
        data = self._load_json(
            self.cache_file
        )

        defects = (
            data.get("defects", [])
            if isinstance(data, dict)
            else []
        )

        if not isinstance(defects, list):
            raise RuntimeError(
                "CFD cache defects must be a list."
            )

        return [
            item
            for item in defects
            if isinstance(item, dict)
        ]

    def get_products(
        self,
    ) -> list[dict[str, str]]:
        """
        Return the products available to the dashboard.

        Existing records created before product support are treated as
        anyconnect so the old cache remains backward compatible.
        """
        defects = self._load_defects()

        product_to_project: dict[str, str] = {
            "anyconnect": "CSC.security",
            "cloud_management": "CSC.security",
        }

        for defect in defects:
            product = self._clean(
                defect.get("product")
            ) or "anyconnect"

            project = self._clean(
                defect.get("project")
            ) or "CSC.security"

            product_to_project.setdefault(
                product,
                project,
            )

        return [
            {
                "name": product,
                "project": project,
            }
            for product, project in sorted(
                product_to_project.items(),
                key=lambda item: item[0].lower(),
            )
        ]

    def get_components(
        self,
        product: str = "",
    ) -> list[dict[str, str]]:
        """
        Return components for one selected product.

        anyconnect includes the original configured component list plus any
        components observed in the cache.

        Other products are discovered directly from cached CDETS defects.
        """
        product_value = (
            self._clean(product).lower()
        )

        configured: dict[str, dict[str, str]] = {}

        # Keep the original AnyConnect component descriptions.
        try:
            data = self._load_json(
                self.components_file
            )
        except FileNotFoundError:
            data = []

        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    name = item.strip()
                    description = ""
                elif isinstance(item, dict):
                    name = self._clean(
                        item.get("name")
                    )
                    description = self._clean(
                        item.get("description")
                    )
                else:
                    continue

                if name:
                    configured[
                        name.lower()
                    ] = {
                        "name": name,
                        "description": description,
                    }

        observed: dict[str, dict[str, str]] = {}

        for defect in self._load_defects():
            defect_product = (
                self._clean(
                    defect.get("product")
                )
                or "anyconnect"
            )

            if (
                product_value
                and defect_product.lower()
                != product_value
            ):
                continue

            component = self._clean(
                defect.get("component")
            )

            if not component:
                continue

            observed[
                component.lower()
            ] = {
                "name": component,
                "description": "",
            }

        # When no product is supplied, preserve the old API behavior.
        if not product_value:
            merged = {
                **configured,
                **observed,
            }
        elif product_value == "anyconnect":
            merged = {
                **configured,
                **observed,
            }
        else:
            merged = observed

        return sorted(
            merged.values(),
            key=lambda item: (
                item["name"].lower()
            ),
        )

    def get_metadata(self) -> dict[str, Any]:
        data = self._load_json(self.cache_file)
        metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
        defects = data.get("defects", []) if isinstance(data, dict) else []
        return {
            **(metadata if isinstance(metadata, dict) else {}),
            "records_count": len(defects if isinstance(defects, list) else []),
            "cache_file": str(self.cache_file),
        }

    def search(
        self,
        product: str = "",
        component: str = "",
        from_date: str = "",
        to_date: str = "",
        text: str = "",
        limit: int = 500,
    ) -> dict[str, Any]:
        defects = self._load_defects()

        product_value = self._clean(
            product
        ).lower()

        component_value = self._clean(
            component
        ).lower()

        search_text = self._clean(
            text
        ).lower()

        from_dt = (
            self._parse_date(from_date)
            if from_date
            else None
        )

        to_dt = (
            self._parse_date(to_date)
            if to_date
            else None
        )

        if from_date and from_dt is None:
            raise ValueError(
                "from_date must be YYYY-MM-DD."
            )

        if to_date and to_dt is None:
            raise ValueError(
                "to_date must be YYYY-MM-DD."
            )

        if to_dt:
            to_dt = to_dt.replace(
                hour=23,
                minute=59,
                second=59,
            )

        safe_limit = max(
            1,
            min(
                int(limit),
                2000,
            ),
        )

        results: list[dict[str, Any]] = []

        for defect in defects:
            defect_product = (
                self._clean(
                    defect.get("product")
                )
                or "anyconnect"
            )

            defect_project = (
                self._clean(
                    defect.get("project")
                )
                or "CSC.security"
            )

            if (
                product_value
                and defect_product.lower()
                != product_value
            ):
                continue

            if (
                component_value
                and self._clean(
                    defect.get("component")
                ).lower()
                != component_value
            ):
                continue

            submitted_dt = self._parse_date(
                defect.get("submitted_on")
            )

            if (
                from_dt
                and (
                    submitted_dt is None
                    or submitted_dt < from_dt
                )
            ):
                continue

            if (
                to_dt
                and (
                    submitted_dt is None
                    or submitted_dt > to_dt
                )
            ):
                continue

            if search_text:
                searchable = " ".join([
                    self._clean(
                        defect.get("id")
                    ),
                    defect_product,
                    defect_project,
                    self._clean(
                        defect.get("component")
                    ),
                    self._clean(
                        defect.get("headline")
                    ),
                    self._clean(
                        defect.get("summary")
                    ),
                    self._clean(
                        defect.get("description")
                    ),
                    self._clean(
                        defect.get("symptoms")
                    ),
                    self._clean(
                        defect.get("conditions")
                    ),
                ]).lower()

                if search_text not in searchable:
                    continue

            results.append({
                "id": self._clean(
                    defect.get("id")
                ),
                "project": defect_project,
                "product": defect_product,
                "headline": self._clean(
                    defect.get("headline")
                    or defect.get("summary")
                ),
                "component": self._clean(
                    defect.get("component")
                ),
                "status": self._clean(
                    defect.get("status")
                ),
                "severity": self._clean(
                    defect.get("severity")
                ),
                "priority": self._clean(
                    defect.get("priority")
                ),
                "submitted_on": self._clean(
                    defect.get("submitted_on")
                ),
                "description": self._clean(
                    defect.get("description")
                ),
                "symptoms": self._clean(
                    defect.get("symptoms")
                ),
                "conditions": self._clean(
                    defect.get("conditions")
                ),
                "workarounds": self._clean(
                    defect.get("workarounds")
                ),
                "further_problem_description": (
                    self._clean(
                        defect.get(
                            "further_problem_description"
                        )
                    )
                ),
            })

        results.sort(
            key=lambda item: (
                self._parse_date(
                    item.get("submitted_on")
                )
                or datetime.min
            ),
            reverse=True,
        )

        total = len(results)

        return {
            "total": total,
            "returned": min(
                total,
                safe_limit,
            ),
            "filters": {
                "product": product,
                "component": component,
                "from_date": from_date,
                "to_date": to_date,
                "text": text,
            },
            "defects": results[:safe_limit],
        }

    def get_defect(self, defect_id: str) -> dict[str, Any]:
        normalized_id = self._clean(defect_id).upper()
        if not normalized_id:
            raise ValueError("Defect ID is required.")
        data = self._load_json(self.cache_file)
        defects = data.get("defects", []) if isinstance(data, dict) else []
        for defect in defects:
            if isinstance(defect, dict) and self._clean(defect.get("id")).upper() == normalized_id:
                return defect
        raise FileNotFoundError(f"Defect {normalized_id} was not found.")