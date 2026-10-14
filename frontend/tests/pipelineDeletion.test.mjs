import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resolve} from 'node:path';
import {createServer} from 'vite';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';

const server=await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const {useLanguageStore}=await server.ssrLoadModule('/src/stores/languageStore.ts');
  useLanguageStore.setState({language:'en'});
  const {PipelineDeleteAction}=await server.ssrLoadModule('/src/features/library/PipelineDeleteAction.tsx');
  const {PipelineTrashList}=await server.ssrLoadModule('/src/features/library/PipelineTrashList.tsx');
  const render=(component,props,setup=()=>{})=>{
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});setup(client);
    const html=renderToStaticMarkup(createElement(QueryClientProvider,{client},createElement(component,props)));
    client.clear();return html;
  };
  await test('only user publications expose a delete action, without performing a deletion while viewing',()=>{
    const pipeline={id:'user-example',version:'3',deletion_revision:2,label:'Published graph'};
    const html=render(PipelineDeleteAction,{pipeline,onDeleted:()=>assert.fail('Viewing cannot delete')});
    assert.match(html,/aria-label="Delete Pipeline: Published graph"/);
    assert.doesNotMatch(html,/role="dialog"/);
    assert.equal(render(PipelineDeleteAction,{pipeline:{...pipeline,id:'builtin'},onDeleted:()=>{}}),'');
  });
  await test('deleted publications expose recovery with the complete version count',()=>{
    const html=render(PipelineTrashList,{},client=>client.setQueryData(['pipeline-trash'],{items:[{
      id:'user-deleted',label:'Deleted graph',version:'3',version_count:3,deletion_revision:1,deleted_at:'2026-10-07T00:00:00Z'
    }]}));
    assert.match(html,/Deleted Pipelines/);assert.match(html,/3 versions/);
    assert.match(html,/aria-label="Restore Pipeline: Deleted graph"/);
    assert.match(html,/previous published or archived status/);
    assert.doesNotMatch(html,/No deleted Pipelines/);
  });
  await test('delete and restore requests retain the publication version and lifecycle conflict guards',async()=>{
    const api=await server.ssrLoadModule('/src/features/library/libraryApi.ts');
    const original=globalThis.fetch,calls=[];
    globalThis.fetch=async(url,options)=>{calls.push({url,method:options.method,body:JSON.parse(options.body)});return new Response('{}',{status:200,headers:{'content-type':'application/json'}});};
    try {
      await api.deletePipeline({id:'user-example',version:'3',deletion_revision:2});
      await api.restorePipeline({id:'user-example',deletion_revision:3});
      assert.equal(calls[0].method,'DELETE');assert.equal(calls[0].url,'/api/library/pipelines/user-example');
      assert.deepEqual(calls[0].body,{expected_version:3,expected_deletion_revision:2});
      assert.equal(calls[1].method,'POST');assert.equal(calls[1].url,'/api/library/pipelines/user-example/restore');
      assert.deepEqual(calls[1].body,{expected_deletion_revision:3});
    }finally{globalThis.fetch=original;}
  });
}finally{await server.close();}
