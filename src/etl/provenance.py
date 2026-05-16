"""Field-level provenance helpers for Dymium ETL records.

Provenance is embedded directly in records as nested metadata. Each field can
answer where the value came from, how it was transformed, how confident the
pipeline is, and which source superseded conflicting candidates.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

PROVENANCE_SCHEMA_VERSION = "dymium-field-lineage-v1"
PROVENANCE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://dymium.local/provenance")

SOURCE_PRIORITIES = {
    "operator": 100,
    "mrds": 90,
    "mrds+pdf": 85,
    "fusion": 80,
    "geology": 70,
    "pdf": 60,
    "unknown": 0,
}


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp for provenance events."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def deterministic_uuid(*parts: Any) -> str:
    """Generate a stable UUID from deterministic provenance inputs."""

    normalized = "|".join(_value_repr(part) for part in parts if part is not None)
    return str(uuid.uuid5(PROVENANCE_NAMESPACE, normalized))


def empty_provenance(*, record_uuid: str | None = None, timestamp: str | None = None) -> dict[str, Any]:
    stamp = timestamp or utc_now()
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "record_uuid": record_uuid,
        "created_at": stamp,
        "updated_at": stamp,
        "fields": {},
        "record_lineage": [],
        "conflicts": [],
    }


def ensure_provenance(value: Any = None, *, record_uuid: str | None = None) -> dict[str, Any]:
    """Return a mutable provenance dictionary from dict, JSON, or None."""

    if isinstance(value, str) and value.strip():
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = None
    provenance = deepcopy(value) if isinstance(value, dict) else empty_provenance(record_uuid=record_uuid)
    provenance.setdefault("schema_version", PROVENANCE_SCHEMA_VERSION)
    provenance.setdefault("record_uuid", record_uuid)
    provenance.setdefault("created_at", utc_now())
    provenance.setdefault("updated_at", provenance.get("created_at") or utc_now())
    provenance.setdefault("fields", {})
    provenance["record_lineage"] = _listify(provenance.get("record_lineage"))
    provenance["conflicts"] = _listify(provenance.get("conflicts"))
    for field, entry in list(provenance.get("fields", {}).items()):
        if isinstance(entry, dict):
            entry["history"] = _listify(entry.get("history"))
            provenance["fields"][field] = entry
    if record_uuid and not provenance.get("record_uuid"):
        provenance["record_uuid"] = record_uuid
    return provenance


def field_event(
    field: str,
    value: Any,
    *,
    source: str,
    method: str,
    confidence: float | None = None,
    source_file: str | None = None,
    source_record_id: str | None = None,
    source_field: str | None = None,
    page: int | None = None,
    pages: list[int] | tuple[int, ...] | None = None,
    chunk_ids: list[str] | tuple[str, ...] | None = None,
    source_text_sha1: str | None = None,
    transformations: list[str] | tuple[str, ...] | None = None,
    normalization_decisions: list[str] | tuple[str, ...] | None = None,
    warnings: list[str] | tuple[str, ...] | None = None,
    supersedes: list[str] | tuple[str, ...] | None = None,
    timestamp: str | None = None,
    source_priority: int | None = None,
) -> dict[str, Any]:
    """Build an immutable field lineage event with JSON-stable values."""

    stamp = timestamp or utc_now()
    page_values = _clean_int_list(pages)
    if page is not None and page not in page_values:
        page_values = [page, *page_values]
    event_seed = [field, source, method, source_file, source_record_id, source_field, page_values, chunk_ids, _value_repr(value)]
    return {
        "event_id": deterministic_uuid("field-event", *event_seed),
        "field": field,
        "value_repr": _value_repr(value),
        "source": source,
        "source_file": source_file,
        "source_record_id": source_record_id,
        "source_field": source_field,
        "page": page_values[0] if page_values else None,
        "pages": page_values,
        "chunk_ids": _clean_string_list(chunk_ids),
        "source_text_sha1": source_text_sha1,
        "method": method,
        "confidence": _clean_confidence(confidence),
        "timestamp": stamp,
        "source_priority": source_priority if source_priority is not None else source_priority_for(source),
        "transformations": _clean_string_list(transformations),
        "normalization_decisions": _clean_string_list(normalization_decisions),
        "warnings": _clean_string_list(warnings),
        "supersedes": _clean_string_list(supersedes),
    }


def set_field(provenance: dict[str, Any], field: str, event: dict[str, Any]) -> dict[str, Any]:
    """Set current field provenance and append the event to field history."""

    provenance = ensure_provenance(provenance)
    fields = provenance.setdefault("fields", {})
    existing = fields.get(field, {})
    history = list(existing.get("history", []))
    if not any(item.get("event_id") == event.get("event_id") for item in history if isinstance(item, dict)):
        history.append(event)
    fields[field] = {**_current_view(event), "history": history}
    provenance["updated_at"] = event.get("timestamp") or utc_now()
    return provenance


def append_field_history(provenance: dict[str, Any], field: str, event: dict[str, Any], *, make_current: bool = False) -> dict[str, Any]:
    """Append a lineage event and optionally make it the current field source."""

    provenance = ensure_provenance(provenance)
    current = provenance.setdefault("fields", {}).get(field)
    if current is None or make_current:
        return set_field(provenance, field, event)
    history = list(current.get("history", []))
    if not any(item.get("event_id") == event.get("event_id") for item in history if isinstance(item, dict)):
        history.append(event)
    current["history"] = history
    provenance["fields"][field] = current
    provenance["updated_at"] = event.get("timestamp") or utc_now()
    return provenance


def append_lineage(
    provenance: dict[str, Any],
    *,
    step: str,
    method: str,
    inputs: list[str] | tuple[str, ...] | None = None,
    outputs: list[str] | tuple[str, ...] | None = None,
    confidence: float | None = None,
    details: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Append an immutable record-level transformation event."""

    provenance = ensure_provenance(provenance)
    stamp = timestamp or utc_now()
    event = {
        "event_id": deterministic_uuid("lineage-event", step, method, inputs, outputs, details),
        "step": step,
        "method": method,
        "inputs": _clean_string_list(inputs),
        "outputs": _clean_string_list(outputs),
        "confidence": _clean_confidence(confidence),
        "timestamp": stamp,
        "details": details or {},
    }
    lineage = provenance.setdefault("record_lineage", [])
    if not any(item.get("event_id") == event["event_id"] for item in lineage if isinstance(item, dict)):
        lineage.append(event)
    provenance["updated_at"] = stamp
    return provenance


