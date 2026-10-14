"""Read-only authoring projection of immutable execution graphs.

Resolve public defaults into method parameters; retain shared files, ordered
multi-source links and conditional stages. Reading a source never saves a draft.
"""

import copy


def pipeline_source(pipeline, registry):
    if pipeline.get("editor"):
        return copy.deepcopy(pipeline["editor"])
    public = {p["id"]: p for p in pipeline["parameters"]}
    defaults = {key: p["default"] for key, p in public.items() if p.get("default") is not None}
    steps, shared, controls = [], {}, set()
    for stage in pipeline["steps"]:
        method = registry.method(stage.get("method_ref", stage.get("method_id")))
        values = {p.id: p.default for p in method.parameters if p.default is not None}
        if stage.get("inherit_parameters", True):
            values.update({key: value for key, value in defaults.items() if key in {p.id for p in method.parameters}})
        for target, source in stage.get("parameter_bindings", {}).items():
            if source in defaults:
                values[target] = defaults[source]
        values.update(stage.get("fixed_parameters", {}))
        inputs = {}
        for slot, binding in stage.get("input_bindings", {}).items():
            if binding["source"] == "step_output":
                inputs[slot] = {key: copy.deepcopy(binding[key]) for key in
                                ("step_id", "output_slot_id", "additional_sources") if key in binding}
            elif binding["source"] == "workflow_input":
                shared.setdefault(binding["slot_id"], []).append(dict(step_id=stage["id"], input_slot_id=slot))
            else:
                raise ValueError("Unsupported pipeline input binding")
        condition = stage.get("when", {})
        controls.update(condition)
        steps.append(dict(id=stage["id"], method_id=method.id, parameters=values,
                          inputs=inputs, when=copy.deepcopy(condition)))
    return dict(name=pipeline["label"], description=pipeline.get("description", ""), steps=steps,
                shared_inputs=[group for group in shared.values() if len(group) > 1],
                controls=[copy.deepcopy(public[key]) for key in sorted(controls)])
