"""LLM-based mineral deposit extraction from geological report text."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You extract mineral deposit records from geological reports.
Return only strictly valid JSON matching this shape:
[
  {
    "site_name": "string",
    "latitude": 0.0,
    "longitude": 0.0,
    "commodities": ["gold"],
    "grade": 0.0,
    "tonnage": 0.0
  }
]
Use null for unknown numeric fields. Include only deposits, mines, districts, prospects, or occurrences that are explicitly mentioned. Do not invent coordinates."""

FEW_SHOT_PROMPT = """Examples:

Input text:
The Creede District in Colorado is a silver-lead mining district near 37.8 N, 106.9 W. Historical workings produced high-grade veins.
Output JSON:
[{"site_name":"Creede District","latitude":37.8,"longitude":-106.9,"commodities":["silver","lead"],"grade":null,"tonnage":null}]

Input text:
At the Red Mountain prospect, copper and gold mineralization occurs in altered volcanic rocks. No coordinates are listed.
Output JSON:
[{"site_name":"Red Mountain prospect","latitude":null,"longitude":null,"commodities":["copper","gold"],"grade":null,"tonnage":null}]

Input text:
The Silver Bell mine contains about 2.4 million tons grading 0.8 percent Cu at latitude 32.38 and longitude -111.50.
Output JSON:
[{"site_name":"Silver Bell mine","latitude":32.38,"longitude":-111.50,"commodities":["copper"],"grade":0.8,"tonnage":2400000}]
"""

JSON_SCHEMA: dict[str, Any] = {
    "name": "mineral_deposit_extraction",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "deposits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "site_name": {"type": ["string", "null"]},
                        "latitude": {"type": ["number", "null"]},
                        "longitude": {"type": ["number", "null"]},
                        "commodities": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "grade": {"type": ["number", "null"]},
                        "tonnage": {"type": ["number", "null"]},
                    },
                    "required": ["site_name", "latitude", "longitude", "commodities", "grade", "tonnage"],
                },
            }
        },
        "required": ["deposits"],
    },
    "strict": True,
}


def extract_deposits_from_chunk(chunk: str, *, model: str = DEFAULT_MODEL) -> list[dict[str, Any]]:
    """Extract deposit candidates from a text chunk with OpenAI structured JSON output."""

    if not chunk.strip():
        return []

    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError("LLM extraction requires the openai package. Install requirements.txt first.") from exc

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY must be set for LLM extraction.")

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{FEW_SHOT_PROMPT}\n\nExtract deposits from this text:\n{chunk}"},
        ],
        response_format={"type": "json_schema", "json_schema": JSON_SCHEMA},
        temperature=0,
    )
    content = response.choices[0].message.content or '{"deposits":[]}'
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        LOGGER.warning("OpenAI returned invalid JSON for chunk: %s", exc)
        return []

    deposits = parsed.get("deposits", parsed if isinstance(parsed, list) else [])
    return deposits if isinstance(deposits, list) else []
