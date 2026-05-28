"""Shared helpers for AI curation return values."""

import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from .suggestion_service import extract_suggested_tracks, missing_recommendations_enabled


def strip_markdown_json_fences(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def extract_balanced_json_object(text: str, start: int = 0) -> Optional[str]:
    """Return the first complete {...} substring, respecting quoted strings."""
    open_brace = text.find("{", start)
    if open_brace < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[open_brace:], start=open_brace):
        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace : index + 1]
    return None


def sanitize_json_for_parse(json_str: str) -> str:
    cleaned_lines = []
    for line in json_str.split("\n"):
        if "//" in line and "http://" not in line and "https://" not in line:
            comment_pos = line.find("//")
            line = line[:comment_pos].rstrip()
        line = re.sub(r",(\s*[\]}])", r"\1", line)
        if line.strip():
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def parse_ai_curation_response(content: str) -> Dict[str, Any]:
    """
    Parse AI playlist curation JSON, including responses with nested suggested_tracks.

    Older regex extraction stopped at the first '}', which broke whenever the model
    returned suggestion objects after track_ids and reasoning.
    """
    cleaned = strip_markdown_json_fences(content)
    candidates: List[str] = [cleaned]
    sanitized = sanitize_json_for_parse(cleaned)
    if sanitized != cleaned:
        candidates.append(sanitized)

    balanced = extract_balanced_json_object(cleaned)
    if balanced:
        candidates.append(balanced)
        balanced_sanitized = sanitize_json_for_parse(balanced)
        if balanced_sanitized != balanced:
            candidates.append(balanced_sanitized)

    last_error: Optional[json.JSONDecodeError] = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"track_ids": data}

    array_match = re.search(r"\[([\d\s,]+)\]", cleaned, re.DOTALL)
    if array_match:
        try:
            data = json.loads(array_match.group(0))
            if isinstance(data, list):
                return {"track_ids": data}
        except json.JSONDecodeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise json.JSONDecodeError("No valid JSON object found", cleaned, 0)


def finish_curation(
    track_ids: List[str],
    reasoning: str,
    response_data: Dict[str, Any],
    include_reasoning: bool,
) -> Union[List[str], Tuple[List[str], str], Tuple[List[str], str, List[Dict[str, Any]]]]:
    suggested = extract_suggested_tracks(response_data)
    if include_reasoning and missing_recommendations_enabled():
        return track_ids, reasoning, suggested
    if include_reasoning:
        return track_ids, reasoning
    return track_ids
