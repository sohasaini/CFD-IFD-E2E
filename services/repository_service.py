from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any


EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".idea",
    ".vscode",
}


class RepositoryService:
    """Read-only access to the local automation repository."""

    def __init__(self, automation_path: str) -> None:
        self.automation_path = Path(automation_path).expanduser()

    def _validate_repository(self) -> Path:
        path = self.automation_path

        if not path.exists():
            raise FileNotFoundError(
                f"Automation folder was not found: {path}"
            )

        if not path.is_dir():
            raise NotADirectoryError(
                f"Configured automation path is not a folder: {path}"
            )

        return path.resolve()

    @staticmethod
    def _is_test_suites_path(file_path: Path) -> bool:
        """
        Return True only when the Python file is under a TestSuites folder.
        Matching is case-insensitive.
        """
        return any(
            part.lower() == "testsuites"
            for part in file_path.parts
        )

    @staticmethod
    def _extract_component(file_path: Path) -> str:
        """
        Example:

        TestSuites\\AnyConnect\\RocketRaccoon510VPNRegression.py
        returns:
        AnyConnect
        """
        parts = list(file_path.parts)

        for index, part in enumerate(parts):
            if part.lower() == "testsuites":
                if index + 1 < len(parts) - 1:
                    return parts[index + 1]

        return "General"

    @staticmethod
    def _extract_python_details(file_path: Path) -> dict[str, Any]:
        """
        Parse the file using Python AST.

        AST reads Python structure without executing the automation code.
        """
        details = {
            "classes": [],
            "functions": [],
            "imports": [],
            "parse_error": "",
        }

        try:
            source = file_path.read_text(
                encoding="utf-8",
                errors="ignore",
            )

            tree = ast.parse(source)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    details["classes"].append(node.name)

                elif isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    details["functions"].append(node.name)

                elif isinstance(node, ast.Import):
                    for imported_name in node.names:
                        details["imports"].append(
                            imported_name.name
                        )

                elif isinstance(node, ast.ImportFrom):
                    module_name = node.module or ""

                    for imported_name in node.names:
                        full_name = (
                            f"{module_name}.{imported_name.name}"
                            if module_name
                            else imported_name.name
                        )
                        details["imports"].append(full_name)

        except SyntaxError as exc:
            details["parse_error"] = (
                f"Python syntax could not be parsed: {exc}"
            )

        except Exception as exc:
            details["parse_error"] = str(exc)

        details["classes"] = sorted(
            set(details["classes"])
        )

        details["functions"] = sorted(
            set(details["functions"])
        )

        details["imports"] = sorted(
            set(details["imports"])
        )

        return details

    def repository_status(self) -> dict[str, Any]:
        try:
            repository_path = self._validate_repository()

        except (FileNotFoundError, NotADirectoryError) as exc:
            return {
                "available": False,
                "path": str(self.automation_path),
                "message": str(exc),
                "python_files": 0,
                "suite_files": 0,
                "components": [],
            }

        python_file_count = 0
        suite_file_count = 0
        component_names: set[str] = set()

        for root, directories, filenames in os.walk(
            repository_path
        ):
            directories[:] = [
                directory
                for directory in directories
                if directory not in EXCLUDED_DIRECTORIES
            ]

            for filename in filenames:
                if not filename.lower().endswith(".py"):
                    continue

                python_file_count += 1

                file_path = Path(root) / filename

                if self._is_test_suites_path(file_path):
                    suite_file_count += 1
                    component_names.add(
                        self._extract_component(file_path)
                    )

        return {
            "available": True,
            "path": str(repository_path),
            "message": "Automation folder is available.",
            "python_files": python_file_count,
            "suite_files": suite_file_count,
            "components": sorted(
                component_names,
                key=str.lower,
            ),
        }

    def list_suites(
        self,
        component: str = "",
        search_text: str = "",
    ) -> list[dict[str, Any]]:
        repository_path = self._validate_repository()

        selected_component = component.strip().lower()
        search_value = search_text.strip().lower()

        suites: list[dict[str, Any]] = []

        for root, directories, filenames in os.walk(
            repository_path
        ):
            directories[:] = [
                directory
                for directory in directories
                if directory not in EXCLUDED_DIRECTORIES
            ]

            for filename in filenames:
                if not filename.lower().endswith(".py"):
                    continue

                file_path = Path(root) / filename

                if not self._is_test_suites_path(file_path):
                    continue

                suite_component = self._extract_component(
                    file_path
                )

                suite_name = file_path.stem

                if (
                    selected_component
                    and suite_component.lower()
                    != selected_component
                ):
                    continue

                searchable_value = (
                    f"{suite_name} "
                    f"{suite_component} "
                    f"{file_path}"
                ).lower()

                if (
                    search_value
                    and search_value not in searchable_value
                ):
                    continue

                suites.append(
                    {
                        "suite_name": suite_name,
                        "file_name": file_path.name,
                        "component": suite_component,
                        "relative_path": str(
                            file_path.relative_to(
                                repository_path
                            )
                        ),
                    }
                )

        return sorted(
            suites,
            key=lambda item: (
                item["component"].lower(),
                item["suite_name"].lower(),
            ),
        )

    def suite_details(
        self,
        relative_path: str,
    ) -> dict[str, Any]:
        repository_path = self._validate_repository()

        requested_path = (
            repository_path / relative_path
        ).resolve()

        # Security check:
        # Do not allow paths outside the configured automation repository.
        try:
            requested_path.relative_to(repository_path)
        except ValueError as exc:
            raise ValueError(
                "The requested suite path is outside "
                "the automation repository."
            ) from exc

        if not requested_path.exists():
            raise FileNotFoundError(
                f"Suite file was not found: {relative_path}"
            )

        if not requested_path.is_file():
            raise ValueError(
                "The selected suite path is not a file."
            )

        details = self._extract_python_details(
            requested_path
        )

        return {
            "suite_name": requested_path.stem,
            "file_name": requested_path.name,
            "component": self._extract_component(
                requested_path
            ),
            "relative_path": str(
                requested_path.relative_to(repository_path)
            ),
            "classes": details["classes"],
            "functions": details["functions"],
            "imports": details["imports"],
            "parse_error": details["parse_error"],
        }
