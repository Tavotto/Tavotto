import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {uiHTML,uiScript} from '../src/native/ui.ts';

// Exercise the actual shipped client in an isolated browser-shaped context.
// Only the DOM operations used by this client are implemented; no network or
// sign-in occurs, and API replies are synthetic server observations.
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.options=[];this.value='';this.textContent='';this.disabled=false;this.checked=false;}
  replaceChildren(...nodes){this.children=nodes;if(this.tagName==='select'){this.options=nodes;this.value=nodes[0]?.value??'';}}
  add(option){this.options.push(option);}
  append(...nodes){this.children.push(...nodes);}
  reset(){}
}
const ID=n=>String(n).padStart(43,'0');
const actor={id:2,login:'synthetic-contributor',role:'contributor'};
function status(id=ID(1),changes={}){
  return {packet:{id,subject:2,valid_until:100},actor,now:100,completed:false,blocked:false,current:false,
    signatures:[],canSign:false,canHold:true,...changes};
}
async function browser({pages=[{actor,packets:[{id:ID(1)}],nextCursor:null}],states={[ID(1)]:status()},signingEnabled=true,stored='',urlPacket='',createID=ID(99),failPage=false}={}){
  const elements=new Map([...uiHTML.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element(match[1]==='packets'?'select':'div')]));
  const inputs=()=>{
    const walk=node=>[...(node.tagName==='input'?[node]:[]),...(node.children??[]).flatMap(walk)];
    return walk(elements.get('attestations'));
  };
  const document={cookie:'__Host-cla-csrf=synthetic',getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),
    createTextNode:text=>({textContent:text}),querySelectorAll:selector=>selector==='#attestations input'?inputs():[]};
  const location={href:'https://cla.invalid/'+(urlPacket?'?packet='+encodeURIComponent(urlPacket):''),assign(){throw Error('NAVIGATION_FORBIDDEN');}};
  const storage=new Map(stored?[['cla.packet',stored]]:[]),calls=[];
  const context={document,URL,Blob,location,Option:class {constructor(label,value){this.textContent=label;this.value=value;}},
    sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    history:{replaceState:(_state,_title,url)=>{location.href=new URL(url,location.href).href;}},
    fetch:async(url,options)=>{
      calls.push({url,options});let result;
      if(url==='/capabilities')result={signingEnabled,oauthEnabled:false};
      else if(url==='/session'){result=pages[0];if(result.error)return {ok:false,json:async()=>structuredClone(result)};}
      else if(url.startsWith('/session?cursor=')){
        if(failPage)return {ok:false,json:async()=>({error:'request_unavailable'})};
        const cursor=decodeURIComponent(url.split('=')[1]),index=pages.findIndex(page=>page.nextCursor===cursor);
        assert.ok(index>=0&&pages[index+1],'unknown cursor');result=pages[index+1];
      }else if(url.startsWith('/status?packet=')){
        const id=decodeURIComponent(url.split('=')[1]);assert.ok(states[id],'unknown status packet');result=states[id];
        if(result.error)return {ok:false,json:async()=>structuredClone(result)};
      }else if(url.startsWith('/agreement'))result={version:'1.0',digest:'a'.repeat(64),content:'SYNTHETIC AGREEMENT'};
      else if(url==='/packets'){
        result={id:createID};states[createID]=status(createID,{packet:{id:createID,subject:2,valid_until:1000},current:true,canSign:true});
        pages[0]={actor,packets:[{id:createID},...pages[0].packets],nextCursor:pages[0].nextCursor};
      }else throw Error('UNEXPECTED_API '+url);
      return {ok:true,json:async()=>structuredClone(result)};
    }};
  vm.runInNewContext(uiScript(),context);
  const settle=()=>new Promise(resolve=>setImmediate(resolve));
  await settle();
  return {elements,calls,states,pages,storage,inputs,location,settle,
    click:async id=>{assert.equal(elements.get(id).disabled,false,id+' must be enabled');elements.get(id).onclick();await settle();},
    select:async id=>{elements.get('packets').value=id;elements.get('packets').onchange();await settle();}};
}

test('auto-selected expired incomplete record permits a fresh packet with cleared consent',async()=>{
  const b=await browser();
  assert.equal(b.elements.get('packets').value,ID(1));
  assert.equal(b.elements.get('create').disabled,false);
  b.elements.get('signature').value='OLD SYNTHETIC SIGNATURE';for(const input of b.inputs())input.checked=true;
  await b.click('create');
  assert.equal(b.calls.filter(call=>call.url==='/packets').length,1);
  assert.equal(b.elements.get('packets').value,ID(99));
  assert.equal(b.storage.get('cla.packet'),ID(99));
  assert.equal(b.elements.get('signature').value,'');assert.ok(b.inputs().every(input=>!input.checked));
  assert.equal(b.elements.get('create').disabled,true);
  assert.ok(b.calls.every(call=>call.url!=='/sign'&&call.url!=='/intents'));
});

