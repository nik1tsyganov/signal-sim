"""Canonical, point-in-time-safe event records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


SOURCES = {"trendradar", "worldmonitor", "quiver", "edgar", "fixture"}
SOURCE_ALIASES = {
    "house-clerk": "fixture",
    "senate-efd": "fixture",
    "sec-edgar": "edgar",
}
KINDS = {"news", "intel_brief", "congress_trade", "insider", "gov_contract"}
FILED_AT_REQUIRED = {"congress_trade", "insider"}


class EventValidationError(ValueError):
    """Raised when an event violates the canonical event contract."""


def _timestamp(value: Any, field: str, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise EventValidationError(f"{field} must be an ISO 8601 timestamp") from error
    else:
        raise EventValidationError(f"{field} must be an ISO 8601 timestamp")
    if result.tzinfo is None or result.utcoffset() is None:
        raise EventValidationError(f"{field} must include a timezone")
    return result


def _json_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


@dataclass(eq=True)
class Event:
    id: str
    source: str
    kind: str
    ticker: str
    entities: list[str]
    headline: str
    url: str
    occurred_at: datetime
    filed_at: datetime | None
    observed_at: datetime
    confidence: float
    raw_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise EventValidationError("id must be a non-empty string")
        if self.source not in SOURCES:
            raise EventValidationError(f"unsupported source: {self.source!r}")
        if self.kind not in KINDS:
            raise EventValidationError(f"unsupported kind: {self.kind!r}")
        if not isinstance(self.ticker, str):
            raise EventValidationError("ticker must be a string")
        if not isinstance(self.entities, list) or not all(isinstance(item, str) for item in self.entities):
            raise EventValidationError("entities must be a list of strings")
        for field in ("headline", "url", "raw_ref"):
            if not isinstance(getattr(self, field), str):
                raise EventValidationError(f"{field} must be a string")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise EventValidationError("confidence must be a number from 0 to 1")
        self.confidence = float(self.confidence)
        if not 0 <= self.confidence <= 1:
            raise EventValidationError("confidence must be a number from 0 to 1")
        self.occurred_at = _timestamp(self.occurred_at, "occurred_at")
        self.filed_at = _timestamp(self.filed_at, "filed_at", optional=True)
        self.observed_at = _timestamp(self.observed_at, "observed_at")
        if self.kind in FILED_AT_REQUIRED and self.filed_at is None:
            raise EventValidationError(f"filed_at is required for {self.kind}")
        if self.filed_at is not None and self.observed_at < self.filed_at:
            raise EventValidationError("observed_at must not be before filed_at")

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "Event":
        fields = {
            "id", "source", "kind", "ticker", "entities", "headline", "url",
            "occurred_at", "filed_at", "observed_at", "confidence", "raw_ref",
        }
        normalized = {field: values[field] for field in fields if field in values}
        if "source" in normalized:
            normalized["source"] = SOURCE_ALIASES.get(normalized["source"], normalized["source"])
        normalized.setdefault("headline", "")
        normalized.setdefault("url", "")
        normalized.setdefault("confidence", 0.0)
        missing = fields - normalized.keys()
        if missing:
            raise EventValidationError(f"missing event fields: {', '.join(sorted(missing))}")
        return cls(**normalized)

    @property
    def first_seen_at(self) -> datetime:
        """Docs alias for observed_at. Never use occurred_at for ordering."""
        return self.observed_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "kind": self.kind,
            "ticker": self.ticker,
            "entities": list(self.entities),
            "headline": self.headline,
            "url": self.url,
            "occurred_at": _json_timestamp(self.occurred_at),
            "filed_at": _json_timestamp(self.filed_at),
            "observed_at": _json_timestamp(self.observed_at),
            "confidence": self.confidence,
            "raw_ref": self.raw_ref,
        }
