"""Configuration lookup is scoped, read only, and performs no runtime/package probes."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agent_execution_config import read_execution_configuration
from app.agent_facts import responsibility


def owner(executable='/configured/mrtrix/bin/tckgen'):
    runner = SimpleNamespace(executable=Path(executable), runtime_status=Mock(side_effect=AssertionError('probe forbidden')))
    method = SimpleNamespace(runner_id='mrtrix.tckgen', execution_mode='runner')
    catalog = SimpleNamespace(registry=SimpleNamespace(method_for=Mock(return_value=method)),
                              runners=SimpleNamespace(get=Mock(return_value=runner)))
    return SimpleNamespace(skill_catalog=catalog), runner


def test_reports_resolved_path_without_claiming_availability_or_running_probe():
    value, runner = owner()
    result = read_execution_configuration(value, {'method_ids': ['mrtrix.tckgen', 'mrtrix.tckgen']})
    assert result['total_items'] == 1 and result['items'][0]['path_configured']
    assert result['items'][0]['executable_path'] == str(runner.executable)
    assert 'available' not in result['items'][0] and not result['items'][0]['python_environment_needed']
    runner.runtime_status.assert_not_called()
    assert responsibility('read_execution_configuration') == 'runtime'


def test_python_is_only_marked_for_on_demand_environment_query():
    value, runner = owner('/configured/env/bin/python3')
    result = read_execution_configuration(value, {'method_ids': ['reviewed.python']})
    assert result['items'][0]['python_environment_needed']
    runner.runtime_status.assert_not_called()


def test_missing_registration_and_empty_path_are_explicit():
    value, _ = owner('.')
    assert not read_execution_configuration(value, {'method_ids': ['m']})['items'][0]['path_configured']
    value.skill_catalog.runners.get.return_value = None
    item = read_execution_configuration(value, {'method_ids': ['m']})['items'][0]
    assert not item['runner_registered'] and not item['path_configured']


@pytest.mark.parametrize('args', [{'method_ids': []}, {'method_ids': ['m'], 'command': 'probe'},
                                {'method_ids': ['m'] * 21}])
def test_requires_scoped_method_ids_and_rejects_arbitrary_parameters(args):
    value, _ = owner()
    with pytest.raises(ValueError):
        read_execution_configuration(value, args)
