"""Deterministic parameter states shared by validation and execution adapters.

Conditions compare JSON scalar values strictly (true is not the number 1).
Inactive values stay in the editable draft, but are excluded from execution.
"""
from __future__ import annotations


def matches(condition, values):
    def same_value(value, candidate):
        # JSON has one numeric category (the browser treats 1 and 1.0 alike),
        # but booleans must never compare equal to numeric 0 or 1.
        same_type = type(value) is type(candidate) or (
            type(value) in {int, float} and type(candidate) in {int, float})
        return same_type and value == candidate
    return all(key in values and values[key] is not None and
               any(same_value(values[key], candidate) for candidate in allowed)
               for key, allowed in condition.items())


def active(spec, values):
    return matches(spec.visible_when, values) and matches(spec.enabled_when, values)


def required(spec, values):
    return active(spec, values) and (spec.required or bool(spec.required_when) and matches(spec.required_when, values))


def effective_parameters(method, selected):
    # Presence is meaningful: an explicit null requests omission. Never use `or`
    # or `??` semantics to replace valid 0/false or omitted values with defaults.
    values = {spec.id: selected.get(spec.id, spec.default) for spec in method.parameters}
    # Legacy snapshots and recipe adapters can carry resource fields outside
    # the public form. Keep them for resource guards and runner validation;
    # silently dropping an unknown field would bypass those checks.
    effective = {key: value for key, value in selected.items() if key not in values}
    effective.update({spec.id: values[spec.id] for spec in method.parameters
                      if active(spec, values) and values[spec.id] is not None})
    return effective


def conflicts(spec, values):
    value = values.get(spec.id)
    if not active(spec, values) or value is None or value is False or value == '':
        return []
    return [key for key in spec.conflicts_with
            if values.get(key) is not None and values[key] is not False and values[key] != '']


def input_required(slot, values):
    """One shared file satisfies all consumers, so any consumer can require it."""
    return bool(slot.required or (slot.required_when and matches(slot.required_when, values))
                or any(matches(condition, values) for condition in getattr(slot, 'required_when_any', [])))