def add_conflict(
    provenance: dict[str, Any],
    *,
    field: str,
    candidates: list[dict[str, Any]],
    chosen_event_id: str | None,
    resolution: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Record a resolved field-level conflict."""

    provenance = ensure_provenance(provenance)
    stamp = timestamp or utc_now()
    conflict = {
        "conflict_id": deterministic_uuid("conflict", field, candidates, chosen_event_id, resolution),
        "field": field,
        "candidates": candidates,
        "chosen_event_id": chosen_event_id,
        "resolution": resolution,
        "timestamp": stamp,
    }
    conflicts = provenance.setdefault("conflicts", [])
    if not any(item.get("conflict_id") == conflict["conflict_id"] for item in conflicts if isinstance(item, dict)):
        conflicts.append(conflict)
    provenance["updated_at"] = stamp
    return provenance


def merge_field_histories(
    target: dict[str, Any],
    field: str,
    *sources: dict[str, Any] | None,
    chosen_source: str | None = None,
) -> dict[str, Any]:
    """Copy field histories from source provenance objects into target."""

    target = ensure_provenance(target)
    candidates: list[dict[str, Any]] = []
    for source_provenance in sources:
        source_provenance = ensure_provenance(source_provenance)
        entry = source_provenance.get("fields", {}).get(field)
        if not entry:
            continue
        history = entry.get("history") or [_event_from_current(field, entry)]
        for event in history:
            if isinstance(event, dict):
                candidates.append(event)
    if not candidates:
        return target

    chosen = _choose_event(candidates, chosen_source=chosen_source)
    for event in candidates:
        target = append_field_history(target, field, event, make_current=event.get("event_id") == chosen.get("event_id"))
    if len({_candidate_value(event) for event in candidates}) > 1:
        target = add_conflict(
            target,
            field=field,
            candidates=[_conflict_candidate(event) for event in candidates],
            chosen_event_id=chosen.get("event_id"),
            resolution=f"chosen_by_source_priority:{chosen_source or chosen.get('source')}",
        )
    return target


def source_priority_for(source: str | None) -> int:
    return SOURCE_PRIORITIES.get(str(source or "unknown").lower(), SOURCE_PRIORITIES["unknown"])


def serialize_provenance(value: Any) -> str:
    return json.dumps(ensure_provenance(value), sort_keys=True, separators=(",", ":"), default=str)


def deserialize_provenance(value: Any) -> dict[str, Any]:
    return ensure_provenance(value)


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "tolist"):
        converted = value.tolist()
        return converted if isinstance(converted, list) else [converted]
    return [value]


def _choose_event(candidates: list[dict[str, Any]], *, chosen_source: str | None) -> dict[str, Any]:
    if chosen_source:
        for event in candidates:
            if str(event.get("source", "")).lower() == chosen_source.lower():
                return event
    return sorted(candidates, key=lambda event: (event.get("source_priority") or 0, event.get("confidence") or 0), reverse=True)[0]


def _current_view(event: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "event_id",
        "source",
        "source_file",
        "source_record_id",
        "source_field",
        "page",
        "pages",
        "chunk_ids",
        "source_text_sha1",
        "method",
        "confidence",
        "timestamp",
        "source_priority",
        "transformations",
        "normalization_decisions",
        "warnings",
        "supersedes",
        "value_repr",
    )
    return {key: event.get(key) for key in keys}


def _event_from_current(field: str, entry: dict[str, Any]) -> dict[str, Any]:
    return {"field": field, **{key: value for key, value in entry.items() if key != "history"}}


def _conflict_candidate(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "source": event.get("source"),
        "source_priority": event.get("source_priority"),
        "confidence": event.get("confidence"),
        "value_repr": event.get("value_repr"),
    }


def _candidate_value(event: dict[str, Any]) -> str:
    return str(event.get("value_repr"))


def _clean_confidence(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(number, 1.0)), 4)


def _clean_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    cleaned: list[int] = []
    for item in values:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number not in cleaned:
            cleaned.append(number)
    return cleaned


def _clean_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    cleaned: list[str] = []
    for item in values:
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _value_repr(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    except TypeError:
        return str(value)
