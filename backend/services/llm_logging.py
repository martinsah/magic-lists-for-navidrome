"""
Human-readable logging for LLM requests and responses.

Enable with AI_VERBOSE_LOGGING=true (default). Set AI_VERBOSE_LOGGING=false to disable.
Logs go to the 'llm' logger (inherits scheduler / root handlers from main.py).
"""

from __future__ import annotations

import json
import logging
import os
import re
import textwrap
from typing import Any, Dict, List, Optional

logger = logging.getLogger("llm")

_SEPARATOR = "─" * 72
_BLOCK_WIDTH = 100


def is_verbose() -> bool:
    return os.getenv("AI_VERBOSE_LOGGING", "true").lower() not in ("0", "false", "no", "off")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def infer_call_label(system_prompt: str, user_prompt: str) -> str:
    combined = f"{system_prompt}\n{user_prompt}".lower()
    if "meta-genre" in combined or "raw_genres=" in combined:
        return "meta_genre_distillation"
    if "genre_mix:" in combined or "genre mix" in combined:
        return "genre_mix"
    if "re-discover" in combined or "rediscover" in combined:
        return "rediscover"
    if "this is" in combined or "artist" in combined[:200]:
        return "this_is"
    return "generic"


def _wrap_block(title: str, body: str, indent: str = "  ") -> str:
    lines = [f"{indent}{title}"]
    if not body:
        lines.append(f"{indent}  (empty)")
        return "\n".join(lines)
    wrapped = textwrap.fill(
        body,
        width=_BLOCK_WIDTH,
        initial_indent=indent + "  ",
        subsequent_indent=indent + "  ",
        break_long_words=False,
        break_on_hyphens=False,
    )
    lines.append(wrapped)
    return "\n".join(lines)


