"""Government board vacancy source adapter.

Fetches board and committee vacancies from a configurable government
appointments API endpoint (e.g. Australian Government Board Vacancies).

Source URL
----------
Default: https://api.boardvacancies.gov.au/v1/vacancies
Override via constructor parameter or GOV_BOARD_VACANCY_URL env var.

Permissions
-----------
No authentication required for public vacancy listings.
Some endpoints may require an API key passed via GOV_BOARD_VACANCY_API_KEY.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import requests

from boardmatch.ingestion.base import (
    SourceAuthError,
    SourceError,
    SourceRateLimitError,
    SourceTimeoutError,
)
from boardmatch.models import Opportunity, Remuneration

_DEFAULT_URL = "https://api.boardvacancies.gov.au/v1/vacancies"
_DEFAULT_TIMEOUT = 30  # seconds


class GovBoardVacancySource:
    """Adapter that retrieves board vacancies from a government API.

    Implements the :class:`~boardmatch.ingestion.base.OpportunitySource`
    protocol.

    Parameters
    ----------
    url : str, optional
        API endpoint URL. Falls back to GOV_BOARD_VACANCY_URL env var,
        then to the built-in default.
    api_key : str, optional
        API key for authenticated endpoints. Falls back to
        GOV_BOARD_VACANCY_API_KEY env var.
    timeout : int, optional
        HTTP request timeout in seconds (default 30).
    """

    source_key: str = "gov_board_vacancies"

    def __init__(
        self,
        url: Optional[str] = None,
        *,
        api_key: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self.url = url or os.environ.get("GOV_BOARD_VACANCY_URL", _DEFAULT_URL)
        self.api_key = api_key or os.environ.get("GOV_BOARD_VACANCY_API_KEY")
        self.timeout = timeout

    def fetch(self) -> list[Opportunity]:
        """Fetch and normalise opportunities from the government API.

        Returns
        -------
        list[Opportunity]
            Normalised opportunity records. Malformed individual records
            are skipped; a completely unparseable response raises SourceError.

        Raises
        ------
        SourceTimeoutError
            When the upstream API does not respond within the timeout.
        SourceAuthError
            When the API returns 401 or 403.
        SourceRateLimitError
            When the API returns 429.
        SourceError
            For any other HTTP or parsing failure.
        """
        data = self._request()
        return self._parse(data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(self) -> list[dict[str, Any]]:
        """Perform the HTTP GET and return parsed JSON."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = requests.get(
                self.url, headers=headers, timeout=self.timeout
            )
        except requests.exceptions.Timeout as exc:
            raise SourceTimeoutError(
                f"Timed out after {self.timeout}s fetching {self.url}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise SourceError(
                f"HTTP request failed for {self.url}: {exc}"
            ) from exc

        if response.status_code == 429:
            raise SourceRateLimitError(
                f"Rate limited by {self.url} (HTTP 429)"
            )
        if response.status_code in (401, 403):
            raise SourceAuthError(
                f"Auth failed for {self.url} (HTTP {response.status_code})"
            )
        if response.status_code != 200:
            raise SourceError(
                f"Unexpected HTTP {response.status_code} from {self.url}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(
                f"Invalid JSON response from {self.url}"
            ) from exc

        # Support both bare list and {"results": [...]} envelope
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and "results" in payload:
            return payload["results"]
        raise SourceError(
            f"Unexpected response structure from {self.url}"
        )

    def _parse(self, records: list[dict[str, Any]]) -> list[Opportunity]:
        """Normalise raw API records into Opportunity domain objects."""
        opportunities: list[Opportunity] = []
        for record in records:
            try:
                opp = self._to_opportunity(record)
                opportunities.append(opp)
            except (KeyError, ValueError, TypeError):
                # Skip malformed individual records
                continue
        return opportunities

    def _to_opportunity(self, data: dict[str, Any]) -> Opportunity:
        """Convert a single API record to an Opportunity."""
        remuneration_raw = data.get("remuneration", "unknown").lower()
        try:
            remuneration = Remuneration(remuneration_raw)
        except ValueError:
            remuneration = Remuneration.UNKNOWN

        return Opportunity(
            id=str(data["id"]),
            title=data["title"],
            organisation=data["organisation"],
            sector=data.get("sector", "Government"),
            location=data.get("location", "Australia"),
            source=self.source_key,
            url=data.get("url", f"{self.url}/{data['id']}"),
            remuneration=remuneration,
            fee_aud=data.get("fee_aud"),
            closes_on=data.get("closes_on"),
            summary=data.get("summary", ""),
            required_skills=tuple(data.get("required_skills", ())),
            desirable_skills=tuple(data.get("desirable_skills", ())),
        )
