from __future__ import annotations
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import tempfile
import sys

from app.execution.runners.reviewed import ReviewedRunner, specifications, probe, ROOT
from app.workflow_models import InputBinding
from app.workflow_models import WorkflowStep
from app.run_models import RunArtifact
from app.execution.run_service import RunService


class ReviewedExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.data=self.root/'inputs';self.data.mkdir()
        self.spec=copy.deepcopy(specifications()['reviewed.nilearn-tool.preprocess'])
        self.runner=ReviewedRunner(self.spec)
        self.context=SimpleNamespace(step=SimpleNamespace(selected_parameters={},input_bindings={}),output_directory=self.root/'run'/'step')
        self.env=patch.dict(os.environ,{'LOCALFLOW_RUN_INPUT_ROOTS':str(self.data)});self.env.start();self.addCleanup(self.env.stop)

    def bind(self,key,path):
        self.context.step.input_bindings[key]=InputBinding(slot_id=key,source='existing_path',absolute_path=str(path),artifact_type='analysis_input')

    def test_strict_parameters_reject_unknown_nonfinite_and_wrong_type(self):
        for value in ({'tr':True},{'tr':float('nan')},{'tr':-1},{'shell':'touch /tmp/no'},{'n_dummies':1.5}):
            with self.subTest(value=value):
                self.context.step.selected_parameters=value
                with self.assertRaises(ValueError):self.runner.parameters(self.context)

    def test_input_boundary_checks_actual_resolved_path(self):
        inside=self.data/'image.nii.gz';inside.write_text('fixture')
        outside=self.root/'outside.nii.gz';outside.write_text('outside')
        self.bind('bold',inside);self.assertEqual(self.runner.checked_inputs(self.context)['bold'],inside)
        self.bind('bold',outside)
        with self.assertRaises(ValueError):self.runner.checked_inputs(self.context)

    def test_path_list_rejects_nested_escape(self):
        spec=copy.deepcopy(specifications()['reviewed.nilearn-tool.ica'])
        self.runner=ReviewedRunner(spec)
        outside=self.root/'outside';outside.write_text('outside')
        listing=self.data/'images.txt';listing.write_text('../outside\n')
        self.bind('input_list',listing)
        with self.assertRaises(ValueError):self.runner.checked_inputs(self.context)

    def test_required_file_and_directory_contracts(self):
        self.bind('bold',self.data)
        with self.assertRaises(ValueError):self.runner.checked_inputs(self.context)
        self.context.step.input_bindings={}
        with self.assertRaises(ValueError):self.runner.checked_inputs(self.context)

    def test_catalog_uses_packaged_files_and_bound_output_paths(self):
        catalog=list(specifications().values())
        self.assertEqual(len(catalog),len(specifications()))
        for spec in catalog:
            with self.subTest(runner=spec['runner_id']):
                self.assertTrue((ROOT/spec['script']).is_file())
                self.assertTrue((ROOT/spec['script']).resolve().is_relative_to(ROOT))
                for _,name in spec['outputs']:
                    self.assertFalse(Path(name).is_absolute());self.assertNotIn('..',Path(name).parts)
                for name in spec['required_files']:self.assertTrue((ROOT/name).is_file())

    def test_model_operation_rejects_unreviewed_choice(self):
        runner=ReviewedRunner(specifications()['reviewed.temporal-models.train'])
        self.context.step.selected_parameters={'operation':'unknown'}
        with self.assertRaises(ValueError):runner.parameters(self.context)

    def test_withdrawn_interfaces_cannot_execute_or_hide_original_plans(self):
        from app.capability_registry import CapabilityRegistry
        from app.execution.runner_registry import RunnerRegistry
        from app.skill_loader import SkillLoader
        retired=set(json.loads((ROOT/'bakend/resources/withdrawn_execution.json').read_text(encoding='utf-8'))['runner_ids'])
        runners=RunnerRegistry();registry=CapabilityRegistry(ROOT/'skills')
        self.assertTrue(retired.isdisjoint(runners.ids()))
        self.assertFalse(any(m.runner_id in retired for m in registry.all_methods()))
        for skill in ['connectome-discovery']:
            methods=registry.selectable_methods_for('skill.'+skill)
            self.assertTrue(methods)
            self.assertTrue(all(m.execution_mode=='planning' for m in methods))
        skills={skill.name:skill for skill in SkillLoader(ROOT/'skills').load_all()}
        self.assertEqual(skills['bnt'].structure_mode,'runner')
        for skill in ['bnt','brainnetcnn','combraintf']:
            method=next(m for m in registry.selectable_methods_for('skill.'+skill) if m.execution_mode=='runner')
            spec=specifications()[method.runner_id]
            self.assertIn('original-trainer',method.runner_id)
            self.assertTrue(spec['original_entrypoint'].endswith('/train.py') or spec['original_entrypoint'].endswith('/train_reference.py'))

    def test_runtime_checks_actual_api_and_rejects_changed_source(self):
        self.assertTrue(probe(sys.executable, ('math',), (('math','sqrt'),))[0])
        self.assertFalse(probe(sys.executable, ('math',), (('math','not_an_existing_algorithm'),))[0])
        spec=copy.deepcopy(self.spec)
        spec['implementation_files'][0]['sha256']='0'*64
        with patch('app.execution.runners.reviewed.probe',return_value=(True,'available')):
            status=ReviewedRunner(spec).runtime_status()
        self.assertFalse(status['available'])
        self.assertIn('实现源码已变化',status['reason'])

    def test_sources_are_recorded_for_every_enabled_adapter(self):
        sources=json.loads((ROOT/'bakend/resources/existing_implementation_sources.json').read_text(encoding='utf-8'))['files']
        for spec in specifications().values():
            with self.subTest(runner=spec['runner_id']):
                origin=spec['implementation_source']
                self.assertTrue(origin['entrypoints'])
                if origin['kind']=='existing_project_script':
                    for entrypoint in origin['entrypoints']:
                        self.assertIn(entrypoint,sources,entrypoint)
                        self.assertIn(entrypoint,{item['path'] for item in spec['implementation_files']})
                elif origin['kind']=='library_api':
                    self.assertTrue(spec['required_apis'])
                else:
                    self.assertEqual(origin['kind'],'software_command')
                    self.assertTrue(spec['executables'])
                self.assertTrue(spec['implementation_files'])

    def test_upstream_uses_exact_output_slot_and_rejects_missing_slot(self):
        binding=InputBinding(slot_id='bold',source='upstream_output',artifact_type='bold_image',upstream_step_id='first',output_slot_id='cleaned')
        step=WorkflowStep(id='second',order=2,title='GLM',description='test',capability='skill.nilearn-tool',status='confirmed',input_bindings={'bold':binding})
        def artifact(name,slot):
            return RunArtifact(artifact_id=name,step_id='first',artifact_type='bold_image',label=name,absolute_path=str(self.root/name),relative_path=name,metadata={'output_slot_id':slot})
        producer=SimpleNamespace(step_id='first',title='producer',status='succeeded',artifacts=[artifact('wrong.nii','other'),artifact('right.nii','cleaned')])
        run=SimpleNamespace(steps=[producer])
        resolved=RunService._resolve_runtime_bindings(step,run)
        self.assertEqual(Path(resolved.input_bindings['bold'].absolute_path).name,'right.nii')
        producer.artifacts.pop()
        with self.assertRaises(RuntimeError):RunService._resolve_runtime_bindings(step,run)

    def test_output_slot_annotations_preserve_explicit_mapping(self):
        from app.workflow_models import MethodOption,OutputSlotSpec
        method=MethodOption(id='test',skill_id='test',label='test',description='test',runner_id='test',
            output_slots=[OutputSlotSpec(id='first',type='generated_file',label='first'),OutputSlotSpec(id='second',type='generated_file',label='second')])
        values=[RunArtifact(artifact_id=str(i),step_id='step',artifact_type='generated_file',label=str(i),absolute_path=str(self.root/str(i)),relative_path=str(i),metadata=meta)
                for i,meta in enumerate([{'output_slot_id':'second'},{'output_slot_id':'first'}])]
        RunService._annotate_output_slots(method,values)
        self.assertEqual([a.metadata['output_slot_id'] for a in values],['second','first'])


if __name__=='__main__':unittest.main()
