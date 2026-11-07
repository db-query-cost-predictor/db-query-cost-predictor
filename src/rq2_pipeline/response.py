"""Strict provider response contract; no legacy generation paths."""
import json

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GenerationError(RuntimeError):
    """Sanitized provider failure suitable for the user-facing report."""


def parse_response(response, model):
    if not isinstance(response, dict):
        raise GenerationError("Groq response must be an object")
    if response.get("model") != model:
        raise GenerationError("Groq returned a different model; response preserved for review")
    choices = response.get("choices")
    if (not isinstance(choices, list) or len(choices) != 1
            or not isinstance(choices[0], dict) or choices[0].get("finish_reason") != "stop"):
        raise GenerationError("Groq response incomplete or refused; no candidate accepted")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise GenerationError("Groq response must contain text content")
    try:
        candidate = json.loads(message["content"], object_pairs_hook=_unique_fields)
    except (KeyError, TypeError, ValueError):
        raise GenerationError("Groq response is not a valid JSON candidate") from None
    if not isinstance(candidate, dict) or set(candidate) != {"sql", "explanation"}:
        raise GenerationError("Groq candidate must contain exactly sql and explanation")
    if not all(isinstance(v, str) and v.strip() for v in candidate.values()):
        raise GenerationError("Groq candidate fields must be nonempty strings")
    return candidate


def _unique_fields(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise GenerationError("Groq candidate contains duplicate JSON fields")
        result[name] = value
    return result
