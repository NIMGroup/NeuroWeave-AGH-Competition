"""Candidate discovery stays short and distinguishes production from mentions."""
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from app.capability_registry import CapabilityRegistry
from app.skill_catalog import SkillCatalog
from app.skill_loader import SkillLoader

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def catalog():
    return SkillCatalog(SkillLoader(ROOT/'skills'), CapabilityRegistry(ROOT/'skills'),
                        SimpleNamespace(get=lambda _: None))


@pytest.mark.parametrize('query, expected', [
    ('mrconvert convert nifti mif format', {'mrtrix.command.mrconvert', 'reviewed.dwi-skill.mrconvert'}),
    ('dwi2mask mask brain', {'mrtrix.command.dwi2mask'}),
    ('tckgen streamlines whole-brain tractogram', {'mrtrix.tckgen'}),
])
def test_specific_registered_command_is_not_widened_by_generic_words(catalog, query, expected):
    result = catalog.list_methods(q=query)
    assert {v['method_id'] for v in result['items']} == expected
    assert result['match_mode'] == 'exact_identity_terms'
    assert len(json.dumps(result, ensure_ascii=False)) < 3000
    assert all(len(v['description']) <= 220 and 'role' not in v for v in result['items'])


def test_target_artifact_filters_producers_without_probing_runtime(catalog):
    result = catalog.list_methods(output_type='tractogram')
    assert 'mrtrix.tckgen' in {v['method_id'] for v in result['items']}
    assert all('tractogram' in v['output_types'] for v in result['items'])
    assert not {'mrtrix.command.afdconnectivity', 'freesurfer.recon_all'} & {v['method_id'] for v in result['items']}


def test_multi_command_lookup_retains_all_independent_identifiers(catalog):
    result = catalog.list_methods(q='mrconvert dwidenoise mrdegibbs dwi2mask', limit=12)
    assert {'mrtrix.command.mrconvert', 'mrtrix.command.dwidenoise',
            'mrtrix.command.mrdegibbs', 'mrtrix.command.dwi2mask'} <= {v['method_id'] for v in result['items']}