def _try_parse_json(content: str) -> Optional[Any]:
    text = (content or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def summarize_parsed_response(data: Any) -> List[str]:
    """Build human-readable bullets from a parsed JSON response."""
    lines: List[str] = []
    if not isinstance(data, dict):
        if isinstance(data, list):
            lines.append(f"JSON array with {len(data)} items")
        return lines

    if "groups" in data:
        groups = data.get("groups") or []
        lines.append(f"Meta-genre groups: {len(groups)}")
        for idx, group in enumerate(groups[:12]):
            name = group.get("meta_genre", "?")
            members = group.get("genres") or []
            count = group.get("total_song_count", "?")
            preview = ", ".join(str(m) for m in members[:4])
            if len(members) > 4:
                preview += f", … (+{len(members) - 4} more)"
            lines.append(f"  [{idx + 1}] {name} ({len(members)} genres, {count} tracks): {preview}")
        if len(groups) > 12:
            lines.append(f"  … and {len(groups) - 12} more groups")

    if "track_ids" in data:
        track_ids = data.get("track_ids") or []
        lines.append(f"Selected track indices: {len(track_ids)}")
        if track_ids:
            preview = track_ids[:20]
            suffix = f" … (+{len(track_ids) - 20} more)" if len(track_ids) > 20 else ""
            lines.append(f"  Indices: {preview}{suffix}")

    if "reasoning" in data and data.get("reasoning"):
        reasoning = str(data["reasoning"])
        lines.append(f"Reasoning ({len(reasoning)} chars): {reasoning[:500]}")
        if len(reasoning) > 500:
            lines.append(f"  … ({len(reasoning) - 500} more characters)")

    suggested = data.get("suggested_tracks")
    if isinstance(suggested, list) and suggested:
        lines.append(f"Suggested missing tracks: {len(suggested)}")
        for idx, track in enumerate(suggested[:8]):
            title = track.get("title", "?")
            artist = track.get("artist", "?")
            album = track.get("album", "")
            album_bit = f" — {album}" if album else ""
            lines.append(f"  [{idx + 1}] {artist} - {title}{album_bit}")
        if len(suggested) > 8:
            lines.append(f"  … and {len(suggested) - 8} more suggestions")

    if "mode" in data:
        lines.append(f"Mode: {data.get('mode')}")
    if "analysis_summary" in data:
        summary = str(data.get("analysis_summary", ""))[:400]
        lines.append(f"Analysis: {summary}")

    if not lines:
        keys = list(data.keys())[:10]
        lines.append(f"JSON object keys: {', '.join(keys)}")

    return lines


def format_usage(usage: Optional[Dict[str, Any]]) -> str:
    if not usage:
        return "  (usage metadata not available)"
    parts = []
    for key in (
        "promptTokenCount",
        "candidatesTokenCount",
        "totalTokenCount",
        "thoughtsTokenCount",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    ):
        if key in usage and usage[key] is not None:
            parts.append(f"{key}={usage[key]}")
    return "  " + ", ".join(parts) if parts else "  (empty usage metadata)"


def log_llm_request(
    *,
    provider: str,
    model: str,
    label: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    json_response: bool = False,
    include_suggestions: bool = False,
    attempt: int = 1,
    max_attempts: int = 1,
) -> None:
    if not is_verbose():
        return

    user_est = estimate_tokens(user_prompt)
    system_est = estimate_tokens(system_prompt)
    total_est = user_est + system_est

    header = (
        f"LLM REQUEST [{label}] provider={provider} model={model} "
        f"attempt={attempt}/{max_attempts} json={json_response} suggestions={include_suggestions}"
    )
    logger.info("%s\n%s", header, _SEPARATOR)
    logger.info(
        "  Settings: max_tokens=%s temperature=%s | estimated input ~%s tokens "
        "(system ~%s + user ~%s, %s chars total)",
        max_tokens,
        temperature,
        total_est,
        system_est,
        user_est,
        len(system_prompt) + len(user_prompt),
    )
    logger.info("%s", _wrap_block("System prompt:", system_prompt))
    logger.info("%s", _wrap_block("User prompt:", _truncate_user_prompt_for_log(user_prompt)))
    logger.info("%s", _SEPARATOR)


def _truncate_user_prompt_for_log(user_prompt: str, max_chars: int = 12000) -> str:
    """Show full prompt up to max_chars; summarize embedded candidate JSON arrays."""
    if len(user_prompt) <= max_chars:
        return user_prompt

    # Genre mix / rediscover embed large JSON candidate lists
    candidates_match = re.search(
        r"(Candidates\s*\([^)]*\):\s*)(\[[\s\S]*\])",
        user_prompt,
    )
    if candidates_match:
        prefix = user_prompt[: candidates_match.start(1)]
        suffix = user_prompt[candidates_match.end(2) :]
        array_text = candidates_match.group(2)
        item_count = array_text.count('"i":')
        if item_count == 0:
            item_count = array_text.count('"id":')
        summary = (
            f"{candidates_match.group(1)}[… {item_count} candidate objects, "
            f"{len(array_text)} chars omitted from log …]"
        )
        condensed = prefix + summary + suffix
        if len(condensed) <= max_chars:
            return condensed

    return (
        user_prompt[:max_chars]
        + f"\n\n… [truncated for log: {len(user_prompt) - max_chars} more characters] …"
    )


def log_llm_response(
    *,
    provider: str,
    model: str,
    label: str,
    content: str,
    duration_ms: float,
    usage: Optional[Dict[str, Any]] = None,
    finish_reason: Optional[str] = None,
    attempt: int = 1,
) -> None:
    if not is_verbose():
        return

    parsed = _try_parse_json(content)
    out_est = estimate_tokens(content)

    header = (
        f"LLM RESPONSE [{label}] provider={provider} model={model} "
        f"attempt={attempt} duration={duration_ms:.0f}ms finish={finish_reason or 'ok'}"
    )
    logger.info("%s\n%s", header, _SEPARATOR)
    logger.info(format_usage(usage))
    logger.info("  Raw output: ~%s tokens, %s characters", out_est, len(content or ""))

    if parsed is not None:
        logger.info("  Parsed summary:")
        for line in summarize_parsed_response(parsed):
            logger.info("    %s", line)
        try:
            pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
            if len(pretty) > 8000:
                pretty = pretty[:8000] + f"\n… [truncated: {len(pretty) - 8000} more chars]"
            logger.info("  Pretty JSON:\n%s", textwrap.indent(pretty, "    "))
        except (TypeError, ValueError):
            pass
    else:
        preview = (content or "")[:2000]
        if len(content or "") > 2000:
            preview += f"\n… [truncated: {len(content) - 2000} more chars]"
        logger.info("%s", _wrap_block("Raw text (not valid JSON):", preview))

    logger.info("%s", _SEPARATOR)


def log_llm_failure(
    *,
    provider: str,
    model: str,
    label: str,
    error: Exception,
    duration_ms: float,
    attempt: int = 1,
    will_retry: bool = False,
) -> None:
    if not is_verbose():
        return
    logger.error(
        "LLM FAILURE [%s] provider=%s model=%s attempt=%s duration=%.0fms retry=%s: %s",
        label,
        provider,
        model,
        attempt,
        duration_ms,
        will_retry,
        error,
    )


def log_curation_parse_result(label: str, content: str, parsed: Dict[str, Any]) -> None:
    """Extra log after local JSON validation (ai_client)."""
    if not is_verbose():
        return
    logger.info("LLM PARSE OK [%s]", label)
    for line in summarize_parsed_response(parsed):
        logger.info("  %s", line)
