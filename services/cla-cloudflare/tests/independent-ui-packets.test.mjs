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
async function browser({pages=[{actor,packets:[{id:ID(1)}],nextCursor:null}],states={[ID(1)]:status()},signingEnabled=true,signedPdf=undefined,stored='',urlPacket='',createID=ID(99),failPage=false,language='zh-CN',agreementContent='SYNTHETIC AGREEMENT'}={}){
  const elements=new Map([...uiHTML.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element(match[1]==='packets'?'select':'div')]));
  const inputs=()=>{
    const walk=node=>[...(node.tagName==='input'?[node]:[]),...(node.children??[]).flatMap(walk)];
    return walk(elements.get('attestations'));
  };
  const document={cookie:'__Host-cla-csrf=synthetic',getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),
    createTextNode:text=>({textContent:text}),querySelectorAll:selector=>selector==='#attestations input'?inputs():[]};
  const location={href:'https://cla.invalid/'+(urlPacket?'?packet='+encodeURIComponent(urlPacket):''),assign(){throw Error('NAVIGATION_FORBIDDEN');}};
  const storage=new Map(stored?[['cla.packet',stored]]:[]),calls=[];
  const context={document,URL,Blob,location,navigator:{language},Option:class {constructor(label,value){this.textContent=label;this.value=value;}},
    sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    history:{replaceState:(_state,_title,url)=>{location.href=new URL(url,location.href).href;}},
    fetch:async(url,options)=>{
      calls.push({url,options});let result;
      if(url==='/capabilities')result={signingEnabled,oauthEnabled:false,...(signedPdf===undefined?{}:{signedPdf})};
      else if(url==='/session'){result=pages[0];if(result.error)return {ok:false,json:async()=>structuredClone(result)};}
      else if(url.startsWith('/session?cursor=')){
        if(failPage)return {ok:false,json:async()=>({error:'request_unavailable'})};
        const cursor=decodeURIComponent(url.split('=')[1]),index=pages.findIndex(page=>page.nextCursor===cursor);
        assert.ok(index>=0&&pages[index+1],'unknown cursor');result=pages[index+1];
      }else if(url.startsWith('/status?packet=')){
        const id=decodeURIComponent(url.split('=')[1]);assert.ok(states[id],'unknown status packet');result=states[id];
        if(result.error)return {ok:false,json:async()=>structuredClone(result)};
      }else if(url.startsWith('/agreement'))result={version:'1.0',digest:'a'.repeat(64),content:agreementContent};
      else if(url==='/packets'){
        result={id:createID};states[createID]=status(createID,{packet:{id:createID,subject:2,valid_until:1000},current:true,canSign:true});
        pages[0]={actor,packets:[{id:createID},...pages[0].packets],nextCursor:pages[0].nextCursor};
      }else if(url==='/intents')result={intent:'SYNTHETIC-INTENT'};
      else if(url==='/sign')result={revision:1};
      else if(url==='/release'){const id=JSON.parse(options.body).packet;result={revision:2};
        states[id]={...states[id],released:true,canRelease:false,block:{...states[id].block,releasedAt:1800000000}};}
      else throw Error('UNEXPECTED_API '+url);
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
  assert.equal(b.elements.get('create').disabled,false);assert.equal(b.elements.get('restart').hidden,false);assert.equal(b.elements.get('sign-form').hidden,true);assert.equal(b.elements.get('record-time').textContent,'');assert.equal(b.elements.get('eligibility').textContent,'');
  assert.match(b.elements.get('t-restart-hint').textContent,/7 天/);assert.equal(b.elements.get('create').textContent,'重新签署');
  b.elements.get('signature').value='OLD SYNTHETIC SIGNATURE';for(const input of b.inputs())input.checked=true;
  await b.click('create');
  assert.equal(b.calls.filter(call=>call.url==='/packets').length,1);
  assert.equal(b.elements.get('packets').value,ID(99));
  assert.equal(b.storage.get('cla.packet'),ID(99));
  assert.equal(b.elements.get('signature').value,'');assert.ok(b.inputs().every(input=>!input.checked));
  assert.equal(b.elements.get('create').disabled,true);assert.equal(b.elements.get('restart').hidden,true);assert.equal(b.elements.get('sign-form').hidden,false);
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

test('a new contributor gets a signing record straight away, once, and lands on a ready form',async()=>{
  const b=await browser({pages:[{actor,packets:[],nextCursor:null}]});
  assert.equal(b.calls.filter(call=>call.url==='/packets').length,1);assert.equal(b.elements.get('packets').value,ID(99));
  assert.ok(b.calls.some(call=>call.url==='/status?packet='+ID(99)));assert.equal(b.elements.get('personal-signature').hidden,false);
  assert.equal(b.elements.get('restart').hidden,true);assert.equal(b.elements.get('record-picker').hidden,true);
  assert.equal(b.elements.get('message').textContent,'');assert.ok(b.calls.every(call=>call.url!=='/sign'&&call.url!=='/intents'));
  await b.click('refresh');assert.equal(b.calls.filter(call=>call.url==='/packets').length,1,'refresh never creates another');
});
for(const [label,options] of [['the holder',{pages:[{actor:{id:1,login:'synthetic-holder',role:'holder'},packets:[],nextCursor:null}]}],['paused signing',{pages:[{actor,packets:[],nextCursor:null}],signingEnabled:false}]])
  test('no record is created automatically for '+label,async()=>{const b=await browser(options);assert.equal(b.calls.some(call=>call.url==='/packets'),false);});
test('clearing a populated selection does not offer a duplicate',async()=>{
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

for(const current of [true,false])test('delegated UI distinguishes system receipt from personal holder signature current='+current,async()=>{
  const b=await browser({states:{[ID(1)]:status(ID(1),{current:true,completed:current,signatures:[{role:'contributor'}],delegated:{recorded:true,current,state:current?'current':'revoked',canAudit:true,expiresAt:1000}})}});
  assert.match(b.elements.get('holder-state').textContent,current ? /限定授权自动会签/ : /授权已撤销/);
  assert.equal(b.elements.get('download-audit').disabled,false);assert.equal(b.elements.get('download-holder').disabled,true);
  assert.ok(!b.elements.get('holder-state').textContent.includes('本人已签署'));assert.ok(b.inputs().every(input=>!input.checked));
  assert.ok(b.calls.every(call=>call.options.method==='GET'));assert.equal(b.elements.get('signature').value,'');
});
test('unverifiable delegated material remains manual with audit download unavailable',async()=>{
  const b=await browser({states:{[ID(1)]:status(ID(1),{current:true,canSign:true,signatures:[{role:'contributor'}],delegated:{recorded:true,current:false,state:'unverifiable',canAudit:false}})}});
  assert.match(b.elements.get('holder-state').textContent,/暂不可验证/);assert.equal(b.elements.get('download-audit').disabled,true);
  assert.equal(b.elements.get('sign').disabled,false);assert.ok(b.calls.every(call=>call.options.method==='GET'));
});

const tags=node=>(node.children??[]).map(child=>child.tagName);
const textOf=node=>node.textContent || (node.children??[]).map(textOf).join('');
test('English is the default outside Chinese browsers and keeps the original attestations',async()=>{
  const b=await browser({language:'en-GB',states:{[ID(1)]:status(ID(1),{current:true,canSign:true})}});
  assert.equal(b.elements.get('contributor-state').textContent,'Not signed yet');
  assert.equal(b.elements.get('sign').textContent,'Sign the agreement');
  assert.deepEqual(b.inputs().map(input=>input.name),['personal','electronic','read','identity','adult','own_rights','independent']);
});
test('switching language relabels without clearing ticks or typed fields',async()=>{
  const b=await browser({language:'en-GB',states:{[ID(1)]:status(ID(1),{current:true,canSign:true})}});
  for(const input of b.inputs())input.checked=true;b.elements.get('name').value='SYNTHETIC NAME';
  b.elements.get('lang').onclick();await b.settle();
  assert.ok(b.inputs().every(input=>input.checked));assert.equal(b.elements.get('name').value,'SYNTHETIC NAME');
  assert.equal(b.elements.get('contributor-state').textContent,'尚未签署');
  assert.equal(b.elements.get('sign').textContent,'由我本人签署（贡献者）');
  assert.ok(b.calls.every(call=>call.options.method==='GET'));
});
test('agreement Markdown becomes structured nodes and only https links are links',async()=>{
  const md='# Title\n\n**CLA_VERSION: 1.0**\n\n> Quoted note\n\n## Section\n\n| A | B |\n|---|---|\n| x | [safe](https://example.org) |\n\n- one\n- [unsafe](javascript:alert(1))\n\nPlain <b>text</b>.';
  const b=await browser({agreementContent:md});
  const doc=b.elements.get('agreement');
  assert.deepEqual(tags(doc),['h1','p','blockquote','h2','div','ul','p']);
  const anchors=[];const walk=node=>{if(node.tagName==='a')anchors.push(node);(node.children??[]).forEach(walk);};walk(doc);
  assert.deepEqual(anchors.map(a=>a.href),['https://example.org']);
  assert.match(textOf(doc.children[6]),/Plain <b>text<\/b>\./);
});
test('the rights holder sees the exact registered name before signing',async()=>{
  const holder={id:1,login:'synthetic-holder',role:'holder',holderName:'Synthetic Holder'};
  const b=await browser({pages:[{actor:holder,packets:[{id:ID(1)}],nextCursor:null}],states:{[ID(1)]:status(ID(1),{actor:holder,signatures:[{role:'contributor'}],canSign:true,current:true})}});
  assert.equal(b.elements.get('holder-hint').hidden,false);assert.match(b.elements.get('holder-hint').textContent,/Synthetic Holder/);
  assert.equal(b.elements.get('name').value,'');
});
test('a contributor never sees the holder name hint',async()=>{
  const b=await browser();assert.equal(b.elements.get('holder-hint').hidden,true);
});
const both=(extra={})=>({states:{[ID(1)]:status(ID(1),{current:true,completed:true,signatures:[{role:'contributor'},{role:'holder'}],...extra})}});
test('both signatures show the thank-you state; the PDF button is hidden and disabled while signedPdf is absent',async()=>{
  const b=await browser(both());
  assert.equal(b.elements.get('thanks').hidden,false);assert.equal(b.elements.get('hero').hidden,true);
  assert.equal(b.elements.get('personal-signature').hidden,true);
  assert.equal(b.elements.get('download-pdf').hidden,true);assert.equal(b.elements.get('download-pdf').disabled,true);
});
test('with signedPdf true the PDF button is visible and enabled once both have signed',async()=>{
  const b=await browser({...both(),signedPdf:true});
  assert.equal(b.elements.get('download-pdf').hidden,false);assert.equal(b.elements.get('download-pdf').disabled,false);
});
test('the address hint mentions the PDF copy only when signedPdf is available',async()=>{
  let b=await browser();assert.doesNotMatch(b.elements.get('t-address-hint').textContent,/PDF/);
  b=await browser({signedPdf:true});assert.match(b.elements.get('t-address-hint').textContent,/PDF/);
});
test('a completed record that is no longer current does not claim the contributor is all set',async()=>{
  const b=await browser(both({current:false}));
  assert.equal(b.elements.get('thanks').hidden,true);assert.equal(b.elements.get('hero').hidden,false);
  assert.equal(b.elements.get('eligibility').textContent,'这条记录已过期，或对应的是旧版本协议。');
});
test('a blocked completed record does not show the thank-you state',async()=>{
  const b=await browser(both({blocked:true}));
  assert.equal(b.elements.get('thanks').hidden,true);assert.equal(b.elements.get('hero').hidden,false);
});
test('the PDF download stays disabled until both parties have signed',async()=>{
  const b=await browser({states:{[ID(1)]:status(ID(1),{current:true,canSign:true,signatures:[{role:'contributor'}]})}});
  assert.equal(b.elements.get('download-pdf').disabled,true);
});
const contributorOnly=(extra={})=>({states:{[ID(1)]:status(ID(1),{current:true,signatures:[{role:'contributor',actor:actor.id,created:1793770000}],...extra})}});
test('a contributor who has signed sees that their part is done, never the signing form again',async()=>{
  const b=await browser({...contributorOnly(),signedPdf:true,pages:[{actor,holderName:'Synthetic Holder',packets:[{id:ID(1)}],nextCursor:null}]});
  assert.equal(b.elements.get('thanks').hidden,false);assert.equal(b.elements.get('hero').hidden,true);
  assert.equal(b.elements.get('personal-signature').hidden,true);assert.equal(b.elements.get('download-pdf').hidden,true);
  assert.equal(b.elements.get('thanks-title').textContent,'谢谢你，你这边已经完成。');
  assert.match(b.elements.get('thanks-lede').textContent,/Synthetic Holder 会来联署/);assert.match(b.elements.get('thanks-lede').textContent,/PDF/);
  assert.match(b.elements.get('thanks-eyebrow').textContent,/^你已签署 · /);assert.equal(b.elements.get('thanks-seal').className,'seal wait');
});
test('the waiting state names the rights holder generically when the session does not',async()=>{
  const b=await browser(contributorOnly());
  assert.match(b.elements.get('thanks-lede').textContent,/^Tavotto 的维护者 权利人 /);assert.doesNotMatch(b.elements.get('thanks-lede').textContent,/PDF/);
});
for(const [label,extra] of [['expired',{current:false}],['withdrawn',{blocked:true}]])test('a contributor-only record that is '+label+' does not claim the contributor is done',async()=>{
  const b=await browser(contributorOnly(extra));
  assert.equal(b.elements.get('thanks').hidden,true);assert.equal(b.elements.get('hero').hidden,false);
});
test('the rights holder still sees the countersigning form on a contributor-only record',async()=>{
  const holder={id:1,login:'synthetic-holder',role:'holder',holderName:'Synthetic Holder'};
  const b=await browser({...contributorOnly(),pages:[{actor:holder,holderName:'Synthetic Holder',packets:[{id:ID(1)}],nextCursor:null}]});
  assert.equal(b.elements.get('thanks').hidden,true);assert.equal(b.elements.get('personal-signature').hidden,false);
});
test('withdrawal is offered only when the server allows a hold and asks first',async()=>{
  let b=await browser({states:{[ID(1)]:status(ID(1),{canHold:false})}});assert.equal(b.elements.get('withdraw-open').hidden,true);
  b=await browser();assert.equal(b.elements.get('withdraw-open').hidden,false);assert.equal(b.elements.get('withdraw-panel').hidden,true);
  b.elements.get('withdraw-open').onclick();assert.equal(b.elements.get('withdraw-panel').hidden,false);
  b.elements.get('hold-confirm').checked=true;b.elements.get('withdraw-cancel').onclick();
  assert.equal(b.elements.get('hold-confirm').checked,false);assert.equal(b.elements.get('withdraw-panel').hidden,true);
});
test('switching language keeps the rendered agreement (and the reader position) untouched',async()=>{
  const b=await browser({language:'en-GB',agreementContent:'# Title\n\nBody text.'});
  const before=b.elements.get('agreement').children;
  b.elements.get('lang').onclick();await b.settle();
  assert.equal(b.elements.get('agreement').children,before);
  assert.equal(b.elements.get('contributor-state').textContent,'尚未签署');
});

test('the signature style is a choice of four, previews the typed name, and is sent with the evidence',async()=>{
  const b=await browser({states:{[ID(1)]:status(ID(1),{current:true,canSign:true,signatures:[]})}});
  assert.equal(b.elements.get('style-signature').className,'sty on');assert.equal(b.elements.get('sample-pen').textContent,'你的姓名');
  b.elements.get('name').value='Shang Moqi';b.elements.get('name').oninput();
  for(const s of ['signature','formal','pen','hand'])assert.equal(b.elements.get('sample-'+s).textContent,'Shang Moqi');
  await b.click('style-pen');assert.equal(b.elements.get('style-pen').className,'sty on');assert.equal(b.elements.get('style-signature').className,'sty');
  b.elements.get('signature').value='Shang Moqi';b.elements.get('address').value='SYNTHETIC ADDRESS';
  b.elements.get('sign-form').onsubmit({preventDefault(){}});for(let i=0;i<5;i++)await b.settle();
  const sign=b.calls.find(call=>call.url==='/sign');assert.ok(sign,'sign must be called');
  const evidence=JSON.parse(sign.options.body).evidence;assert.equal(evidence.signature_style,'pen');assert.equal(evidence.signature,'Shang Moqi');
});

test('when an unavailable record was open, the picker stays visible so the contributor can choose their one record',async()=>{
  const b=await browser({stored:ID(7),states:{[ID(1)]:status(),[ID(7)]:{error:'packet_unavailable'}}});
  assert.equal(b.elements.get('packets').value,'');assert.equal(b.elements.get('record-picker').hidden,false);
  assert.ok([...b.elements.get('packets').options].some(o=>o.value===ID(1)));
});

const holder={id:1,login:'synthetic-holder',role:'holder'},DAY0=1700000000;
const withdrawn=(extra={})=>status(ID(1),{blocked:true,released:false,canHold:false,canRelease:true,block:{packet:ID(1),heldAt:DAY0,releasedAt:null},...extra});
test('the rights holder can release a withdrawn record only after ticking the review box',async()=>{
  const b=await browser({pages:[{actor:holder,packets:[{id:ID(1)}],nextCursor:null}],states:{[ID(1)]:withdrawn({actor:holder})}});
  assert.equal(b.elements.get('release-panel').hidden,false);assert.equal(b.elements.get('release-form').hidden,false);
  assert.match(b.elements.get('release-note').textContent,/^已于 .*2023.* 撤回。和对方核对过之后，可以允许对方重新签署。$/);
  assert.equal(b.elements.get('release').textContent,'允许重新签署');assert.equal(b.elements.get('t-release-confirm').textContent,'我已和贡献者核对过');
  await b.click('release');assert.ok(b.calls.every(call=>call.url!=='/release'),'no request without the confirmation');assert.match(b.elements.get('message').textContent,/personal_confirmation_required/);await b.click('refresh');
  b.elements.get('release-confirm').checked=true;await b.click('release');
  const call=b.calls.find(x=>x.url==='/release');assert.deepEqual(JSON.parse(call.options.body),{packet:ID(1)});
  assert.equal(b.elements.get('release-form').hidden,true);assert.equal(b.elements.get('release-confirm').checked,false);
  assert.match(b.elements.get('release-note').textContent,/^已于 .* 解除，对方现在可以重新签一份。$/);
  assert.equal(b.elements.get('create').disabled,true);
});
test('the release panel reads in English and appears for nobody but the holder on a withdrawn record',async()=>{
  const h=await browser({language:'en-GB',pages:[{actor:holder,packets:[{id:ID(1)}],nextCursor:null}],states:{[ID(1)]:withdrawn({actor:holder})}});
  assert.match(h.elements.get('release-note').textContent,/^Withdrawn on .*2023.*\. After you have talked it through with them, you can let them sign again\.$/);
  assert.equal(h.elements.get('release').textContent,'Allow signing again');assert.equal(h.elements.get('t-release-confirm').textContent,'I have reviewed this with the contributor');
  assert.match(h.elements.get('t-withdraw-body').textContent,/until a maintainer has talked it through with you\.$/);
  const c=await browser({states:{[ID(1)]:withdrawn()}});assert.equal(c.elements.get('release-panel').hidden,true);assert.equal(c.elements.get('restart').hidden,true);
  const fine=await browser({pages:[{actor:holder,packets:[{id:ID(1)}],nextCursor:null}],states:{[ID(1)]:status(ID(1),{actor:holder})}});assert.equal(fine.elements.get('release-panel').hidden,true);
  const done=await browser({language:'en-GB',pages:[{actor:holder,packets:[{id:ID(1)}],nextCursor:null}],states:{[ID(1)]:withdrawn({actor:holder,released:true,canRelease:false,block:{packet:ID(1),heldAt:DAY0,releasedAt:DAY0+86400}})}});
  assert.match(done.elements.get('release-note').textContent,/^Released on .*2023.*\. They can now sign a new agreement\.$/);assert.equal(done.elements.get('release-form').hidden,true);
});
test('a contributor whose withdrawal was released sees Sign again; an unreleased or unknown hold does not allow it',async()=>{
  const b=await browser({states:{[ID(1)]:withdrawn({released:true,canRelease:false,block:{packet:ID(1),heldAt:DAY0,releasedAt:DAY0+1},completed:true,current:true,signatures:[{role:'contributor'},{role:'holder'}]})}});
  assert.equal(b.elements.get('restart').hidden,false);assert.equal(b.elements.get('sign-form').hidden,true);assert.equal(b.elements.get('create').disabled,false);
  assert.equal(b.elements.get('t-restart-hint').textContent,'维护者已和你核对过撤回的事。点下面重新签一次，需要重新阅读和确认。');
  assert.equal(b.elements.get('release-panel').hidden,true);
  await b.click('create');assert.equal(b.calls.filter(call=>call.url==='/packets').length,1);assert.equal(b.elements.get('packets').value,ID(99));
  assert.equal(b.elements.get('t-restart-hint').textContent.includes('维护者'),false);
  const en=await browser({language:'en-GB',states:{[ID(1)]:withdrawn({released:true,canRelease:false})}});
  assert.equal(en.elements.get('t-restart-hint').textContent,'The maintainer has reviewed your withdrawal with you. Sign again here; you will read and confirm everything afresh.');
  for(const extra of [{released:false},{released:undefined},{blocked:undefined,released:true}]){
    const x=await browser({states:{[ID(1)]:withdrawn(extra)}});assert.equal(x.elements.get('create').disabled,true);assert.equal(x.elements.get('restart').hidden,true);
  }
  const lapsed=await browser();assert.match(lapsed.elements.get('t-restart-hint').textContent,/7 天/);
});
test('on a withdrawn record the holder sees the release panel, not a countersigning form',async()=>{
  const holder={id:1,login:'synthetic-holder',role:'holder',holderName:'Synthetic Holder'};
  const b=await browser({pages:[{actor:holder,packets:[{id:ID(1)}],nextCursor:null}],states:{[ID(1)]:status(ID(1),{actor:holder,current:false,blocked:true,released:false,canRelease:true,completed:true,
    block:{packet:ID(1),heldAt:1793700000},signatures:[{role:'contributor'},{role:'holder'}]})}});
  assert.equal(b.elements.get('personal-signature').hidden,true);assert.equal(b.elements.get('release-panel').hidden,false);
  assert.equal(b.elements.get('eligibility').textContent,'贡献者撤回了这份签署。');
});
