from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

import requests
from openai import AzureOpenAI


class CiscoTokenManager:
    """
    Generates and caches a temporary Cisco OAuth access token.

    The token is refreshed automatically before it expires.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str,
    ) -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.token_url = token_url.strip()

        self._access_token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _validate_configuration(self) -> None:
        missing: list[str] = []

        if not self.client_id:
            missing.append("CISCO_AI_CLIENT_ID")

        if not self.client_secret:
            missing.append("CISCO_AI_CLIENT_SECRET")

        if not self.token_url:
            missing.append("CISCO_AI_TOKEN_URL")

        if missing:
            raise RuntimeError(
                "Missing Cisco AI configuration: "
                + ", ".join(missing)
            )

    def _token_is_valid(self) -> bool:
        """
        Keep a 60-second safety margin before token expiry.
        """
        return bool(
            self._access_token
            and time.time() < self._expires_at - 60
        )

    def get_access_token(
        self,
        force_refresh: bool = False,
    ) -> str:
        self._validate_configuration()

        with self._lock:
            if (
                not force_refresh
                and self._token_is_valid()
            ):
                return self._access_token

            try:
                response = requests.post(
                    self.token_url,
                    data={
                        "grant_type": (
                            "client_credentials"
                        ),
                    },
                    auth=(
                        self.client_id,
                        self.client_secret,
                    ),
                    headers={
                        "Accept": "application/json",
                    },
                    timeout=30,
                )

                response.raise_for_status()

            except requests.RequestException as exc:
                response_text = ""

                if getattr(exc, "response", None) is not None:
                    response_text = (
                        exc.response.text[:1000]
                    )

                raise RuntimeError(
                    "Could not obtain Cisco AI access token. "
                    f"{response_text or str(exc)}"
                ) from exc

            try:
                payload = response.json()

            except ValueError as exc:
                raise RuntimeError(
                    "Cisco token service returned "
                    "an invalid JSON response."
                ) from exc

            access_token = str(
                payload.get("access_token", "")
            ).strip()

            if not access_token:
                raise RuntimeError(
                    "Cisco token response did not contain "
                    "an access_token."
                )

            try:
                expires_in = int(
                    payload.get("expires_in", 3600)
                )

            except (TypeError, ValueError):
                expires_in = 3600

            self._access_token = access_token
            self._expires_at = (
                time.time() + expires_in
            )

            return self._access_token


class CfdAiService:
    """
    Understands a defect and converts it into structured
    technical information for automation test discovery.
    """

    GENERIC_WORDS = {
        "access",
        "agent",
        "application",
        "check",
        "date",
        "error",
        "failed",
        "failure",
        "issue",
        "module",
        "problem",
        "product",
        "retrieve",
        "retrieving",
        "secure",
        "seconds",
        "service",
        "system",
        "test",
        "update",
        "up",
        "wait",
        "waiting",
    }

    def __init__(
        self,
        token_manager: CiscoTokenManager,
        endpoint: str,
        api_version: str,
        app_key: str,
        model: str,
    ) -> None:
        self.token_manager = token_manager
        self.endpoint = endpoint.strip()
        self.api_version = api_version.strip()
        self.app_key = app_key.strip()
        self.model = model.strip()

    def _validate_configuration(self) -> None:
        missing: list[str] = []

        if not self.endpoint:
            missing.append("CISCO_AI_ENDPOINT")

        if not self.api_version:
            missing.append("CISCO_AI_API_VERSION")

        if not self.app_key:
            missing.append("CISCO_AI_APP_KEY")

        if not self.model:
            missing.append("CISCO_AI_MODEL")

        if missing:
            raise RuntimeError(
                "Missing Cisco AI configuration: "
                + ", ".join(missing)
            )

    def _client(
        self,
        force_token_refresh: bool = False,
    ) -> AzureOpenAI:
        self._validate_configuration()

        access_token = (
            self.token_manager.get_access_token(
                force_refresh=force_token_refresh
            )
        )

        return AzureOpenAI(
            azure_endpoint=self.endpoint,
            api_key=access_token,
            api_version=self.api_version,
        )

    @staticmethod
    def _strip_json_fence(value: str) -> str:
        text = value.strip()

        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"\s*```$",
            "",
            text,
        )

        return text.strip()

    @classmethod
    def _clean_keywords(
        cls,
        values: Any,
    ) -> list[str]:
        if not isinstance(values, list):
            return []

        cleaned: list[str] = []
        existing: set[str] = set()

        for value in values:
            keyword = str(value or "").strip()

            if not keyword:
                continue

            lowered = keyword.lower()

            if lowered in cls.GENERIC_WORDS:
                continue

            if lowered in existing:
                continue

            existing.add(lowered)
            cleaned.append(keyword)

        return cleaned[:20]

    @staticmethod
    def _clean_string_list(
        values: Any,
        maximum: int = 20,
    ) -> list[str]:
        if not isinstance(values, list):
            return []

        cleaned: list[str] = []
        existing: set[str] = set()

        for value in values:
            text = str(value or "").strip()

            if not text:
                continue

            lowered = text.lower()

            if lowered in existing:
                continue

            existing.add(lowered)
            cleaned.append(text)

        return cleaned[:maximum]

    def _create_completion(
        self,
        messages: list[dict[str, str]],
        force_token_refresh: bool = False,
    ) -> Any:
        client = self._client(
            force_token_refresh=force_token_refresh
        )

        return client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=messages,
            user=json.dumps(
                {
                    "appkey": self.app_key,
                }
            ),
        )

    def analyze(
        self,
        defect_text: str,
        selected_component: str = "",
        repository_components: list[str] | None = None,
    ) -> dict[str, Any]:
        description = defect_text.strip()

        if not description:
            raise ValueError(
                "Defect description is required."
            )

        allowed_components = (
            repository_components or []
        )

        component_text = (
            ", ".join(allowed_components)
            if allowed_components
            else "Not provided"
        )

        prompt = f"""
