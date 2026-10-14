from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import json,os,unittest
from unittest.mock import patch,AsyncMock

from app.capability_registry import CapabilityRegistry
from app.capability_tree import CapabilityTree
from app.catalog_taxonomy import catalog_entries,facets
from app.catalog_locale import localize
from app.library_retrieval import retrieve,compact_catalog
from app.method_planning import MethodContractIndex
from app.task_contract import TaskContract
from app.local_store import LocalStore
from app.pipeline_library import PipelineLibrary
from app.execution.runners.scientific_commands import scientific_runners
from app.execution.base import RunnerContext
from app.workflow_models import Workflow,WorkflowStep,InputBinding

ROOT=Path(__file__).resolve().parents[2]


class LibraryUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry=CapabilityRegistry(ROOT/'skills')
        cls.index=MethodContractIndex(cls.registry)
        cls.runners={runner.runner_id:runner for runner in scientific_runners()}

    def test_whole_catalog_facets_do_not_depend_on_current_page(self):
        with TemporaryDirectory() as folder:
            library=PipelineLibrary(LocalStore(Path(folder)/'test.db'),self.registry)
            first=library.catalog('method',limit=5)
            last=library.catalog('method',offset=20,limit=5)
            self.assertEqual(first['facets'],last['facets'])
            self.assertGreater(first['total'],300)
            self.assertEqual(sum(c['count'] for c in first['facets']['categories'] if len(c['path'])==1),first['total'])
            for category in first['facets']['categories']:
                page=library.catalog('method',category='/'.join(category['path']),limit=200)
                self.assertEqual(page['total'],category['count'])
            fsl=library.catalog('method',q='fslmaths',software='fsl',language='en')
            self.assertEqual([v['id'] for v in fsl['items']],['fsl.command.fslmaths'])

    def test_tree_explicitly_covers_native_entities_and_has_function_branches(self):
        tree=CapabilityTree(ROOT/'skills/capability.tree.json')
        self.assertTrue(all(c.id.startswith('function.') for c in tree.children(tree.root_id)))
        mids={mid for node in tree.leaves() for mid in node.method_ids}
        self.registry._load()
        self.assertEqual(mids,set(self.registry.definitions.methods))
        self.assertEqual({sid for node in tree.leaves() for sid in node.skill_ids},set(self.registry.definitions.skills))
        self.assertTrue(any(node.pipeline_ids for node in tree.leaves()))

    def test_exact_command_and_semantic_rewrites_ignore_wrong_tree_route(self):
        scope=TaskContract(goal='校正偏置场',goal_artifact_types=['imaging_input'],retrieval_queries=['N4 bias field correction'])
        ids,trace=retrieve(self.index,scope,['dwi.tractography'],message='Use N4BiasFieldCorrection',limit=24)
        self.assertEqual(ids[0],'ants.command.N4BiasFieldCorrection')
        self.assertFalse(trace['tree_is_filter'])
        self.assertIn('ants.command.N4BiasFieldCorrection',trace['exact_matches'])
        self.assertTrue(trace['truncated'])
        ids2,_=retrieve(self.index,scope,[],message='N4BiasFieldCorrectionX',limit=24)
        self.assertIn('ants.command.N4BiasFieldCorrection',ids2)
        self.assertNotIn('ants.command.N4BiasFieldCorrection',retrieve(self.index,scope,[],message='N4BiasFieldCorrectionX')[1]['exact_matches'])

    def test_diffusion_goal_recalls_upstream_producers_from_all_methods(self):
        scope=TaskContract(goal='whole brain tractography',goal_artifact_types=['tractogram'],input_policy='only_listed',
            allowed_existing_inputs=[{'artifact_type':k} for k in ['preprocessed_dwi','gradient_table','brain_mask']],
            retrieval_queries=['response function estimation','fiber orientation distribution','tractography'],plan_scope='goal_from_inputs')
        ids,trace=retrieve(self.index,scope,['dwi.preprocess'],message='只用预处理 DWI 和梯度、mask 重建全脑纤维',limit=32)
        self.assertTrue({'mrtrix.dwi2response','mrtrix.dwi2fod','mrtrix.tckgen'}<=set(ids),ids)
        self.assertGreater(trace['indexed_pipelines'],0)
        self.assertTrue(any(row['kind']=='pipeline' for row in compact_catalog(self.index)))

    def test_locale_projection_never_changes_contract_values_or_user_content(self):
        value={'id':'canonical','label':'源标签','parameters':[{'id':'x','label':'源标签','default':'源标签','options':[{'value':'源标签','label':'源标签'}]}],
               'input_bindings':{'path':'源标签'},'history':[{'content':'源标签'}]}
        with patch('app.catalog_locale.translate',side_effect=lambda s,lang:'translated' if s=='源标签' else s):
            projected=localize(value,'en')
            self.assertEqual(projected['label'],'translated')
            self.assertEqual(projected['parameters'][0]['default'],'源标签')
            self.assertEqual(projected['parameters'][0]['options'][0]['value'],'源标签')
            self.assertEqual(projected['history'],value['history'])
            self.assertEqual(localize({'id':'user-123','label':'源标签'},'en')['label'],'源标签')
        self.assertEqual(value['label'],'源标签')

    def context(self,runner,folder,params=None,bindings=None):
        step=WorkflowStep(id='test',order=1,title='test',description='',capability='method',status='confirmed',
            selected_method_id=runner.method.id,method_options=[runner.method],selected_parameters=params or {},
            input_bindings={key:InputBinding(slot_id=key,artifact_type='imaging_input',source='existing_path',absolute_path=str(path)) for key,path in (bindings or {}).items()})
        wf=Workflow(workflow_id='test',template_id='test',title='test',summary='',original_request='test',status='confirmed',steps=[step],created_at='',updated_at='')
        return RunnerContext(run_id='test',workflow=wf,step=step,method=runner.method,output_directory=folder/'outputs',cancel_event=Event(),log=lambda *args:None)

    def test_argv_has_separate_authorized_paths_and_conditional_operands(self):
        runner=self.runners['fsl.command.fslmaths']
        with TemporaryDirectory() as tmp,patch.dict(os.environ,{'LOCALFLOW_RUN_INPUT_ROOTS':tmp}):
            folder=Path(tmp);source=folder/'input with spaces.nii.gz';source.write_bytes(b'image')
            context=self.context(runner,folder,{'operation':'-add','value':3},{'input':source})
            argv=runner.build_argv(context)
            self.assertEqual(argv[1],str(source.resolve()))
            self.assertEqual(argv[2:4],['-add','3'])
            self.assertTrue(Path(argv[4]).is_relative_to(context.output_directory))
            context.step.selected_parameters={'operation':'-mas'}
            with self.assertRaisesRegex(ValueError,'Missing required input'):runner.build_argv(context)
            context.step.selected_parameters={'operation':'-bin','value':9}
            self.assertNotIn('9',runner.build_argv(context))
            context.step.selected_parameters={'operation':'-bin','arbitrary':'--out /etc/file'}
            with self.assertRaises(ValueError):runner.build_argv(context)

    def test_numeric_grammar_and_threads_cannot_escape_execution_contract(self):
        runner=self.runners['ants.command.SmoothImage']
        with TemporaryDirectory() as tmp,patch.dict(os.environ,{'LOCALFLOW_RUN_INPUT_ROOTS':tmp}):
            folder=Path(tmp);source=folder/'input.nii';source.write_bytes(b'image')
            for value in ['1;rm -rf /','/etc/passwd','1\n--output','[1,/tmp/else]']:
                with self.subTest(value=value),self.assertRaises(ValueError):runner.build_argv(self.context(runner,folder,{'sigma':value},{'input':source}))
            argv=runner.build_argv(self.context(runner,folder,{'sigma':'1x2x3'},{'input':source}))
            self.assertIn('1x2x3',argv)
            mr=self.runners['mrtrix3.command.mrcalc']
            with patch.dict(os.environ,{'SLURM_CPUS_PER_TASK':'2'}),self.assertRaises(ValueError):mr.build_argv(self.context(mr,folder,{'nthreads':3},{'input':source}))

    def test_filter_parameters_and_registration_outputs_follow_algorithm(self):
        from app.parameter_rules import matches
        method=self.runners['mrtrix3.command.mrfilter'].method
        self.assertEqual(next(p for p in method.parameters if p.id=='fwhm').visible_when,{'filter':['smooth']})
        register=self.runners['mrtrix3.command.mrregister'].method
        rigid={s.id for s in register.output_slots if matches(s.generated_when,{'type':'rigid'})}
        nonlinear={s.id for s in register.output_slots if matches(s.generated_when,{'type':'nonlinear'})}
        self.assertEqual(rigid,{'registered','rigid'})
        self.assertEqual(nonlinear,{'registered','forward_warp','inverse_warp'})

    def test_all_new_methods_have_documented_fields_and_code_allowlist(self):
        from app.method_validation import documentation_gaps,contract_errors
        for runner in self.runners.values():
            definition=self.registry.definitions.method(runner.runner_id)
            with self.subTest(method=definition.id):
                self.assertEqual(documentation_gaps(definition),[])
                self.assertEqual(contract_errors(definition),[])
                self.assertTrue((ROOT/definition.source_references[0].path).is_file())
                self.assertFalse(any(flag in json.dumps(definition.execution) for flag in ['shell=True','rm -rf']))

    def test_fsl_long_options_bind_values_without_shell_and_keep_spaces(self):
        runner=self.runners['fsl.command.convertwarp']
        with TemporaryDirectory() as tmp,patch.dict(os.environ,{'LOCALFLOW_RUN_INPUT_ROOTS':tmp}):
            folder=Path(tmp);source=folder/'input with spaces.nii.gz';source.write_bytes(b'image')
            argv=runner.build_argv(self.context(runner,folder,{}, {'reference':source}))
            self.assertIn('--ref='+str(source.resolve()),argv)
            self.assertTrue(any(v.startswith('--out=') for v in argv))
            self.assertNotIn('--ref',argv)

    def test_locale_middleware_changes_only_metadata_responses(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.catalog_locale import CatalogLocaleMiddleware
        app=FastAPI();app.add_middleware(CatalogLocaleMiddleware)
        @app.get('/api/library/methods/example')
        @app.get('/api/conversations/example')
        def example():return {'id':'immutable','label':'源标签','content':'用户文字','parameters':[{'default':'源标签'}]}
        with patch('app.catalog_locale.translate',side_effect=lambda s,lang:'Display' if lang=='en' else s),TestClient(app) as client:
            translated=client.get('/api/library/methods/example',headers={'Accept-Language':'en-US'})
            self.assertEqual(translated.json()['label'],'Display')
            self.assertEqual(translated.json()['parameters'][0]['default'],'源标签')
            self.assertEqual(translated.json()['content'],'用户文字')
            self.assertEqual(client.get('/api/conversations/example',headers={'Accept-Language':'en'}).json()['label'],'源标签')
            self.assertIn('Accept-Language',translated.headers['vary'])
