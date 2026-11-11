import json
import re
from typing import Any

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class InvalidModelOutput(ValueError):
    pass


def parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating a ```json fence."""
    text = raw.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        raise InvalidModelOutput(f"not JSON: {e}") from e
    if not isinstance(value, dict):
        raise InvalidModelOutput("JSON is not an object")
    return value
