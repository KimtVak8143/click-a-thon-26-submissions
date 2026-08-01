import json
from dataclasses import dataclass
from typing import Any


class SchemaInliningError(ValueError):
    """Raised when a provider schema cannot be safely inlined."""


@dataclass(frozen=True)
class SchemaInliningDiagnostics:
    schema_byte_count: int
    references_before_inlining: int
    references_after_inlining: int
    nested_required_array_count: int

    def as_dict(self) -> dict[str, int]:
        return {
            "provider_schema_bytes": self.schema_byte_count,
            "schema_references_before": self.references_before_inlining,
            "schema_references_after": self.references_after_inlining,
            "nested_required_array_count": self.nested_required_array_count,
        }


def inline_local_json_schema_references(
    schema: dict[str, Any],
    *,
    strict_object_compliance: bool = False,
) -> tuple[dict[str, Any], SchemaInliningDiagnostics]:
    """Inline strict local ``#/$defs/...`` references for provider transport.

    When ``strict_object_compliance`` is True every object node gets
    ``required`` (all property names) and ``additionalProperties: false``
    so the schema satisfies OpenAI structured-output strict mode.
    """

    definitions = schema.get("$defs", {})
    if not isinstance(definitions, dict):
        raise SchemaInliningError("JSON schema $defs must be an object")
    _validate_reference_shapes(schema, definitions)
    references_before = count_schema_references(schema)
    inlined = _inline_value(schema, definitions=definitions, stack=())
    if not isinstance(inlined, dict):
        raise SchemaInliningError("JSON schema root must remain an object")
    if strict_object_compliance:
        inlined = _apply_strict_object_compliance(inlined)
        if not isinstance(inlined, dict):
            raise SchemaInliningError("JSON schema root must remain an object after normalization")
    references_after = count_schema_references(inlined)
    if references_after:
        raise SchemaInliningError("provider JSON schema contains unresolved references")
    diagnostics = SchemaInliningDiagnostics(
        schema_byte_count=len(_stable_json(inlined).encode("utf-8")),
        references_before_inlining=references_before,
        references_after_inlining=references_after,
        nested_required_array_count=count_nested_required_arrays(inlined),
    )
    return inlined, diagnostics


def count_schema_references(value: Any) -> int:
    if isinstance(value, list):
        return sum(count_schema_references(item) for item in value)
    if not isinstance(value, dict):
        return 0
    return int("$ref" in value) + sum(count_schema_references(item) for item in value.values())


def count_nested_required_arrays(value: Any, *, depth: int = 0) -> int:
    if isinstance(value, list):
        return sum(count_nested_required_arrays(item, depth=depth + 1) for item in value)
    if not isinstance(value, dict):
        return 0
    current = int(depth > 0 and isinstance(value.get("required"), list))
    return current + sum(
        count_nested_required_arrays(item, depth=depth + 1) for item in value.values()
    )


def _inline_value(
    value: Any,
    *,
    definitions: dict[str, Any],
    stack: tuple[str, ...],
) -> Any:
    if isinstance(value, list):
        return [_inline_value(item, definitions=definitions, stack=stack) for item in value]
    if not isinstance(value, dict):
        return value

    reference = value.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise SchemaInliningError("only local #/$defs/... references are supported")
        definition_name = reference.removeprefix("#/$defs/")
        if not definition_name or "/" in definition_name or definition_name not in definitions:
            raise SchemaInliningError("JSON schema contains an unknown local reference")
        if definition_name in stack:
            cycle = " -> ".join((*stack, definition_name))
            raise SchemaInliningError(f"cyclic JSON schema reference detected: {cycle}")
        definition = definitions[definition_name]
        if not isinstance(definition, dict):
            raise SchemaInliningError("referenced JSON schema definition must be an object")
        resolved = _inline_value(
            definition,
            definitions=definitions,
            stack=(*stack, definition_name),
        )
        siblings = {
            key: _inline_value(child, definitions=definitions, stack=stack)
            for key, child in value.items()
            if key != "$ref" and key != "$defs"
        }
        return _merge_reference_siblings(resolved, siblings)

    return {
        key: _inline_value(child, definitions=definitions, stack=stack)
        for key, child in value.items()
        if key != "$defs"
    }


def _validate_reference_shapes(value: Any, definitions: dict[str, Any]) -> None:
    if isinstance(value, list):
        for item in value:
            _validate_reference_shapes(item, definitions)
        return
    if not isinstance(value, dict):
        return
    if "$ref" in value:
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise SchemaInliningError("only local #/$defs/... references are supported")
        definition_name = reference.removeprefix("#/$defs/")
        if not definition_name or "/" in definition_name or definition_name not in definitions:
            raise SchemaInliningError("JSON schema contains an unknown local reference")
    for child in value.values():
        _validate_reference_shapes(child, definitions)


def _merge_reference_siblings(resolved: dict[str, Any], siblings: dict[str, Any]) -> dict[str, Any]:
    merged = dict(resolved)
    for key, value in siblings.items():
        if key in merged and merged[key] != value:
            raise SchemaInliningError(
                f"JSON schema reference sibling conflicts with definition key {key!r}"
            )
        merged[key] = value
    return merged


def _apply_strict_object_compliance(value: Any) -> Any:
    """Recursively add required+additionalProperties:false to every object node.

    OpenAI strict mode requires ALL properties listed in required (even those
    with defaults) and additionalProperties set to false on every object.
    """
    if isinstance(value, list):
        return [_apply_strict_object_compliance(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _apply_strict_object_compliance(child) for key, child in value.items()}
    if "properties" in result and isinstance(result["properties"], dict):
        result["required"] = list(result["properties"].keys())
        result["additionalProperties"] = False
    return result


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
