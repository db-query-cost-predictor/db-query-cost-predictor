"""One Groq request from the pinned v2 template; never retry a generation."""

from dataclasses import asdict
import json
import os
import urllib.error
import urllib.request

from rq2_pipeline.response import ENDPOINT, GenerationError, parse_response
from rq2_pipeline.hashing import sha256_text
from rq2_pipeline.prompts import build_generation_request
from rq2_pipeline.storage import write_json


def generate(query, context, config, folder, *, opener=urllib.request.urlopen):
    request_data = build_generation_request(query["sql"], context,
                                           expected_template_sha256=config["template_sha256"])
    body = {"model": config["model"], "messages": request_data.messages,
            "temperature": config["temperature"], "reasoning_effort": config["reasoning_effort"],
            "max_completion_tokens": config["max_completion_tokens"],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "sql_rewrite", "strict": True, "schema": {"type": "object",
                "properties": {"sql": {"type": "string"}, "explanation": {"type": "string"}},
                "required": ["sql", "explanation"], "additionalProperties": False}}}}
    write_json(folder / "request.json", {"provider": "groq", "endpoint": ENDPOINT,
                                        "generation_identity": asdict(request_data), "body": body})
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise GenerationError("GROQ_API_KEY is missing")
    request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": "qcp-rq2-v2/1.0"})
    try:
        with opener(request, timeout=120) as result:
            raw = result.read().decode("utf-8").replace(key, "[REDACTED]")
            headers = {k: v for k, v in result.headers.items()
                       if k.lower().startswith("x-ratelimit-") or k.lower() in {"x-request-id", "retry-after"}}
    except urllib.error.HTTPError as exc:
        write_json(folder / "api-error.json", {"http_status": exc.code,
                   "body": exc.read().decode(errors="replace").replace(key, "[REDACTED]")})
        raise GenerationError(f"Groq HTTP {exc.code}; no automatic retry") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise GenerationError("Groq network failure; no automatic retry") from None
    write_json(folder / "response-raw.json", {"body": raw, "headers": headers})
    try:
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise ValueError("Response is not an object")
        write_json(folder / "response.json", response)
        candidate = parse_response(response, config["model"])
    except (ValueError, TypeError, KeyError) as exc:
        raise GenerationError("Malformed Groq response; no automatic retry") from exc
    record = {**candidate, "status": "GENERATED", "source": "LLM_REWRITE", "provider": "groq",
              "requested_model": config["model"], "served_model": response["model"],
              "system_fingerprint": response.get("system_fingerprint"), "response_id": response.get("id"),
              "finish_reason": response["choices"][0]["finish_reason"], "usage": response.get("usage"),
              "original_sql_sha256": sha256_text(query["sql"]), "raw_sql_sha256": sha256_text(candidate["sql"]),
              "prompt_sha256": request_data.prompt_sha256, "template_sha256": request_data.template_sha256}
    write_json(folder / "candidate.json", record)
    return record
