from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


class SuiteParserService:
    """
    Read-only parser for the automation framework.

    It discovers:

    Suite
      -> inherited parent suites
      -> addTestCollection(...) calls
      -> collection files
      -> test methods beginning with 'test'
    """

    def __init__(self, automation_path: str) -> None:
        self.automation_path = Path(automation_path).resolve()

    def _validate_path(self, file_path: Path) -> Path:
        resolved_path = file_path.resolve()

        try:
            resolved_path.relative_to(self.automation_path)
        except ValueError as exc:
            raise ValueError(
                "Requested file is outside the automation repository."
            ) from exc

        if not resolved_path.exists():
            raise FileNotFoundError(
                f"File was not found: {resolved_path}"
            )

        if not resolved_path.is_file():
            raise ValueError(
                f"Path is not a file: {resolved_path}"
            )

        return resolved_path

    @staticmethod
    def _read_python_tree(file_path: Path) -> ast.Module:
        source = file_path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        return ast.parse(
            source,
            filename=str(file_path),
        )

    @staticmethod
    def _extract_import_map(
        tree: ast.Module,
    ) -> dict[str, str]:
        """
        Example:

        import BrowserPlugin

        returns:

        {
            "BrowserPlugin": "BrowserPlugin"
        }

        Example:

        import QuicksilverNVMRegression

        returns:

        {
            "QuicksilverNVMRegression":
                "QuicksilverNVMRegression"
        }
        """
        imports: dict[str, str] = {}

        for node in tree.body:
            if isinstance(node, ast.Import):
                for imported in node.names:
                    local_name = (
                        imported.asname
                        or imported.name.split(".")[-1]
                    )

                    imports[local_name] = imported.name

            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""

                for imported in node.names:
                    local_name = (
                        imported.asname
                        or imported.name
                    )

                    full_name = (
                        f"{module_name}.{imported.name}"
                        if module_name
                        else imported.name
                    )

                    imports[local_name] = full_name

        return imports

    @staticmethod
    def _extract_suite_classes(
        tree: ast.Module,
    ) -> list[ast.ClassDef]:
        return [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        ]

    @staticmethod
    def _base_class_names(
        class_node: ast.ClassDef,
    ) -> list[str]:
        names: list[str] = []

        for base in class_node.bases:
            if isinstance(base, ast.Name):
                names.append(base.id)

            elif isinstance(base, ast.Attribute):
                if isinstance(base.value, ast.Name):
                    names.append(
                        f"{base.value.id}.{base.attr}"
                    )
                else:
                    names.append(base.attr)

        return names

    @staticmethod
    def _collection_references(
        class_node: ast.ClassDef,
    ) -> list[dict[str, str]]:
        """
        Detect patterns like:

            tc = BrowserPlugin.BrowserPlugin()
            this.addTestCollection(tc)

        Also supports:

            this.addTestCollection(
                BrowserPlugin.BrowserPlugin()
            )
        """
        assignments: dict[str, dict[str, str]] = {}
        collections: list[dict[str, str]] = []

        for node in ast.walk(class_node):
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1:
                    continue

                target = node.targets[0]

                if not isinstance(target, ast.Name):
                    continue

                value = node.value

                if not isinstance(value, ast.Call):
                    continue

                if not isinstance(value.func, ast.Attribute):
                    continue

                module_name = ""

                if isinstance(value.func.value, ast.Name):
                    module_name = value.func.value.id

                assignments[target.id] = {
                    "module": module_name,
                    "class_name": value.func.attr,
                }

        for node in ast.walk(class_node):
            if not isinstance(node, ast.Call):
                continue

            if not isinstance(node.func, ast.Attribute):
                continue

            if node.func.attr != "addTestCollection":
                continue

            if not node.args:
                continue

            argument = node.args[0]

            collection: dict[str, str] | None = None

            if isinstance(argument, ast.Name):
                collection = assignments.get(argument.id)

            elif (
                isinstance(argument, ast.Call)
                and isinstance(
                    argument.func,
                    ast.Attribute,
                )
            ):
                module_name = ""

                if isinstance(
                    argument.func.value,
                    ast.Name,
                ):
                    module_name = (
                        argument.func.value.id
                    )

                collection = {
                    "module": module_name,
                    "class_name": argument.func.attr,
                }

            if collection:
                unique_key = (
                    collection["module"],
                    collection["class_name"],
                )

                if not any(
                    (
                        existing["module"],
                        existing["class_name"],
                    )
                    == unique_key
                    for existing in collections
                ):
                    collections.append(collection)

        return collections

    def _find_python_module(
        self,
        module_name: str,
        current_file: Path,
    ) -> Path | None:
        """
        Resolve a module such as BrowserPlugin to BrowserPlugin.py.

        Search priority:

        1. Same folder as the current suite
        2. Repository-wide exact filename match
        """
        short_name = module_name.split(".")[-1]
        expected_filename = f"{short_name}.py"

        same_folder_file = (
            current_file.parent / expected_filename
        )

        if same_folder_file.exists():
            return same_folder_file.resolve()

        matches = list(
            self.automation_path.rglob(
                expected_filename
            )
        )

        if not matches:
            return None

        # Prefer files under Tests rather than TestSuites
        # when resolving collection implementations.
        tests_matches = [
            match
            for match in matches
            if any(
                part.lower() == "tests"
                for part in match.parts
            )
        ]

        if tests_matches:
            return tests_matches[0].resolve()

        return matches[0].resolve()

    @staticmethod
    def _extract_test_methods(
        tree: ast.Module,
        expected_class_name: str = "",
    ) -> list[dict[str, Any]]:
        test_methods: list[dict[str, Any]] = []

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue

            if (
                expected_class_name
                and node.name != expected_class_name
            ):
                continue

            for member in node.body:
                if not isinstance(
                    member,
                    (
                        ast.FunctionDef,
                        ast.AsyncFunctionDef,
                    ),
                ):
                    continue

                if not member.name.startswith("test"):
                    continue

                docstring = (
                    ast.get_docstring(member)
                    or ""
                ).strip()

                test_methods.append(
                    {
                        "name": member.name,
                        "line_number": member.lineno,
                        "description": docstring,
                    }
                )

        return test_methods

    def _parse_collection(
        self,
        module_name: str,
        class_name: str,
        suite_file: Path,
    ) -> dict[str, Any]:
        collection_file = self._find_python_module(
            module_name=module_name,
            current_file=suite_file,
        )

        if not collection_file:
            return {
                "module": module_name,
                "class_name": class_name,
                "file_found": False,
                "relative_path": "",
                "tests": [],
                "message": (
                    f"Could not find Python file "
                    f"for module {module_name}."
                ),
            }

        try:
            tree = self._read_python_tree(
                collection_file
            )

            tests = self._extract_test_methods(
                tree,
                expected_class_name=class_name,
            )

            # Fallback when class-name matching is different.
            if not tests:
                tests = self._extract_test_methods(
                    tree
                )

            return {
                "module": module_name,
                "class_name": class_name,
                "file_found": True,
                "relative_path": str(
                    collection_file.relative_to(
                        self.automation_path
                    )
                ),
                "tests": tests,
                "test_count": len(tests),
                "message": "",
            }

        except SyntaxError as exc:
            return {
                "module": module_name,
                "class_name": class_name,
                "file_found": True,
                "relative_path": str(
                    collection_file.relative_to(
                        self.automation_path
                    )
                ),
                "tests": [],
                "test_count": 0,
                "message": (
                    f"Could not parse Python file: {exc}"
                ),
            }

    def _parse_suite_recursive(
        self,
        suite_file: Path,
        visited: set[Path],
    ) -> dict[str, Any]:
        suite_file = self._validate_path(
            suite_file
        )

        if suite_file in visited:
            return {
                "suite_name": suite_file.stem,
                "relative_path": str(
                    suite_file.relative_to(
                        self.automation_path
                    )
                ),
                "collections": [],
                "parent_suites": [],
                "message": "Suite already parsed.",
            }

        visited.add(suite_file)

        tree = self._read_python_tree(
            suite_file
        )

        import_map = self._extract_import_map(
            tree
        )

        suite_classes = self._extract_suite_classes(
            tree
        )

        collections: list[dict[str, Any]] = []
        parent_suites: list[dict[str, Any]] = []

        for class_node in suite_classes:
            for reference in self._collection_references(
                class_node
            ):
                collection = self._parse_collection(
                    module_name=reference["module"],
                    class_name=reference["class_name"],
                    suite_file=suite_file,
                )

                collections.append(collection)

            for base_name in self._base_class_names(
                class_node
            ):
                module_alias = (
                    base_name.split(".")[0]
                )

                imported_module = import_map.get(
                    module_alias,
                    module_alias,
                )

                parent_file = self._find_python_module(
                    module_name=imported_module,
                    current_file=suite_file,
                )

                if (
                    parent_file
                    and any(
                        part.lower() == "testsuites"
                        for part in parent_file.parts
                    )
                ):
                    parent_result = (
                        self._parse_suite_recursive(
                            parent_file,
                            visited,
                        )
                    )

                    parent_suites.append(
                        parent_result
                    )

        return {
            "suite_name": suite_file.stem,
            "relative_path": str(
                suite_file.relative_to(
                    self.automation_path
                )
            ),
            "collections": collections,
            "collection_count": len(collections),
            "parent_suites": parent_suites,
            "message": "",
        }

    def parse_suite(
        self,
        relative_path: str,
    ) -> dict[str, Any]:
        suite_file = (
            self.automation_path
            / relative_path
        )

        parsed = self._parse_suite_recursive(
            suite_file=suite_file,
            visited=set(),
        )

        flattened_collections: list[
            dict[str, Any]
        ] = []

        def collect_all(
            suite_data: dict[str, Any],
            source_suite: str,
        ) -> None:
            for collection in (
                suite_data.get("collections")
                or []
            ):
                value = dict(collection)
                value["source_suite"] = (
                    source_suite
                )

                flattened_collections.append(
                    value
                )

            for parent in (
                suite_data.get("parent_suites")
                or []
            ):
                collect_all(
                    parent,
                    parent.get(
                        "suite_name",
                        "Unknown",
                    ),
                )

        collect_all(
            parsed,
            parsed["suite_name"],
        )

        unique_collections: list[
            dict[str, Any]
        ] = []

        seen: set[tuple[str, str]] = set()

        for collection in flattened_collections:
            key = (
                collection.get("module", ""),
                collection.get(
                    "class_name",
                    "",
                ),
            )

            if key in seen:
                continue

            seen.add(key)
            unique_collections.append(
                collection
            )

        total_tests = sum(
            collection.get(
                "test_count",
                len(
                    collection.get("tests")
                    or []
                ),
            )
            for collection in unique_collections
        )

        return {
            "suite_name": parsed["suite_name"],
            "relative_path": parsed[
                "relative_path"
            ],
            "collections": unique_collections,
            "collection_count": len(
                unique_collections
            ),
            "total_tests": total_tests,
            "suite_tree": parsed,
        }