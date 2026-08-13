from __future__ import annotations

import socket
import struct
import threading
import time
import uuid
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET


TERMINAL_TEST_STATUSES = {
    "PASSED",
    "FAILED",
    "ERRORED",
    "CRASHED",
    "BLOCKED",
}


class AutomationResponse:
    def __init__(
        self,
        code: str = "",
        data: str = "",
        error: str = "",
    ) -> None:
        self.code = code
        self.data = data
        self.error = error


class AutomationSocketClient:
    PORT = 5122

    @staticmethod
    def _receive_exact(
        sock: socket.socket,
        length: int,
    ) -> bytes:
        buffer = bytearray()

        while len(buffer) < length:
            chunk = sock.recv(length - len(buffer))

            if not chunk:
                raise ConnectionError(
                    "CiscoAutomationRunner closed the connection."
                )

            buffer.extend(chunk)

        return bytes(buffer)

    @staticmethod
    def extract_xml(
        xml_text: str,
        field_name: str,
    ) -> str:
        if not xml_text:
            return ""

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return ""

        element = root.find(f".//{field_name}")

        if element is None:
            return ""

        return str(element.text or "").strip()

    def request(
        self,
        host: str,
        command: str,
        timeout: int = 30,
    ) -> AutomationResponse:
        response = AutomationResponse()

        try:
            with socket.create_connection(
                (host, self.PORT),
                timeout=timeout,
            ) as sock:
                sock.settimeout(timeout)
                sock.sendall(command.encode("latin1"))
                sock.shutdown(socket.SHUT_WR)

                response.code = self._receive_exact(
                    sock,
                    3,
                ).decode(
                    "ascii",
                    errors="replace",
                )

                message_length = struct.unpack(
                    "!i",
                    self._receive_exact(sock, 4),
                )[0]

                raw_data = self._receive_exact(
                    sock,
                    message_length,
                )

                response.data = raw_data.decode(
                    "utf-8",
                    errors="replace",
                )

                response.error = (
                    self.extract_xml(
                        response.data,
                        "error",
                    )
                    or ""
                )

                return response

        except socket.timeout:
            response.code = "ecn"
            response.error = "timed out"
            return response

        except Exception as exc:
            response.code = "ecn"
            response.error = str(exc)
            return response