You are analyzing a software defect to find the most relevant
automation test cases from a large Python automation repository.

Return only valid JSON. Do not return markdown or explanations
outside the JSON.

Repository component folders:
{component_text}

Component manually selected by the user:
{selected_component or "Not selected"}

Defect:
{description}

Understand the technical meaning of the defect. Do not rely only
on generic keyword matching.

Required JSON format:
{{
  "repository_component": "",
  "product_area": "",
  "feature": "",
  "technology": [],
  "operation": "",
  "failure_signatures": [],
  "symptoms": [],
  "platforms": [],
  "strong_keywords": [],
  "weak_keywords": [],
  "exclude_keywords": [],
  "focused_search_query": "",
  "analysis_summary": ""
}}

Rules:

1. repository_component must be one of the provided repository
   component folder names whenever a reasonable match exists.

2. strong_keywords must contain specific technical terms such as
   product names, error constants, APIs, executables, protocols,
   profile names, technology names and exact operations.

3. Do not place generic terms such as error, failed, issue,
   access, date, up, update, test, agent, service or seconds
   into strong_keywords unless they form a specific phrase.

4. Preserve exact identifiers such as
   WAAPI_ERROR_ACCESS_DENIED, Cortex XDR, DTLS, eBPF,
   acnvmagent, OPSWAT, CMID and profile filenames.

5. focused_search_query must be short and contain only the
   strongest concepts useful for finding related automation.

6. exclude_keywords should identify misleading concepts that
   appear similar but are unrelated.

7. Infer the likely repository component using the defect and
   the available component list.
