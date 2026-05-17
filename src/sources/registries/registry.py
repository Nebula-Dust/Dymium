"""Source registry metadata and adapter lookup."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
DEFAULT_SOURCE_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "sources" / "registry.json"
class SourceRegistry:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_SOURCE_REGISTRY_PATH
        self.payload = self._load(self.path)
        self.version = str(self.payload.get("version", "unknown"))
        self.sources = {str(key): value for key, value in self.payload.get("sources", {}).items()}
    def get(self, source_name: str | None) -> dict[str, Any]:
        if not source_name:
            return {}
        for key, value in self.sources.items():
            if key.lower() == str(source_name).lower():
                return {"source_name": key, **value}
        return {}
    def adapter_name(self, source_name: str | None) -> str | None:
        return self.get(source_name).get("adapter")
    def trust_level(self, source_name: str | None, default: float = 0.45) -> float:
        try:
            return float(self.get(source_name).get("trust_level", default))
        except (TypeError, ValueError):
            return default
    def reconciliation_rules(self, source_name: str | None) -> dict[str, Any]:
        value = self.get(source_name).get("reconciliation_rules", {})
        return value if isinstance(value, dict) else {}
    def source_names(self) -> list[str]:
        return sorted(self.sources)
    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"version": "missing", "sources": {}}
        return json.loads(path.read_text(encoding="utf-8"))
