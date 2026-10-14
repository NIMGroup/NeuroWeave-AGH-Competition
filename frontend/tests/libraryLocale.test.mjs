import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resolve} from 'node:path';
import {readFile} from 'node:fs/promises';
import {createServer} from 'vite';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const server=await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const {useLanguageStore}=await server.ssrLoadModule('/src/stores/languageStore.ts');
  const {ui}=await server.ssrLoadModule('/src/lib/libraryText.ts');
  await test('library labels follow the selected language and preserve placeholders',async()=>{
    useLanguageStore.setState({language:'en'});
    assert.equal(ui('查看详情'),'Details');
    const dictionary=JSON.parse(await readFile(resolve(import.meta.dirname,'../src/lib/library-en.json'),'utf8'));
    for(const [source,text] of Object.entries(dictionary))assert.deepEqual(source.match(/\{\d+\}/g),text.match(/\{\d+\}/g),source);
    useLanguageStore.setState({language:'zh'});assert.equal(ui('查看详情'),'查看详情');
  });
  await test('functional navigation renders stable category identities and whole catalog counts',async()=>{
    useLanguageStore.setState({language:'en'});
    const {LibraryNavigation}=await server.ssrLoadModule('/src/features/library/LibraryNavigation.tsx');
    const html=renderToStaticMarkup(createElement(LibraryNavigation,{
      facets:{categories:[{path:['diffusion'],label:'Diffusion',count:157},{path:['diffusion','tracking'],label:'Tractography',count:20}],software:[{id:'mrtrix3',label:'MRtrix3',count:90}]},
      category:'diffusion/tracking',software:'mrtrix3',onCategory:()=>{},onSoftware:()=>{}}));
    for(const label of ['Browse by goal','Tractography','157','MRtrix3','Clear category'])assert.ok(html.includes(label),label);
    assert.ok(html.includes('aria-current="page"'));
    assert.ok(html.includes('overflow-y-auto'));
  });
} finally {await server.close();}