for(const [label,changes] of [
  ['completed after its original deadline',{completed:true,current:true,signatures:[{role:'contributor'},{role:'holder'}]}],
  ['held expired incomplete',{blocked:true}],
  ['still pending before deadline',{now:99,current:true}],
  ['missing explicit completion observation',{completed:undefined}],
  ['missing explicit hold observation',{blocked:undefined}],
  ['server time unavailable',{now:undefined}],
  ['another contributor actor',{actor:{...actor,id:3}}],
  ['another subject',{packet:{id:ID(1),subject:3,valid_until:100}}],
])test('create stays disabled for '+label,async()=>{
  const b=await browser({states:{[ID(1)]:status(ID(1),changes)}});
  assert.equal(b.elements.get('create').disabled,true);
  assert.equal(b.calls.some(call=>call.url==='/packets'),false);
});

test('holder and signing-disabled contributors cannot create expired replacements',async()=>{
  const holder={id:1,login:'synthetic-holder',role:'holder'};
  const h=await browser({pages:[{actor:holder,packets:[{id:ID(1)}],nextCursor:null}],states:{[ID(1)]:status(ID(1),{actor:holder})}});
  assert.equal(h.elements.get('create').disabled,true);
  const off=await browser({signingEnabled:false});assert.equal(off.elements.get('create').disabled,true);
});

test('new contributor can create, but clearing a populated selection does not offer a duplicate',async()=>{
  const empty=await browser({pages:[{actor,packets:[],nextCursor:null}]});assert.equal(empty.elements.get('create').disabled,false);
  const many=await browser({pages:[{actor,packets:[{id:ID(1)},{id:ID(2)}],nextCursor:null}]});
  assert.equal(many.elements.get('create').disabled,true);
  assert.equal(many.calls.some(call=>call.url.startsWith('/status')),false);
});

test('load-more is manual, uses server cursor, preserves selection, and reaches packet beyond first 20',async()=>{
  const holder={id:1,login:'synthetic-holder',role:'holder'};
  const packets=Array.from({length:25},(_,n)=>({id:ID(n+1)}));
  const pages=[{actor:holder,packets:packets.slice(0,20),nextCursor:ID(20)},{actor:holder,packets:packets.slice(20),nextCursor:null}];
  const states={[ID(1)]:status(ID(1),{actor:holder}),[ID(25)]:status(ID(25),{actor:holder})};
  const b=await browser({pages,states,stored:ID(1)});
  assert.equal(b.calls.filter(call=>call.url.startsWith('/session')).length,1);
  assert.equal(b.elements.get('packets').options.length,21);
  await b.click('packets-more');
  assert.equal(b.calls.filter(call=>call.url.startsWith('/session')).length,2);
  assert.equal(b.calls.at(-1).url,'/session?cursor='+ID(20));
  assert.equal(b.elements.get('packets').value,ID(1));
  assert.equal(b.elements.get('packets').options.length,26);
  assert.equal(b.elements.get('packets-more').disabled,true);
  await b.select(ID(25));
  assert.ok(b.calls.some(call=>call.url==='/status?packet='+ID(25)));
  assert.equal(b.elements.get('packets').value,ID(25));
  assert.equal(b.storage.get('cla.packet'),ID(25));
  assert.equal(b.elements.get('packets').options.filter(option=>option.value===ID(25)).length,1);
  await b.click('packets-more');
  assert.equal(b.elements.get('packets').options.filter(option=>option.value===ID(25)).length,1);
  assert.equal(b.elements.get('packets').options.length,26);
  assert.ok(b.calls.every(call=>call.options.method==='GET'));
});

test('failed load-more stops with no automatic retry or mutation',async()=>{
  const b=await browser({pages:[{actor,packets:[{id:ID(1)}],nextCursor:ID(1)}],stored:ID(1),failPage:true});
  await b.click('packets-more');
  assert.equal(b.elements.get('packets-more').disabled,true);
  assert.equal(b.elements.get('create').disabled,true);
  assert.match(b.elements.get('message').textContent,/request_unavailable/);
  assert.equal(b.calls.filter(call=>call.url.startsWith('/session?')).length,1);
  assert.ok(b.calls.every(call=>call.options.method==='GET'));
});

for(const source of ['session storage','URL'])test('unauthorized '+source+' packet is discarded while current account history remains usable',async()=>{
  const other={id:3,login:'synthetic-other',role:'contributor'};
  const packets=Array.from({length:21},(_,n)=>({id:ID(n+30)}));
  const pages=[{actor:other,packets:packets.slice(0,20),nextCursor:ID(49)},{actor:other,packets:packets.slice(20),nextCursor:null}];
  const states={[ID(1)]:{error:'packet_unavailable'},[ID(50)]:status(ID(50),{actor:other,packet:{id:ID(50),subject:3,valid_until:100}})};
  const b=await browser({pages,states,stored:ID(1),...(source==='URL'?{urlPacket:ID(1)}:{})});
  assert.equal(b.storage.has('cla.packet'),false);assert.equal(new URL(b.location.href).search,'');
  assert.equal(b.elements.get('packets').value,'');
  assert.equal(b.elements.get('packets').options.some(option=>option.value===ID(1)),false);
  assert.deepEqual(b.elements.get('packets').options.slice(1).map(option=>option.value),packets.slice(0,20).map(packet=>packet.id));
  assert.equal(b.elements.get('sign').disabled,true);assert.equal(b.elements.get('hold').disabled,true);
  assert.equal(b.elements.get('download-contributor').disabled,true);assert.equal(b.elements.get('download-holder').disabled,true);
  await b.click('refresh');
  assert.equal(b.calls.filter(call=>call.url==='/status?packet='+ID(1)).length,1,'manual refresh must not retry the rejected cached ID');
  await b.click('packets-more');
  assert.equal(b.elements.get('packets').options.some(option=>option.value===ID(1)),false);
  assert.equal(b.elements.get('packets').options.length,22);
  await b.select(ID(50));
  assert.equal(b.elements.get('packets').value,ID(50));
  assert.equal(b.storage.get('cla.packet'),ID(50));
  assert.equal(b.elements.get('packets').options.filter(option=>option.value===ID(50)).length,1,'authorized historical selection remains pinned');
  assert.equal(b.elements.get('packets').options.some(option=>option.value===ID(1)),false);
  assert.ok(b.calls.every(call=>call.options.method==='GET'));
});

