#!/usr/bin/env python3
"""Explicit, isolated two-run Slurm check using the existing Nilearn denoiser.

Requires configured shared worker/Python paths. Never reads .env or production DB.
Synthetic inputs, SQLite and evidence remain under a new --root for inspection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--partial', action='store_true', help='Withhold one synthetic source output; submit only the other sample')
    mode.add_argument('--single', action='store_true', help='One original sample, ordinary inputs, then historical inputs on node03')
    args = parser.parse_args()
    sample_count = 1 if args.single else 2
    root = args.root.resolve()
    if root.parent != Path('/home/data/neuroweave/smoke') or root.exists():
        parser.error('Use a new direct child of /home/data/neuroweave/smoke')
    if os.getenv('LOCALFLOW_SLURM_ENABLED') != 'true':
        parser.error('Set LOCALFLOW_SLURM_ENABLED=true and shared worker/Python paths')
    root.mkdir(mode=0o700)
    raw = root / 'raw'
    raw.mkdir()
    (root / 'runs').mkdir()
    os.environ.update(LOCALFLOW_DATA_ROOTS=str(raw), LOCALFLOW_RUN_INPUT_ROOTS=str(raw),
        LOCALFLOW_SLURM_OUTPUT_ROOT=str(root / 'runs'), LOCALFLOW_SLURM_POLL_SECONDS='0.5')
    # Generate only synthetic images; production input files are never opened.
    subprocess.run([os.getenv('LOCALFLOW_SKILL_PYTHON', '/opt/neuroweave/envs/scientific-py311/bin/python'),
        '-c', '''import sys
from pathlib import Path
import nibabel as nib
import numpy as np
for index, name in enumerate(('subject-A', 'subject-B')[:int(sys.argv[2])]):
    folder = Path(sys.argv[1]) / name
    folder.mkdir()
    rng = np.random.default_rng(200 + index)
    image = rng.normal(100, 3, size=(8, 8, 8, 40)).astype('float32')
    nib.save(nib.Nifti1Image(image, np.eye(4)), folder / 'bold.nii.gz')
''', str(raw), str(sample_count)], check=True)
    sys.path.insert(0, str(PROJECT / 'bakend'))
    from app.capability_registry import CapabilityRegistry
    from app.chat_service import ChatService
    from app.dataset_service import DatasetService
    from app.execution.run_service import RunService
    from app.execution.runner_registry import RunnerRegistry
    from app.historical_inputs import HistoricalInputs, HistoricalSelectionRequest
    from app.local_store import LocalStore
    from app.run_models import NodeAllocation, RunExecutionOptions
    from app.workflow_models import InputBinding, MethodOption, Workflow, WorkflowStep
    from app.workflow_service import WorkflowService

    store = LocalStore(root / 'smoke.sqlite3')
    datasets = DatasetService(PROJECT, store)
    dataset = datasets.register(datasets.roots()[0].root_id, '')
    assert dataset.inspection.sample_count == sample_count
    capabilities = CapabilityRegistry(PROJECT / 'skills')
    definition = next(item for item in capabilities.methods_for('skill.nilearn-tool')
                      if item.runner_id == 'reviewed.nilearn-tool.denoise')
    method = MethodOption(id=definition.method_id, **definition.model_dump(exclude={'method_id'}))
    runner_registry = RunnerRegistry()
    runtime = runner_registry.get(method.runner_id).runtime_status()
    assert runtime['available'], runtime['reason']
    workflows = WorkflowService(ChatService(), capabilities, store, datasets,
        executable_runner_ids={method.runner_id}, runner_registry=runner_registry)
    runs = RunService(store, capabilities, runner_registry, root / 'local-unused')
    options = RunExecutionOptions(backend='slurm', sample_mode='per_sample',
        nodes=[NodeAllocation(node=node, cpus=1, memory_gb=2, time_minutes=2)
               for node in (('node03',) if args.single else ('node01', 'node02'))])
    evidence = {'root': str(root), 'runner': method.runner_id, 'runs': [], 'success': False}
    owned_runs = []

    def workflow(name):
        stamp = datetime.now(timezone.utc).isoformat()
        result = Workflow(workflow_id='smoke_history_' + name, template_id='dynamic:skill.nilearn-tool',
            title='Synthetic historical input ' + name, summary='Existing Nilearn implementation',
            original_request='Reuse previous synthetic outputs', status='awaiting_input_binding',
            dataset_id=dataset.dataset_id, dataset=dataset.model_copy(deep=True), locked_at=stamp,
            execution_mode='runner', plan_kind='atomic', requested_capabilities=['skill.nilearn-tool'],
            steps=[WorkflowStep(id='denoise', order=1, title='Denoise', description='Existing implementation',
                capability='skill.nilearn-tool', status='method_selected', method_options=[method],
                selected_method_id=method.id,
                selected_parameters={p.id: p.default for p in method.parameters})],
            created_at=stamp, updated_at=stamp)
        preview = datasets.preview_sample_mapping(dataset.dataset_id, dataset.root_id,
            'subject-A/bold.nii.gz', binding='file', formats=['.nii.gz'])
        assert preview.can_apply
        result.steps[0].input_bindings['bold'] = InputBinding(slot_id='bold', artifact_type='bold_image',
            source='existing_path', label='Synthetic BOLD', dataset_id=dataset.dataset_id,
            absolute_path=str(raw / 'subject-A/bold.nii.gz'), binding_mode=preview.binding_mode,
            path_template=preview.path_template, sample_matches=preview.matches,
            coverage=preview.coverage, status='validated')
        store.save_workflow(result, event_type='synthetic_fixture')
        return result

    def execute(plan):
        plan = workflows.review_parameters(plan.workflow_id, plan.revision)
        plan = workflows.dry_run(plan.workflow_id, plan.revision)
        assert plan.dry_run_report.valid and plan.dry_run_report.execution_enabled, plan.dry_run_report
        plan = workflows.confirm(plan.workflow_id, plan.revision)
        run = runs.create(plan.workflow_id, plan.revision, options)
        owned_runs.append(run.run_id)
        print('Submitted', run.run_id, flush=True)
        deadline = time.monotonic() + 240
        while run.status not in {'succeeded', 'failed', 'cancelled'} and time.monotonic() < deadline:
            time.sleep(0.5)
            run = runs.get(run.run_id)
        evidence['runs'].append({'run_id': run.run_id, 'status': run.status,
            'steps': [{'sample_id': s.sample_id, 'status': s.status, 'scheduler': s.scheduler,
                       'error': s.error_message} for s in run.steps]})
        assert run.status == 'succeeded', (run.status, run.error_message, evidence['runs'][-1])
        assert all(s.scheduler['terminal']['state'] == 'COMPLETED'
                   and s.scheduler['terminal']['exit_code'] == '0:0' for s in run.steps)
        return run

    try:
        first = execute(workflow('source'))
        paths = {s.sample_id: next(a.absolute_path for a in s.artifacts if a.artifact_type == 'bold_image')
                 for s in first.steps}
        if args.partial:
            withheld = Path(paths['subject-B']).resolve()
            assert withheld.is_relative_to(root / 'runs')
            withheld.rename(withheld.with_name(withheld.name + '.unavailable'))
            options.nodes = [NodeAllocation(node='node02', cpus=1, memory_gb=2,
                time_minutes=2, parallel_samples=2)]
        checksums = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                     for path in paths.values() if Path(path).is_file()}
        target = workflow('target')
        target.dataset.inspection.samples.reverse()
        store.save_workflow(target, event_type='synthetic_reorder')
        slot = next(slot for slot in method.input_slots if slot.id == 'bold')
        choices = HistoricalInputs(store).candidates(target, slot)['candidates']
        choice = next(item for item in choices if item['run_id'] == first.run_id)
        request = HistoricalSelectionRequest(expected_revision=target.revision,
            run_id=first.run_id, group_id=choice['group_id'], allow_partial=args.partial)
        preview = workflows.preview_historical_input(target.workflow_id, 'denoise', 'bold', request)
        assert preview['can_apply'] and preview['coverage']['matched'] == (1 if args.partial else sample_count)
        assert all(paths[m['sample_id']] == m['absolute_path'] for m in preview['matches'] if m['status'] == 'matched')
        target = workflows.bind_historical_input(target.workflow_id, 'denoise', 'bold', request)
        second = execute(target)
        if args.single:
            assert all(len(run.steps) == 1 and run.steps[0].sample_id == 'subject-A' for run in (first, second))
            assert all(run.steps[0].node_name == 'node03' for run in (first, second))
            assert second.plan_snapshot['steps'][0]['input_bindings']['bold']['binding_mode'] == 'single'
            evidence['single_sample_on_compute_node'] = True
        if args.partial:
            assert [s.sample_id for s in second.steps] == ['subject-A']
            assert second.steps[0].step_id == 'sample_0002__denoise'
            assert [s.sample_id for s in second.sample_scope.skipped_samples] == ['subject-B']
            evidence['sample_scope'] = second.sample_scope.model_dump(mode='json')
        # The worker request proves each sample read its own source output in place.
        for step in second.steps:
            request_path = Path(step.scheduler['job_directory']) / 'payload.json'
            payload = json.loads(request_path.read_text())
            binding = payload['step']['input_bindings']['bold']
            assert binding['absolute_path'] == paths[step.sample_id]
            assert len(binding['history']['artifacts']) == 1
            assert binding['history']['artifacts'][0]['sample_id'] == step.sample_id
            evidence.setdefault('worker_inputs', []).append({'sample_id': step.sample_id,
                'absolute_path': binding['absolute_path'], 'run_id': binding['history']['run_id']})
        assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
                   for path, digest in checksums.items())
        assert len(list(raw.rglob('*.nii.gz'))) == sample_count
        evidence.update(success=True, sample_mapping=preview['matches'], source_checksums=checksums,
            raw_input_roots_only=True, no_input_copy=True)
        print(f"PASS: two runs, {len(first.steps) + len(second.steps)} real Slurm jobs, exact historical paths and unchanged selected source files.", flush=True)
    finally:
        # Cancel only runs created in this isolated test if an assertion/timeout occurs.
        for run_id in owned_runs:
            run = runs.get(run_id)
            if run.status not in {'succeeded', 'failed', 'cancelled'}:
                runs.cancel(run_id)
                deadline = time.monotonic() + 65
                while runs.get(run_id).status not in {'succeeded', 'failed', 'cancelled'} and time.monotonic() < deadline:
                    time.sleep(0.5)
        runs.shutdown()
        (root / 'evidence.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
