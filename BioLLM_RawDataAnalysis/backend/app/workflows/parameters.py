from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


class ParameterValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _matches_condition(condition: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
    if isinstance(condition.get('any_of'), list):
        return any(_matches_condition(item, values) for item in condition['any_of'] if isinstance(item, Mapping))
    parameter = condition.get("parameter")
    if not isinstance(parameter, str) or not parameter:
        return True
    current = values.get(parameter)
    if "equals" in condition:
        return current == condition["equals"]
    if "not_equals" in condition:
        return current != condition["not_equals"]
    if "in" in condition and isinstance(condition["in"], list):
        return current in condition["in"]
    return True


def parameter_is_active(schema: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
    active_when = schema.get("active_when")
    if active_when is None:
        return True
    if isinstance(active_when, Mapping):
        return _matches_condition(active_when, values)
    if isinstance(active_when, list):
        return all(
            _matches_condition(condition, values)
            for condition in active_when
            if isinstance(condition, Mapping)
        )
    return True


def _valid_type(value: Any, expected_type: object) -> bool:
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "object":
        return isinstance(value, dict)
    return True


def validate_parameter_values(
    schema: Mapping[str, Mapping[str, Any]],
    supplied: Mapping[str, Any],
    *,
    owner: str,
) -> dict[str, object]:
    unknown = sorted(set(supplied) - set(schema))
    if unknown:
        raise ParameterValidationError(
            "unknown_parameter",
            f"{owner} contains unregistered parameters: {', '.join(unknown)}",
        )

    values: dict[str, object] = {}
    for name, definition in schema.items():
        if name in supplied:
            values[name] = supplied[name]
        elif "default" in definition:
            values[name] = definition["default"]

    normalized: dict[str, object] = {}
    for name, definition in schema.items():
        active = parameter_is_active(definition, values)
        was_supplied = name in supplied
        if not active:
            if was_supplied:
                raise ParameterValidationError(
                    "inactive_parameter",
                    f"{owner} parameter {name} is inactive for the selected method/options",
                )
            continue
        if name not in values:
            if definition.get("required") is True:
                raise ParameterValidationError(
                    "missing_required_parameter",
                    f"{owner} parameter {name} is required",
                )
            continue
        value = values[name]
        if value is None and definition.get("nullable"):
            # Null database references mean use the server-managed default.
            continue
        expected_type = definition.get("type")
        if isinstance(value, float) and not math.isfinite(value):
            raise ParameterValidationError('nonfinite_parameter', f'{owner} parameter {name} must be finite')
        if not _valid_type(value, expected_type):
            raise ParameterValidationError(
                "invalid_parameter_type",
                f"{owner} parameter {name} must be {expected_type}",
            )
        enum = definition.get("enum")
        if isinstance(enum, list) and value not in enum:
            raise ParameterValidationError(
                "invalid_parameter_value",
                f"{owner} parameter {name} is not an allowed value",
            )
        if "minimum" in definition and value < definition["minimum"]:
            raise ParameterValidationError(
                "parameter_below_minimum",
                f"{owner} parameter {name} is below its minimum",
            )
        if "maximum" in definition and value > definition["maximum"]:
            raise ParameterValidationError(
                "parameter_above_maximum",
                f"{owner} parameter {name} exceeds its maximum",
            )
        normalized[name] = value
    return {name: normalized[name] for name in sorted(normalized)}