"""

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a senior QA automation architect. "
                    "Return strictly valid JSON."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        try:
            response = self._create_completion(
                messages=messages
            )

        except Exception as first_error:
            error_text = str(first_error).lower()

            token_error_indicators = (
                "401",
                "unauthorized",
                "authentication",
                "expired",
                "invalid token",
            )

            should_retry = any(
                indicator in error_text
                for indicator in token_error_indicators
            )

            if not should_retry:
                raise RuntimeError(
                    "Cisco AI request failed: "
                    f"{first_error}"
                ) from first_error

            try:
                response = self._create_completion(
                    messages=messages,
                    force_token_refresh=True,
                )

            except Exception as retry_error:
                raise RuntimeError(
                    "Cisco AI request failed after "
                    "refreshing the access token: "
                    f"{retry_error}"
                ) from retry_error

        if (
            not response.choices
            or not response.choices[0].message
        ):
            raise RuntimeError(
                "Cisco AI returned an empty response."
            )

        raw_content = (
            response.choices[0].message.content
            or ""
        )

        cleaned_content = self._strip_json_fence(
            raw_content
        )

        try:
            result = json.loads(cleaned_content)

        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Cisco AI returned invalid JSON: "
                f"{cleaned_content[:1000]}"
            ) from exc

        if not isinstance(result, dict):
            raise RuntimeError(
                "Cisco AI response must be a JSON object."
            )

        result["repository_component"] = str(
            result.get(
                "repository_component",
                "",
            )
            or ""
        ).strip()

        result["product_area"] = str(
            result.get(
                "product_area",
                "",
            )
            or ""
        ).strip()

        result["feature"] = str(
            result.get(
                "feature",
                "",
            )
            or ""
        ).strip()

        result["operation"] = str(
            result.get(
                "operation",
                "",
            )
            or ""
        ).strip()

        result["focused_search_query"] = str(
            result.get(
                "focused_search_query",
                "",
            )
            or ""
        ).strip()

        result["analysis_summary"] = str(
            result.get(
                "analysis_summary",
                "",
            )
            or ""
        ).strip()

        result["technology"] = (
            self._clean_string_list(
                result.get("technology")
            )
        )

        result["failure_signatures"] = (
            self._clean_string_list(
                result.get(
                    "failure_signatures"
                )
            )
        )

        result["symptoms"] = (
            self._clean_string_list(
                result.get("symptoms")
            )
        )

        result["platforms"] = (
            self._clean_string_list(
                result.get("platforms")
            )
        )

        result["strong_keywords"] = (
            self._clean_keywords(
                result.get("strong_keywords")
            )
        )

        result["weak_keywords"] = (
            self._clean_string_list(
                result.get("weak_keywords")
            )
        )

        result["exclude_keywords"] = (
            self._clean_string_list(
                result.get("exclude_keywords")
            )
        )

        if not result["focused_search_query"]:
            fallback_parts = [
                result["feature"],
                result["operation"],
                *result["technology"],
                *result["failure_signatures"],
                *result["strong_keywords"],
            ]

            result["focused_search_query"] = " ".join(
                part
                for part in fallback_parts
                if part
            )

        return result


def create_cfd_ai_service() -> CfdAiService:
    client_id = os.getenv(
        "CISCO_AI_CLIENT_ID",
        "",
    )

    client_secret = os.getenv(
        "CISCO_AI_CLIENT_SECRET",
        "",
    )

    token_url = os.getenv(
        "CISCO_AI_TOKEN_URL",
        "https://id.cisco.com/oauth2/default/v1/token",
    )

    endpoint = os.getenv(
        "CISCO_AI_ENDPOINT",
        "https://chat-ai.cisco.com",
    )

    api_version = os.getenv(
        "CISCO_AI_API_VERSION",
        "2025-04-01-preview",
    )

    app_key = os.getenv(
        "CISCO_AI_APP_KEY",
        "",
    )

    model = os.getenv(
        "CISCO_AI_MODEL",
        "gpt-5-nano",
    )

    token_manager = CiscoTokenManager(
        client_id=client_id,
        client_secret=client_secret,
        token_url=token_url,
    )

    return CfdAiService(
        token_manager=token_manager,
        endpoint=endpoint,
        api_version=api_version,
        app_key=app_key,
        model=model,
    )