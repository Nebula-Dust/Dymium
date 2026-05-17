"""Update-aware source acquisition scaffolding."""
from __future__ import annotations
import shutil, time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from src.etl.provenance import utc_now
from src.sources.loaders.detection import file_sha256
from src.sources.schemas import SourceUpdateState
def acquire_source(uri: str, destination: str | Path, *, prior_state: SourceUpdateState | None = None, rate_limit_seconds: float = 1.0, user_agent: str = "Dymium-source-loader/0.1") -> dict[str, Any]:
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(uri)
    if parsed.scheme in {"http", "https"}:
        time.sleep(max(rate_limit_seconds, 0.0))
        request = Request(uri, headers={"User-Agent": user_agent})
        with urlopen(request, timeout=30) as response, destination_path.open("wb") as output:
            shutil.copyfileobj(response, output)
        source_uri = uri
    else:
        source_path = Path(uri)
        shutil.copy2(source_path, destination_path)
        source_uri = str(source_path)
    checksum = file_sha256(destination_path)
    changed = prior_state is None or prior_state.checksum_sha256 != checksum
    state = SourceUpdateState(source_dataset=prior_state.source_dataset if prior_state else "UNKNOWN", source_uri=source_uri, source_file=str(destination_path), checksum_sha256=checksum, last_ingested_at=utc_now())
    return {"path": str(destination_path), "checksum_sha256": checksum, "changed": changed, "state": state}
