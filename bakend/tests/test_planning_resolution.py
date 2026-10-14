from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from app.capability_registry import CapabilityRegistry
from app.execution.recipe_compiler import RecipeCompiler, RecipeCompilationError
from app.execution.runner_registry import RunnerRegistry
from app.method_registry import MethodRegistry
from app.workflow_models import InputBinding, MethodOption, Workflow, WorkflowStep

ROOT=Path(__file__).resolve().parents[2]


def workflow_for(definition):
    method=MethodOption(id=definition.method_id, **definition.model_dump(exclude={'method_id'}))
    step=WorkflowStep(id='analysis',order=1,title='Synthetic recipe',description='test',status='confirmed',
        capability=definition.capability,method_options=[method],selected_method_id=method.id,
        selected_parameters={p.id:p.default for p in method.parameters if p.default is not None})
    return Workflow(workflow_id='recipe-test',template_id='dynamic',title='test',summary='test',
        original_request='test',status='confirmed',steps=[step],created_at='2026-09-08',updated_at='2026-09-08')


class PlanningResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry=CapabilityRegistry(ROOT/'skills')

    def test_planning_capabilities_never_grant_execution(self):
        store=self.registry.definitions;store.load()
        for method in self.registry.all_methods():
            if method.entry_kind=='capability':
                self.assertEqual(method.execution_mode,'planning')
                self.assertFalse(method.runner_id)
        self.assertEqual(set(store.methods),RunnerRegistry().ids())

    def test_all_selectable_recipes_compile_only_registered_existing_calls(self):
        runners=RunnerRegistry().ids()
        count=0
        for cap in self.registry.capabilities():
            for definition in self.registry.selectable_methods_for(cap):
                if not definition.execution_recipe:continue
                with self.subTest(method=definition.method_id):
                    units=RecipeCompiler(self.registry).compile(workflow_for(definition))
                    self.assertEqual(len(units),sum(all(workflow_for(definition).steps[0].selected_parameters.get(k) in v for k,v in step.when.items()) for step in definition.execution_recipe.steps))
                    self.assertTrue(all(u.method.runner_id in runners and u.method.execution_mode=='runner' for u in units))
                    self.assertTrue(all(u.method.execution_recipe is None for u in units))
                    count+=1
        self.assertGreater(count,10)

    def test_act_sift2_preserves_existing_5tt_and_routes_stage_parameters(self):
        definition=self.registry.method_for('dwi.tractography','mrtrix3-act-sift2--via-rebuild-fod-connectome')
        workflow=workflow_for(definition)
        step=workflow.steps[0]
        self.assertNotIn('t1w',{s.id for s in definition.input_slots})
        self.assertNotIn('tracking_postprocessing',step.selected_parameters)
        step.selected_parameters.update(tracking_algorithm='SD_STREAM',threads=3)
        step.input_bindings['five_tt']=InputBinding(slot_id='five_tt',artifact_type='five_tt_image',
            source='existing_path',absolute_path='/approved/sample/5tt.mif')
        units=RecipeCompiler(self.registry).compile(workflow)
        self.assertEqual([u.method.runner_id for u in units],
            ['mrtrix.dwi2response','mrtrix.dwi2fod','mrtrix.tckgen','mrtrix.command.tcksift2','mrtrix.tck2connectome'])
        self.assertEqual([u.step.selected_parameters.get('algorithm') for u in units[:3]],
            ['dhollander','msmt_csd','SD_STREAM'])
        self.assertTrue(all(u.step.selected_parameters.get('threads',u.step.selected_parameters.get('nthreads'))==3 for u in units))
        self.assertEqual(units[2].step.input_bindings['five_tt'].absolute_path,'/approved/sample/5tt.mif')
        self.assertEqual(units[-1].step.input_bindings['weights'].upstream_step_id,'analysis__tracking_sift2')
        self.assertEqual(units[-1].step.input_bindings['weights'].output_slot_id,'out_weights')

    def test_fixed_conditions_expose_required_inputs_and_real_outputs(self):
        definition=self.registry.method_for('dwi.tractography','mrtrix3-act-sift2--via-existing-fod-act-sift2')
        self.assertTrue(next(s for s in definition.input_slots if s.id=='five_tt').required)
        self.assertEqual(next(s for s in definition.output_slots if s.id=='weights').generated_when,{})
        self.assertNotIn('postprocessing',{p.id for p in definition.parameters})
        self.assertFalse(any(s.capability=='dwi.five_tt' for s in definition.execution_recipe.steps))

    def test_recipe_rejects_relabelled_input_type_and_unknown_parameter_mapping(self):
        definition=self.registry.method_for('dwi.tractography','mrtrix3-act-sift2--via-existing-fod-act-sift2')
        workflow=workflow_for(definition)
        workflow.steps[0].input_bindings['five_tt']=InputBinding(slot_id='five_tt',source='existing_path',artifact_type='fod_image',absolute_path='/wrong/file')
        with self.assertRaisesRegex(RecipeCompilationError,'类型不匹配'):
            RecipeCompiler(self.registry).compile(workflow)
        workflow.steps[0].input_bindings={}
        workflow.steps[0].method_options[0].execution_recipe.steps[0].parameter_bindings['made_up']='threads'
        with self.assertRaisesRegex(RecipeCompilationError,'参数映射'):
            RecipeCompiler(self.registry).compile(workflow)

    def test_missing_original_api_is_not_replaced_by_a_similar_algorithm(self):
        source=self.registry.method_for('skill.eeg-skill','eeg-skill-guided--frequency-band-energy-extraction')
        self.assertEqual(source.execution_mode,'planning')
        self.assertEqual(source.execution_resolution['state'],'source_error')
        methods=self.registry.selectable_methods_for('dwi.tractography')
        self.assertIn('mrtrix3-existing-fod',{m.method_id for m in methods})

    def test_original_model_architectures_and_training_loops_are_unchanged(self):
        sources=json.loads((ROOT/'bakend/resources/existing_implementation_sources.json').read_text(encoding='utf-8'))['files']
        for path in ['skills/bnt/scripts/train_reference.py','models/brainnetcnn/scripts/train.py',
                     'skills/combraintf/scripts/train_reference.py','models/bnt/net/bnt.py',
                     'models/brainnetcnn/net/brainnetcnn.py','models/combraintf/net/combraintf.py']:
            digest=hashlib.sha256((ROOT/path).read_text(encoding='utf-8-sig').encode('utf-8')).hexdigest()
            self.assertEqual(digest,sources[path]['source_sha256'],path)
