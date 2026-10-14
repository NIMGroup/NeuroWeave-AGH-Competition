"""Validate native Skill identities; retired alias definitions are unsupported."""

from .skill_references import reference_specs

def native_skills(values):
    for identity, value in values.items():
        if "alias_of" in value:
            raise ValueError("Skill aliases are no longer supported: " + identity)
        if identity != value.get("id"):
            raise ValueError("Skill identity mismatch: " + identity)
        for field in ("selection_rules", "applicability", "limitations"):
            if field not in value:
                continue
            items = value[field]
            if (not isinstance(items, list)
                    or any(not isinstance(item, str) or not item.strip() for item in items)
                    or len(items) != len(set(items))):
                raise ValueError("Invalid Skill guidance: " + identity + "/" + field)
        reference_specs(value)
    return values
