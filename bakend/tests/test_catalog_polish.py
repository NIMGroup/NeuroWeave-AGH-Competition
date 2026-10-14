"""Regression coverage for dataset navigation, locale projection and native cleanup."""
import copy,json,re,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.method_registry import MethodRegistry
from app.catalog_taxonomy import catalog_entries
from app.catalog_locale import CatalogLocaleMiddleware,localize,localize_workflow,translate
from app.capability_registry import CapabilityRegistry
from app.skill_catalog import SkillCatalog
from app.skill_loader import SkillLoader

ROOT=Path(__file__).resolve().parents[2]

def test_dataset_skills_have_an_exclusive_category_and_keep_functional_methods():
 store=MethodRegistry(ROOT);store.load();entries=catalog_entries(store)
 datasets={s['id'] for s in store.skills.values() if s.get('scope')=='dataset'}
 assert len(datasets)==29 and {'abcd-skill','ukb-skill','seed-iv-skill'}<=datasets
 for item in entries:
  if item['id'] in datasets:assert item['categories']==[['datasets','cohorts']]
  if item['kind']=='method':assert item['category_ids'][0]!='datasets'
 tree=json.loads((ROOT/'skills/capability.tree.json').read_text(encoding='utf-8'))
 def leaves(node):
  yield node
  for child in node.get('children',[]):yield from leaves(child)
 for node in leaves(tree['root']):
  if set(node.get('skill_ids',[])) & datasets:assert node['category_ids']==['datasets','cohorts']

def test_every_metadata_string_has_a_translation_and_values_are_immutable():
 sys.path.insert(0,str(ROOT/'scripts'))
 from catalog_translation import check
 result=check(ROOT)
 assert result['missing']==[]
 assert result['invalid_placeholders']==[]
 store=MethodRegistry(ROOT);store.load()
 for method in store.methods.values():
  source=method.model_dump(mode='json');original=copy.deepcopy(source)
  for lang in ('en','zh'):
   view=localize(source,lang)
   assert view['execution']==source['execution']
   for before,after in zip(source['parameters'],view['parameters']):
    for key in ('id','argument','default','visible_when','required_when','enabled_when','conflicts_with'):
     assert before.get(key)==after.get(key),(method.id,key)
    assert [o['value'] for o in before['options']]==[o['value'] for o in after['options']]
   if lang=='en':assert not re.search(r'[\u3400-\u9fff]',view['description']),method.id
  assert source==original

def test_workflow_projection_preserves_user_data_and_translates_only_contracts():
 value={'workflow_id':'saved','title':'输入','summary':'用户说明','steps':[{'title':'用户步骤','selected_parameters':{'name':'输入'},'input_bindings':{'input':{'absolute_path':'/data/输入.nii.gz'}},'method_options':[{'id':'m','label':'输入','parameters':[{'id':'mode','default':'输入','options':[{'value':'输入','label':'输入'}]}]}]}]}
 projected=localize_workflow(value,'en')
 assert projected['title']==value['title'] and projected['summary']==value['summary']
 assert projected['steps'][0]['selected_parameters']==value['steps'][0]['selected_parameters']
 assert projected['steps'][0]['input_bindings']==value['steps'][0]['input_bindings']
 assert projected['steps'][0]['method_options'][0]['label']!='输入'
 assert projected['steps'][0]['method_options'][0]['parameters'][0]['default']=='输入'

def test_preview_and_saved_pipeline_user_names_are_not_translated():
 for identity in ('preview','user-123'):
  payload={'kind':'pipeline','id':identity,'editor':{'name':'输入'},'label':'输入','description':'输入','input_slots':[{'label':'输入'}]}
  result=localize(payload,'en')
  assert result['label']=='输入' and result['description']=='输入'
  assert result['editor']==payload['editor']
  assert result['input_slots'][0]['label']!='输入'

def test_locale_applies_to_pipeline_validation_and_workflow_mutation_responses():
 app=FastAPI();app.add_middleware(CatalogLocaleMiddleware)
 @app.post('/api/library/pipelines/validate')
 def validate():return {'id':'preview','kind':'pipeline','editor':{'name':'输入'},'label':'输入','input_slots':[{'label':'输入'}]}
 @app.patch('/api/workflows/saved/steps/s/parameters')
 def change():return {'workflow_id':'saved','title':'输入','steps':[{'method_options':[{'label':'输入'}],'selected_parameters':{'mode':'输入'}}]}
 with TestClient(app) as client:
  preview=client.post('/api/library/pipelines/validate',headers={'Accept-Language':'en'}).json()
  assert preview['label']=='输入' and preview['input_slots'][0]['label']!='输入'
  result=client.patch('/api/workflows/saved/steps/s/parameters',headers={'Accept-Language':'en'}).json()
  assert result['title']=='输入' and result['steps'][0]['method_options'][0]['label']!='输入'
  chinese=client.patch('/api/workflows/saved/steps/s/parameters',headers={'Accept-Language':'zh'}).json()
  assert chinese['steps'][0]['method_options'][0]['label']=='输入'

def test_scientific_method_details_do_not_require_mrtrix_option_inventory():
 registry=CapabilityRegistry(ROOT/'skills')
 method=registry.definitions.method('fsl.command.fslmaths')
 runner=SimpleNamespace(contract=method.execution['contract'],runtime_status=lambda:{'available':False,'reason':'test'})
 catalog=SkillCatalog(SkillLoader(ROOT/'skills'),registry,SimpleNamespace(get=lambda _:runner))
 detail=catalog.method(method_id=method.id)
 assert detail['command_coverage']['total'] is None
 assert detail['command_coverage']['exposed']==len(method.parameters)
 functions=catalog.skill_structure('fsl-tool')['functions']
 assert any(f.get('method_ref')==method.id and f['description']==method.description for f in functions)

def test_template_translation_preserves_path_values():
 path='/datasets/原始影像/sub-01.nii.gz'
 source='未找到可执行文件：'+path
 result=translate(source,'en')
 assert path in result and '未找到' not in result
 assert translate('s1 · 输入','en').startswith('s1 · ')

def test_reference_sources_survive_cleanup():
 registry=MethodRegistry(ROOT);registry.load()
 for method in registry.methods.values():
  for reference in method.source_references:assert (ROOT/reference.path).is_file(),(method.id,reference.path)
  adapter=method.execution.get('adapter',{})
  if adapter.get('script'):assert (ROOT/adapter['script']).is_file()
 assert not list((ROOT/'skills').glob('*/OVERVIEW.md'))
 assert not list((ROOT/'skills').glob('*/planning.manifest.json'))
 assert not list((ROOT/'skills').glob('*/*.manifest.json'))
