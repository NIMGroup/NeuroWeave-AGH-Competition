import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resolve} from 'node:path';
import {createServer} from 'vite';
const server=await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const g=await server.ssrLoadModule('/src/features/library/canvasGeometry.ts');
  const c=await server.ssrLoadModule('/src/features/library/canvasConnections.ts');
  await test('zoom keeps the world point underneath the pointer and clamps at canvas origin',()=>{
    const scroll={x:700,y:350},anchor={x:210,y:150};
    for(const to of [.5,1,1.4,2]){
      const next=g.zoomAnchor(scroll,anchor,.8,to);
      assert.ok(Math.abs((next.x+anchor.x)/to-(scroll.x+anchor.x)/.8)<1e-8);
      assert.ok(Math.abs((next.y+anchor.y)/to-(scroll.y+anchor.y)/.8)<1e-8);
    }
    assert.deepEqual(g.zoomAnchor({x:0,y:0},anchor,1,.5),{x:0,y:0});
  });
  await test('fit includes distant and tall graph nodes inside the viewport',()=>{
    const bounds=g.enclosingBounds([{x:80,y:80,width:280,height:700},{x:1700,y:1100,width:280,height:850}]);
    const viewport={width:840,height:470},fit=g.fitBounds(bounds,viewport);
    assert.ok(bounds.x*fit.zoom-fit.scroll.x>=0);
    assert.ok(bounds.y*fit.zoom-fit.scroll.y>=0);
    assert.ok((bounds.x+bounds.width)*fit.zoom-fit.scroll.x<=viewport.width);
    assert.ok((bounds.y+bounds.height)*fit.zoom-fit.scroll.y<=viewport.height);
  });
  await test('wire routes avoid intervening node bodies, including reverse and shared links',()=>{
    const boxes=[{x:80,y:80,width:280,height:240},{x:580,y:80,width:280,height:240},{x:1080,y:80,width:280,height:240}];
    for(const [a,b,shared] of [[{x:360,y:152},{x:1080,y:152},false],[{x:1360,y:184},{x:80,y:184},false],[{x:80,y:216},{x:1080,y:216},true]]){
      const route=g.routeConnection(a,b,boxes,1,shared);
      assert.deepEqual(route[0],a);assert.deepEqual(route.at(-1),b);
      route.slice(1).forEach((p,i)=>{
        assert.ok(p.x===route[i].x||p.y===route[i].y);
        for(const box of boxes) assert.equal(g.segmentCrossesBox(route[i],p,box),false,JSON.stringify({route,box}));
      });
    }
  });
  await test('parallel response ports have distinct routes and unambiguous endpoint identities',()=>{
    const slots=['wm','gm','csf'].map(id=>({id,label:id.toUpperCase()}));
    const definitions={response:{label:'Response',output_slots:slots,input_slots:[]},fod:{label:'FOD',input_slots:slots,output_slots:[]}};
    const steps=[{id:'a',method_id:'response',inputs:{}},{id:'b',method_id:'fod',inputs:Object.fromEntries(slots.map(s=>[s.id,{step_id:'a',output_slot_id:s.id}]))}];
    const edges=c.canvasConnections(steps,definitions,[],(id,slot)=>({x:id==='a'?360:780,y:152+slots.findIndex(s=>s.id===slot)*32}),[]);
    assert.equal(new Set(edges.map(e=>e.id)).size,3);assert.equal(new Set(edges.map(e=>e.path)).size,3);
    edges.forEach((edge,i)=>assert.equal(c.connectionLabel(edge),`1. Response / ${slots[i].label} → 2. FOD / ${slots[i].label}`));
  });
} finally { await server.close(); }
