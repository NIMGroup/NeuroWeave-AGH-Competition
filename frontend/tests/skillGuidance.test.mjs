import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resolve} from 'node:path';
import {createServer} from 'vite';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
const server=await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const {SkillDetails}=await server.ssrLoadModule('/src/features/skills/SkillDetails.tsx');
  const {useLanguageStore}=await server.ssrLoadModule('/src/stores/languageStore.ts');
  const language=useLanguageStore.getInitialState().language;
  const render=orchestration=>{
    const skill={name:'sample',display_name:'Sample',description:'Unique overview',source:'NeuroWeave',skill_type:'task',runtime:'registered-methods',dependencies:[],files:[],orchestration};
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    client.setQueryData(['skill-detail','sample',language],skill);
    const noop=()=>{};
    const html=renderToStaticMarkup(createElement(QueryClientProvider,{client},createElement(SkillDetails,{skill,selected:false,disabled:false,onAdd:noop,onRemove:noop,onClose:noop})));
    client.clear();return html;
  };
  await test('skills without additional guidance omit empty sections and show the description once',()=>{
    const html=render({description:'Unique overview',applicability:[],selection_rules:[],limitations:[],examples:[],functions:[]});
    assert.equal(html.split('Unique overview').length-1,1);
    assert.doesNotMatch(html,/Skill-specific guidance|Skill 专属指导|Selection guidance|选择依据|Limitations|限制/);
  });
  await test('specific rules and limits remain visible without unrelated empty headings',()=>{
    const html=render({description:'Unique overview',selection_rules:[],limitations:['Only a single 3D volume'],examples:[],functions:[]});
    assert.match(html,/Only a single 3D volume/);
    assert.match(html,/Skill-specific guidance|Skill 专属指导/);
    assert.doesNotMatch(html,/Selection guidance|选择依据/);
    assert.equal(html.split('Unique overview').length-1,1);
  });
}finally{await server.close();}
