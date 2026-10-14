"""Stable display/execution ordering using registered method interfaces only."""
from __future__ import annotations

from .capability_registry import CapabilityRegistry


def order_capabilities(registry: CapabilityRegistry, requested: list[str]) -> list[str]:
    nodes = list(dict.fromkeys(requested))
    generic = {"*", "dataset_folder", "analysis_input", "generated_file",
               "generated_folder", "planned_artifact", "execution_log"}
    inputs, outputs = {}, {}
    for capability in nodes:
        methods = registry.selectable_methods_for(capability)
        inputs[capability] = {
            value for method in methods for value in (
                [slot.type for slot in method.input_slots if "upstream_output" in slot.source_options]
                if method.input_slots else method.input_types
            )
        } - generic
        outputs[capability] = set()
        for method in methods:
            declared = {slot.type for slot in method.output_slots} if method.output_slots else set(method.output_types)
            primary = {slot.type for slot in method.output_slots if slot.primary} - generic
            # A bundled operation's intermediate products must not put it before
            # a standalone producer of the input another method can consume.
            outputs[capability].update(primary or (declared - generic))
    edges = {node: set() for node in nodes}
    for producer in nodes:
        for consumer in nodes:
            # Shared output types describe alternatives/in-place transforms, not
            # proof that either operation must precede the other.
            if producer != consumer and (outputs[producer] - outputs[consumer]) & inputs[consumer]:
                edges[producer].add(consumer)
    # Alternative methods can imply cyclic capability-level edges. Keep their
    # relative order within each strongly connected group, while ordering all
    # unambiguous dependencies between groups.
    reachable = {}
    for node in nodes:
        seen, pending = {node}, list(edges[node])
        while pending:
            other = pending.pop()
            if other not in seen:
                seen.add(other)
                pending.extend(edges[other])
        reachable[node] = seen
    groups, remaining = [], set(nodes)
    for node in nodes:
        if node in remaining:
            group = [other for other in nodes
                     if other in remaining and other in reachable[node] and node in reachable[other]]
            groups.append(group)
            remaining.difference_update(group)
    result = []
    while groups:
        group = next(group for group in groups if not any(
            target in group for other in groups if other is not group
            for source in other for target in edges[source]
        ))
        result.extend(group)
        groups.remove(group)
    return result