test('account switch clears stale consent and packet state without reintroducing the old selection',async()=>{
  const old=status(ID(1),{signatures:[{role:'contributor'}],canSign:true});
  const b=await browser({states:{[ID(1)]:old}});
  b.elements.get('name').value='SYNTHETIC OLD NAME';b.elements.get('address').value='SYNTHETIC OLD ADDRESS';
  b.elements.get('signature').value='SYNTHETIC OLD NAME';b.elements.get('hold-confirm').checked=true;
  for(const input of b.inputs())input.checked=true;
  assert.equal(b.elements.get('download-contributor').disabled,false);
  const other={id:3,login:'synthetic-other',role:'contributor'};
  b.pages[0]={actor:other,packets:[{id:ID(30)},{id:ID(31)}],nextCursor:null};
  b.states[ID(1)]={error:'packet_unavailable'};
  await b.click('refresh');
  for(const id of ['name','address','signature'])assert.equal(b.elements.get(id).value,'');
  assert.equal(b.elements.get('hold-confirm').checked,false);assert.equal(b.inputs().length,0);
  assert.equal(b.elements.get('record-time').textContent,'');
  assert.equal(b.elements.get('contributor-state').textContent,'尚未签署');
  assert.equal(b.elements.get('sign').disabled,true);assert.equal(b.elements.get('hold').disabled,true);
  assert.equal(b.elements.get('download-contributor').disabled,true);assert.equal(b.elements.get('download-holder').disabled,true);
  assert.equal(b.storage.has('cla.packet'),false);assert.equal(new URL(b.location.href).search,'');
  assert.deepEqual(b.elements.get('packets').options.map(option=>option.value),['',ID(30),ID(31)]);
  const callsAfterSwitch=b.calls.length;
  await b.click('refresh');
  assert.equal(b.calls.slice(callsAfterSwitch).some(call=>call.url==='/status?packet='+ID(1)),false);
  assert.deepEqual(b.elements.get('packets').options.map(option=>option.value),['',ID(30),ID(31)]);
  assert.ok(b.calls.every(call=>call.options.method==='GET'));
});

for(const interrupted of [false,true])test('contributor to holder on the same authorized packet clears prior actor consent and identity fields'+(interrupted?' after an expired session':''),async()=>{
  const b=await browser({states:{[ID(1)]:status(ID(1),{signatures:[{role:'contributor'}]})}});
  b.elements.get('name').value='SYNTHETIC CONTRIBUTOR NAME';b.elements.get('address').value='SYNTHETIC CONTRIBUTOR ADDRESS';
  b.elements.get('signature').value='SYNTHETIC CONTRIBUTOR NAME';b.elements.get('hold-confirm').checked=true;
  for(const input of b.inputs())input.checked=true;
  if(interrupted){b.pages[0]={error:'session_rejected'};await b.click('refresh');}
  const holder={id:1,login:'synthetic-holder',role:'holder'};
  b.pages[0]={actor:holder,packets:[{id:ID(1)}],nextCursor:null};
  b.states[ID(1)]=status(ID(1),{actor:holder,signatures:[{role:'contributor'}],canSign:true,canHold:true});
  await b.click('refresh');
  for(const id of ['name','address','signature'])assert.equal(b.elements.get(id).value,'');
  assert.equal(b.elements.get('hold-confirm').checked,false);assert.ok(b.inputs().every(input=>!input.checked));
  assert.equal(b.inputs().length,5);assert.ok(b.inputs().some(input=>input.name==='reviewed'));
  assert.equal(b.elements.get('packets').value,ID(1));assert.equal(b.storage.get('cla.packet'),ID(1));
  assert.equal(new URL(b.location.href).searchParams.get('packet'),ID(1));
  assert.equal(b.elements.get('sign').textContent,'由我本人签署（权利人）');
  assert.equal(b.elements.get('sign').disabled,false);assert.equal(b.elements.get('hold').disabled,false);
  assert.equal(b.elements.get('create').disabled,true);
  assert.ok(b.calls.every(call=>call.options.method==='GET'));
});
