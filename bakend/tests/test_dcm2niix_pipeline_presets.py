"""Keep actual preset constraints; duplicate labels are not separate operations."""
from pathlib import Path

from app.capability_registry import CapabilityRegistry
from app.local_store import LocalStore
from app.pipeline_library import PipelineLibrary
from app.pipeline_models import ComposeRequest


def test_dcm2niix_presets_are_distinct_and_all_skill_references_resolve(tmp_path):
    registry = CapabilityRegistry(Path(__file__).resolve().parents[2] / 'skills')
    registry._load()
    definitions = registry.definitions
    pipelines = [p for p in definitions.pipelines.values()
                 if any(s['method_ref'] == 'dcm2niix.convert' for s in p['steps'])]
    assert len(pipelines) == 3
    constraints = {tuple(sorted(p['steps'][0]['fixed_parameters'].items())): p
                   for p in pipelines}
    assert set(constraints) == {
        (('compression', 'y'),),
        (('bids_sidecar', True), ('compression', 'y')),
        (('anonymize', True), ('compression', 'y')),
    }
    refs = definitions.skills['dcm2nii']['pipeline_refs']
    assert {ref['pipeline_ref'] for ref in refs} == {p['id'] for p in pipelines}
    assert len(refs) == 3
    library = PipelineLibrary(LocalStore(tmp_path / 'presets.db'), registry)
    for ref in refs:
        p = definitions.pipelines[ref['pipeline_ref']]
        assert ref['label'] == p['label'] and ref['role'] == p['description']
        parameters = {parameter['id'] for parameter in p['parameters']}
        fixed = p['steps'][0]['fixed_parameters']
        assert not parameters.intersection(fixed)
        assert parameters == {'bids_sidecar', 'anonymize', 'filename_template'} - set(fixed)
        # Immutable read/copy still resolves the preset into the atomic contract.
        source = library.source(p['id'])['editor']
        for key, value in fixed.items():
            assert source['steps'][0]['parameters'][key] == value
        copied = library.compile(ComposeRequest.model_validate(source))
        assert copied['steps'][0]['method_ref'] == 'dcm2niix.convert'
    # Keep the identifier used by the existing development task.
    assert 'reviewed-dcm2niix-convert-y-true-989e0368fb' in definitions.pipelines