class AutomationRunnerService:
    """
    Loads one collection and starts the complete collection using
    the same runner-level start action as the Cisco Automation Runner UI.

    It does not call runsubset or runsubsetparallelrunner.
    """

    def __init__(
        self,
        default_host: str,
    ) -> None:
        self.default_host = str(default_host or "").strip()
        self.client = AutomationSocketClient()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _utc_now() -> str:
        return datetime.utcnow().isoformat()

    def _update(
        self,
        session_id: str,
        **values: Any,
    ) -> None:
        with self._lock:
            self._sessions[session_id].update(values)

    def _command(
        self,
        host: str,
        command: str,
        timeout: int = 30,
    ) -> AutomationResponse:
        response = self.client.request(
            host=host,
            command=command,
            timeout=timeout,
        )

        if response.error:
            raise RuntimeError(
                f"{command} failed: {response.error}"
            )

        return response

    def _get_machine_state(
        self,
        host: str,
    ) -> str:
        response = self._command(
            host,
            "%01 ragopi status",
            timeout=30,
        )

        return self.client.extract_xml(
            response.data,
            "state",
        )

    def _wait_for_state(
        self,
        host: str,
        expected_state: str,
        timeout: int,
        session_id: str,
    ) -> bool:
        started = time.monotonic()

        while time.monotonic() - started < timeout:
            with self._lock:
                if self._sessions[session_id].get(
                    "cancel_requested",
                    False,
                ):
                    return False

            try:
                state = self._get_machine_state(host)

                self._update(
                    session_id,
                    machine_state=state,
                )

                if state == expected_state:
                    return True

            except RuntimeError as exc:
                self._update(
                    session_id,
                    message=f"Waiting for machine state: {exc}",
                )

            time.sleep(2)

        return False

    def _get_cases(
        self,
        host: str,
    ) -> list[str]:
        response = self._command(
            host,
            "%01 ragopi getcases",
            timeout=60,
        )

        try:
            root = ET.fromstring(response.data)
        except ET.ParseError as exc:
            raise RuntimeError(
                "getcases returned invalid XML."
            ) from exc

        cases: list[str] = []

        for element in root.findall(".//name"):
            name = str(element.text or "").strip()

            if name:
                cases.append(name)

        return list(dict.fromkeys(cases))

    def _get_case_status(
        self,
        host: str,
        testcase: str,
    ) -> str:
        response = self.client.request(
            host=host,
            command=f"%01 ragopi caseinfo {testcase}",
            timeout=30,
        )

        if response.error:
            return "UNKNOWN"

        return (
            self.client.extract_xml(
                response.data,
                "status",
            )
            or "UNKNOWN"
        )

    def _run_collection(
        self,
        session_id: str,
    ) -> None:
        with self._lock:
            session = dict(self._sessions[session_id])

        host = session["host"]
        collection = session["collection"]

        try:
            self._update(
                session_id,
                state="INITIALIZING",
                started_at=self._utc_now(),
                message="Setting machine to IDLE.",
            )

            self._command(
                host,
                "%01 ragopi goidle",
                timeout=30,
            )

            if not self._wait_for_state(
                host=host,
                expected_state="IDLE",
                timeout=45,
                session_id=session_id,
            ):
                raise RuntimeError(
                    "Machine did not reach IDLE state."
                )

            self._update(
                session_id,
                message=f"Loading collection {collection}.",
            )

            self._command(
                host,
                f"%01 ragopi load {collection}",
                timeout=60,
            )

            if not self._wait_for_state(
                host=host,
                expected_state="SUITE_LOADED",
                timeout=90,
                session_id=session_id,
            ):
                raise RuntimeError(
                    "Collection did not reach SUITE_LOADED state."
                )

            testcases = self._get_cases(host)

            if not testcases:
                raise RuntimeError(
                    "Loaded collection returned zero testcases."
                )

            self._update(
                session_id,
                state="RUNNING",
                total_tests=len(testcases),
                completed_tests=0,
                progress_percentage=0.0,
                testcases={
                    testcase: "NOTRUN"
                    for testcase in testcases
                },
                message=(
                    f"Starting complete collection with "
                    f"{len(testcases)} testcases."
                ),
            )

            # This is the important change:
            # start the complete loaded collection exactly like clicking
            # the Start button in Cisco Automation Runner.
            self._command(
                host,
                "%01 ragopi start",
                timeout=30,
            )

            execution_started = time.monotonic()

            while time.monotonic() - execution_started < 7200:
                with self._lock:
                    cancel_requested = self._sessions[
                        session_id
                    ].get(
                        "cancel_requested",
                        False,
                    )

                if cancel_requested:
                    self.client.request(
                        host=host,
                        command="%01 ragopi goidle",
                        timeout=30,
                    )

                    self._update(
                        session_id,
                        state="CANCELLED",
                        message="Execution was cancelled.",
                        completed_at=self._utc_now(),
                    )
                    return

                statuses: dict[str, str] = {}
                completed_count = 0
                running_test = ""

                for testcase in testcases:
                    status = self._get_case_status(
                        host,
                        testcase,
                    )

                    statuses[testcase] = status

                    if status in TERMINAL_TEST_STATUSES:
                        completed_count += 1

                    elif status == "RUNNING":
                        running_test = testcase

                progress = round(
                    completed_count / len(testcases) * 100,
                    2,
                )

                self._update(
                    session_id,
                    testcases=statuses,
                    completed_tests=completed_count,
                    progress_percentage=progress,
                    current_test=running_test,
                    message=(
                        f"Collection running: "
                        f"{completed_count}/{len(testcases)} completed."
                    ),
                )

                if completed_count == len(testcases):
                    # caseinfo can report every testcase as terminal while
                    # Cisco Automation Runner is still performing suite-level
                    # teardown. Wait for the Runner to leave SUITE_RUNNING
                    # before app.py collects evidence and calls goidle.
                    self._update(
                        session_id,
                        current_test="",
                        completed_tests=len(testcases),
                        progress_percentage=100.0,
                        message=(
                            "All testcases finished. Waiting for "
                            "Runner suite cleanup."
                        ),
                    )

                    finalized = self.wait_until_suite_finalized(
                        host=host,
                        timeout=600,
                        session_id=session_id,
                    )

                    self._update(
                        session_id,
                        state="COMPLETED",
                        machine_state=finalized.get(
                            "machine_state",
                            "",
                        ),
                        current_test="",
                        completed_at=self._utc_now(),
                        progress_percentage=100.0,
                        message=(
                            "Collection execution and Runner "
                            "suite cleanup completed."
                        ),
                    )
                    return

                time.sleep(5)

            raise RuntimeError(
                "Timed out waiting for complete collection execution."
            )

        except Exception as exc:
            self._update(
                session_id,
                state="FAILED",
                error=str(exc),
                message=str(exc),
                completed_at=self._utc_now(),
            )

    def wait_until_suite_finalized(
        self,
        host: str,
        timeout: int = 600,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Wait for the Runner itself to finish suite-level cleanup.

        testcase caseinfo can become PASSED/FAILED before the Runner has
        finished suite teardown. During that period the machine still reports
        SUITE_RUNNING and goidle is rejected.

        Do not call goidle until SUITE_RUNNING has ended.
        """
        host_value = str(
            host or self.default_host
        ).strip()

        if not host_value:
            raise ValueError(
                "Execution host is required."
            )

        started = time.monotonic()
        last_state = ""
        stable_non_running = 0

        while time.monotonic() - started < timeout:
            try:
                last_state = (
                    self._get_machine_state(
                        host_value
                    )
                    or ""
                ).strip().upper()

                if session_id:
                    self._update(
                        session_id,
                        machine_state=last_state,
                        message=(
                            "All testcases finished. "
                            "Waiting for Runner suite cleanup."
                            if last_state == "SUITE_RUNNING"
                            else "Runner suite cleanup completed."
                        ),
                    )

            except RuntimeError:
                last_state = ""

            if last_state and last_state != "SUITE_RUNNING":
                stable_non_running += 1
                if stable_non_running >= 2:
                    return {
                        "success": True,
                        "host": host_value,
                        "machine_state": last_state,
                        "message": (
                            "Runner finished suite-level cleanup "
                            "and can now be released."
                        ),
                    }
            else:
                stable_non_running = 0

            time.sleep(3)

        raise RuntimeError(
            "All testcases completed, but the Runner remained "
            f"in {last_state or 'UNKNOWN'} for more than "
            f"{timeout} seconds."
        )


    def wait_until_idle(
        self,
        host: str,
        timeout: int = 90,
        stable_checks: int = 2,
    ) -> dict[str, Any]:
        """
        Wait until the Cisco Automation Runner reports IDLE.

        The state must be IDLE for more than one consecutive check so the
        next collection is not loaded while the previous suite is still
        being cleaned up.
        """
        host_value = str(
            host or self.default_host
        ).strip()

        if not host_value:
            raise ValueError(
                "Execution host is required."
            )

        started = time.monotonic()
        consecutive_idle = 0
        last_state = ""

        while time.monotonic() - started < timeout:
            try:
                last_state = self._get_machine_state(
                    host_value
                )
            except RuntimeError:
                last_state = ""

            if last_state == "IDLE":
                consecutive_idle += 1

                if consecutive_idle >= max(
                    1,
                    stable_checks,
                ):
                    return {
                        "success": True,
                        "host": host_value,
                        "machine_state": "IDLE",
                        "message": (
                            "Runner is released and ready "
                            "for the next collection."
                        ),
                    }
            else:
                consecutive_idle = 0

            time.sleep(2)

        raise RuntimeError(
            "Runner did not reach a stable IDLE state "
            f"within {timeout} seconds. "
            f"Last state: {last_state or 'UNKNOWN'}."
        )

    def release_host(
        self,
        host: str,
        timeout: int = 180,
        retry_count: int = 12,
    ) -> dict[str, Any]:
        """
        Release a completed Runner safely.

        Wait for SUITE_RUNNING to end before sending goidle, then verify a
        stable IDLE state before allowing the next collection to load.
        """
        host_value = str(
            host or self.default_host
        ).strip()

        if not host_value:
            raise ValueError(
                "Execution host is required."
            )

        started = time.monotonic()
        last_error = ""
        attempt = 0

        try:
            current_state = (
                self._get_machine_state(
                    host_value
                )
                or ""
            ).strip().upper()
        except RuntimeError:
            current_state = ""

        if current_state == "SUITE_RUNNING":
            elapsed = int(
                time.monotonic() - started
            )
            remaining_for_finalize = max(
                30,
                min(
                    600,
                    timeout - elapsed
                    if timeout > elapsed
                    else 30,
                ),
            )

            self.wait_until_suite_finalized(
                host=host_value,
                timeout=remaining_for_finalize,
            )

        while (
            attempt < max(1, retry_count)
            and time.monotonic() - started < timeout
        ):
            attempt += 1

            try:
                state_before = (
                    self._get_machine_state(
                        host_value
                    )
                    or ""
                ).strip().upper()
            except RuntimeError:
                state_before = ""

            if state_before == "IDLE":
                result = self.wait_until_idle(
                    host=host_value,
                    timeout=max(
                        20,
                        int(
                            timeout
                            - (
                                time.monotonic()
                                - started
                            )
                        ),
                    ),
                    stable_checks=2,
                )
                result["attempt"] = attempt
                return result

            if state_before == "SUITE_RUNNING":
                time.sleep(5)
                continue

            response = self.client.request(
                host=host_value,
                command="%01 ragopi goidle",
                timeout=30,
            )

            if response.error:
                last_error = response.error
                if (
                    "SUITE_RUNNING" in last_error.upper()
                    or "CAN NOT GO IDLE" in last_error.upper()
                    or "CANNOT GO IDLE" in last_error.upper()
                ):
                    time.sleep(5)
                    continue

                time.sleep(3)
                continue

            try:
                remaining = max(
                    20,
                    int(
                        timeout
                        - (
                            time.monotonic()
                            - started
                        )
                    ),
                )
                result = self.wait_until_idle(
                    host=host_value,
                    timeout=remaining,
                    stable_checks=2,
                )
                result["attempt"] = attempt
                return result

            except RuntimeError as exc:
                last_error = str(exc)
                time.sleep(4)

        raise RuntimeError(
            "Unable to release the automation runner"
            + (
                f": {last_error}"
                if last_error
                else (
                    ". Runner did not become releasable "
                    "within the configured timeout."
                )
            )
        )

    def start_collection(
        self,
        host: str,
        collection: str,
    ) -> dict[str, Any]:
        host_value = str(
            host or self.default_host
        ).strip()

        collection_value = str(
            collection or ""
        ).strip()

        if not host_value:
            raise ValueError(
                "Execution host is required."
            )

        if not collection_value:
            raise ValueError(
                "Collection name is required."
            )

        with self._lock:
            active = [
                session
                for session in self._sessions.values()
                if (
                    session.get("host") == host_value
                    and session.get("state")
                    in {
                        "CREATED",
                        "INITIALIZING",
                        "RUNNING",
                    }
                )
            ]

            if active:
                raise RuntimeError(
                    "The selected host already has an active session."
                )

            session_id = str(uuid.uuid4())

            self._sessions[session_id] = {
                "session_id": session_id,
                "host": host_value,
                "collection": collection_value,
                "state": "CREATED",
                "message": "Runner session created.",
                "machine_state": "",
                "current_test": "",
                "total_tests": 0,
                "completed_tests": 0,
                "progress_percentage": 0.0,
                "testcases": {},
                "error": "",
                "cancel_requested": False,
                "created_at": self._utc_now(),
                "started_at": "",
                "completed_at": "",
            }

        worker = threading.Thread(
            target=self._run_collection,
            args=(session_id,),
            daemon=True,
            name=f"automation-runner-{session_id[:8]}",
        )

        worker.start()

        return self.get_session(session_id) or {}

    def get_session(
        self,
        session_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(session_id)

            if not session:
                return None

            result = dict(session)
            result["testcases"] = dict(
                session.get("testcases", {})
            )

            return result

    def cancel_session(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(session_id)

            self._sessions[
                session_id
            ]["cancel_requested"] = True

            return dict(
                self._sessions[session_id]
            )

    def get_testcase_details(
        self,
        host: str,
        testcase: str,
    ) -> dict[str, Any]:
        """
        Fetch the complete testcase information returned by
        CiscoAutomationRunner.

        We return the raw XML first so that we can identify the exact
        XML tags used for Test Case Log, Documentation, Data Files
        and History.
        """
        host_value = str(
            host or self.default_host
        ).strip()

        testcase_value = str(
            testcase or ""
        ).strip()

        if not host_value:
            raise ValueError(
                "Execution host is required."
            )

        if not testcase_value:
            raise ValueError(
                "Testcase name is required."
            )

        response = self.client.request(
            host=host_value,
            command=(
                "%01 ragopi caseinfo "
                f"{testcase_value}"
            ),
            timeout=60,
        )

        if response.error:
            raise RuntimeError(
                "Unable to fetch testcase information: "
                f"{response.error}"
            )

        return {
            "host": host_value,
            "testcase": testcase_value,
            "raw_xml": response.data,
        }