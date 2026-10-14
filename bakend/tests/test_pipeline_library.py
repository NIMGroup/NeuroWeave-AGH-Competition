from pathlib import Path

import pytest
from app.capability_registry import CapabilityRegistry
from app.local_store import LocalStore
from app.pipeline_library import PipelineLibrary
from app.pipeline_models import ComposeRequest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def library(tmp_path):
    registry = CapabilityRegistry(ROOT / "skills")
    library = PipelineLibrary(LocalStore(tmp_path / "test.db"), registry)
    registry.library_provider = library.bindings
    return library


def draft(library):
    method = library.registry.definitions.method("mrtrix.command.mrthreshold")
    source = next(
        o
        for o in method.output_slots
        if o.type == "scalar_image" and not o.generated_when
    )
    target = next(i for i in method.input_slots if i.type == "scalar_image")
    return {
        "name": "Threshold chain",
        "steps": [
            {"id": "first", "method_id": method.id},
            {
                "id": "second",
                "method_id": method.id,
                "inputs": {
                    target.id: {"step_id": "first", "output_slot_id": source.id}
                },
            },
        ],
    }


def test_composition_infers_ports_and_preserves_conditions(library):
    value = library.compile(ComposeRequest.model_validate(draft(library)))
    assert len(value["steps"]) == 2
    assert any(s["id"].startswith("first__") for s in value["input_slots"])
    assert all(
        not s["id"].startswith("first__")
        for s in value["output_slots"]
        if s["type"] == "scalar_image"
    )
    method = library.registry.definitions.method("mrtrix.command.mrthreshold")
    for original in method.parameters:
        composed = next(
            p for p in value["parameters"] if p["id"] == "first__" + original.id
        )
        assert composed["conflicts_with"] == [
            "first__" + p for p in original.conflicts_with
        ]
        assert composed["required_when"] == {
            "first__" + k: v for k, v in original.required_when.items()
        }


def test_backward_links_and_unknown_methods_parameters_are_rejected(library):
    value = draft(library)
    value["steps"].reverse()
    with pytest.raises(ValueError, match="向后引用"):
        library.compile(ComposeRequest.model_validate(value))
    value = draft(library)
    value["steps"][0]["parameters"] = {"not_a_parameter": True}
    with pytest.raises(ValueError, match="未定义"):
        library.compile(ComposeRequest.model_validate(value))
    value = draft(library)
    value["steps"][0]["method_id"] = "shell.rm"
    with pytest.raises(KeyError):
        library.compile(ComposeRequest.model_validate(value))


def test_immutable_versions_and_catalog_loading(library):
    request = ComposeRequest.model_validate(draft(library))
    first = library.save(request)
    request.expected_version = 1
    request.name = "Updated"
    second = library.save(request, first["id"])
    assert second["version"] == "2"
    assert library.get(first["id"], 1)["label"] == "Threshold chain"
    assert library.get(first["id"])["label"] == "Updated"
    with pytest.raises(ValueError, match="版本"):
        library.save(request, first["id"])
    assert not library.registry.methods_for("library." + first["id"] + "@1")
    assert (
        library.registry.methods_for("library." + first["id"] + "@2")[0].label
        == "Updated"
    )
    assert (
        library.registry.methods_for("library." + first["id"] + "@2")[0].label
        == "Updated"
    )


def test_canonical_method_listing_is_unique_and_paginated(library):
    first = library.catalog("method", limit=7)
    second = library.catalog("method", offset=7, limit=7)
    assert first["has_more"]
    assert not {m["id"] for m in first["items"]} & {m["id"] for m in second["items"]}
    assert library.catalog("method", "mrtrix.tckgen")["total"] == 1


def test_contract_parameter_types_cannot_be_bypassed(library):
    value = draft(library)
    value["steps"][0]["parameters"] = {"nthreads": "not a number"}
    with pytest.raises(ValueError):
        library.compile(ComposeRequest.model_validate(value))
