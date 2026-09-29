import json
import logging
import re
import typing
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger("ai.repair")

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json_block(text: str) -> str:
    """Extract the most likely JSON block from an LLM response."""
    if not text:
        raise ValueError("Empty LLM response.")
    text = text.strip()
    match = _FENCE_RE.search(text)
    if match:
        block = match.group(1).strip()
        try:
            json.loads(block)
            return block
        except json.JSONDecodeError:
            pass
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    # Find the first { ... } spanning region
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    # Find the first [ ... ] spanning region
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    raise ValueError("No valid JSON found in LLM response.")


def _unwrap_singleton(value):
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _string_to_model(value: str, model: Type[BaseModel]) -> dict:
    """Map a bare string onto the first string-ish field of a model (e.g. SourceRef.quote)."""
    for candidate in ("quote", "text", "name", "title", "description", "factor"):
        if candidate in model.model_fields:
            return {candidate: value}
    return {}


def _normalize_value(value, annotation):
    if annotation is None:
        return value
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if value is None:
            return None
        if len(non_none) == 1:
            return _normalize_value(value, non_none[0])
        return value

    if origin in (list, typing.List):
        inner = args[0] if args else None
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        elif isinstance(value, dict):
            value = [value]
        if isinstance(value, (list, tuple)):
            return [_normalize_value(v, inner) for v in value]
        return value

    if origin is dict:
        return value if isinstance(value, dict) else {}

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        value = _unwrap_singleton(value)
        if isinstance(value, dict):
            return _normalize_model(value, annotation)
        if isinstance(value, str):
            return _string_to_model(value, annotation)
        return {}

    if annotation is str:
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v) for v in value)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return value if isinstance(value, str) else str(value)

    if annotation is int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    if annotation is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    if annotation is bool:
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "1")
        return bool(value)

    return value


def _normalize_model(data, model: Type[BaseModel]):
    if isinstance(data, list):
        data = _unwrap_singleton(data)
    if not isinstance(data, dict):
        return data

    # Case: expected a wrapper with a single list field, but got the item itself.
    list_fields = [
        name
        for name, f in model.model_fields.items()
        if typing.get_origin(f.annotation) in (list, typing.List)
    ]
    if len(list_fields) == 1 and list_fields[0] not in data:
        inner = typing.get_args(model.model_fields[list_fields[0]].annotation)
        inner = inner[0] if inner else None
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            inner_keys = set(inner.model_fields.keys())
            if inner_keys & set(data.keys()):
                return {list_fields[0]: [data]}

    out = {}
    for name, field in model.model_fields.items():
        if name in data:
            out[name] = _normalize_value(data[name], field.annotation)
    return out


def parse_and_validate(text: str, model: Type[T]) -> T | None:
    """Parse an LLM response into `model`, coercing common shape mistakes. Returns None if invalid."""
    try:
        block = extract_json_block(text)
        data = json.loads(block)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("JSON extraction failed: %s", exc)
        return None
    # Normalize first: schema fields all have defaults, so an un-normalized payload can
    # silently validate into an empty result (e.g. a bare risk object -> {"risks": []}).
    try:
        normalized = _normalize_model(data, model)
        return model.model_validate(normalized)
    except ValidationError as exc:
        logger.warning("Pydantic validation failed (normalized): %s", exc)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        logger.warning("Pydantic validation failed (raw): %s", exc)
        return None


def first_good(texts: list[str], model: Type[T]) -> tuple[T | None, int]:
    """Return (model_instance, index) of first successful validation, else (None, last_index)."""
    last_index = -1
    for i, t in enumerate(texts):
        last_index = i
        parsed = parse_and_validate(t, model)
        if parsed is not None:
            return parsed, i
    return None, last_index