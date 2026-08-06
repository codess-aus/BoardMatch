"""JSON file source adapter — wraps existing demo data files."""

from __future__ import annotations

import json
from pathlib import Path

from boardmatch.ingestion.base import SourceError
from boardmatch.ingestion.models import SourceRecord
from boardmatch.models import Opportunity, Remuneration

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class JsonFileSource:
    """Adapter that loads opportunities from a local JSON file.

    Implements the :class:`OpportunitySource` protocol.
    """

    source_key: str

    def __init__(
        self,
        filename: str,
        *,
        source_key: str | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self.filename = filename
        self.source_key = source_key or Path(filename).stem
        self._data_dir = data_dir or _DEFAULT_DATA_DIR

    @property
    def path(self) -> Path:
        return self._data_dir / self.filename

    def fetch(self) -> list[Opportunity]:
        """Load and normalise opportunities from the JSON file."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise SourceError(f"Source file not found: {self.path}") from exc
        except OSError as exc:
            raise SourceError(f"Cannot read source file: {self.path}") from exc

        try:
            records = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceError(f"Malformed JSON in {self.path}: {exc}") from exc

        opportunities: list[Opportunity] = []
        for record in records:
            try:
                opp = self._to_opportunity(record)
                opportunities.append(opp)
            except (KeyError, ValueError):
                # Skip malformed records — callers can compare count to source
                continue

        return opportunities

    def fetch_with_metadata(self) -> list[tuple[Opportunity, SourceRecord]]:
        """Fetch opportunities with source audit metadata attached."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise SourceError(f"Source file not found: {self.path}") from exc
        except OSError as exc:
            raise SourceError(f"Cannot read source file: {self.path}") from exc

        try:
            records = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceError(f"Malformed JSON in {self.path}: {exc}") from exc

        results: list[tuple[Opportunity, SourceRecord]] = []
        for record in records:
            try:
                opp = self._to_opportunity(record)
                source_record = SourceRecord(
                    source_key=self.source_key,
                    external_id=record["id"],
                    raw_data=record,
                )
                results.append((opp, source_record))
            except (KeyError, ValueError):
                continue

        return results

    def _to_opportunity(self, data: dict) -> Opportunity:
        """Convert a raw JSON dict into an Opportunity domain object."""
        return Opportunity(
            id=data["id"],
            title=data["title"],
            organisation=data["organisation"],
            sector=data["sector"],
            location=data["location"],
            source=data["source"],
            url=data["url"],
            remuneration=Remuneration(data["remuneration"]),
            fee_aud=data.get("fee_aud"),
            closes_on=data.get("closes_on"),
            summary=data.get("summary", ""),
            required_skills=tuple(data.get("required_skills", ())),
            desirable_skills=tuple(data.get("desirable_skills", ())),
        )


# Convenience pre-configured adapters for the bundled demo data
def gov_vacancies_source(data_dir: Path | None = None) -> JsonFileSource:
    """Source adapter for government vacancies demo data."""
    return JsonFileSource(
        "gov_vacancies.json", source_key="gov_vacancies", data_dir=data_dir
    )


def mock_sources_source(data_dir: Path | None = None) -> JsonFileSource:
    """Source adapter for mock sources demo data."""
    return JsonFileSource(
        "mock_sources.json", source_key="mock_sources", data_dir=data_dir
    )
