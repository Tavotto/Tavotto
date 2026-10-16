export function client():void {
  const $=<T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as T;
  const text=(id:string,value:unknown)=>{$(id).textContent=String(value);};
  let packet=new URL(location.href).searchParams.get('packet') ?? sessionStorage.getItem('cla.packet') ?? '',state:any=null,busy=false,uncertain=false,cap:any={};
  let nextCursor:string|null=null,hasPackets=false,lastActor:number|undefined;
  const csrf=()=>document.cookie.split(';').map(x=>x.trim()).find(x=>x.startsWith('__Host-cla-csrf='))?.split('=')[1] ?? '';
  async function api(path:string,body?:unknown){
    const r=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',
      headers:body===undefined?{}:{'content-type':'application/json','x-csrf-token':csrf()},body:body===undefined?undefined:JSON.stringify(body)});
    const data=await r.json();if(!r.ok)throw new Error(data.error ?? 'request_unavailable');return data;
  }
  function message(value:string,error=false){text('message',value);$('message').className=error?'notice error':'notice';}
  const canCreate=()=>cap.signingEnabled && cap.actor?.role==='contributor' && (!state?!hasPackets:
    state.actor?.id===cap.actor.id && state.packet?.subject===cap.actor.id && state.completed===false && state.blocked===false &&
    Number.isSafeInteger(state.now) && Number.isSafeInteger(state.packet.valid_until) && state.packet.valid_until<=state.now);
  function controls(){
    ($('sign') as HTMLButtonElement).disabled=busy || uncertain || !state?.canSign;
    ($('hold') as HTMLButtonElement).disabled=busy || uncertain || !state?.canHold;
    ($('create') as HTMLButtonElement).disabled=busy || uncertain || !canCreate();
    ($('packets-more') as HTMLButtonElement).disabled=busy || uncertain || !nextCursor;
    ($('login') as HTMLButtonElement).disabled=busy || !cap.oauthEnabled;
    ($('refresh') as HTMLButtonElement).disabled=busy;
  }
  async function agreement(path:string){const d=await api(path);text('version',d.version);text('digest',d.digest);text('agreement',d.content);return d;}
  function clearConsent(){($('signature') as HTMLInputElement).value='';
    document.querySelectorAll<HTMLInputElement>('#attestations input').forEach(x=>{x.checked=false;});}
  function clearPersonalFields(){
    $<HTMLInputElement>('name').value='';$<HTMLTextAreaElement>('address').value='';$<HTMLInputElement>('hold-confirm').checked=false;
  }
  function discardPacket(){
    packet='';state=null;clearConsent();sessionStorage.removeItem('cla.packet');history.replaceState(null,'','/');
    clearPersonalFields();
    $('attestations').replaceChildren();text('contributor-state','尚未签署');text('holder-state','等待本人审阅与签署');text('record-time','');
    for(const role of ['contributor','holder'])$<HTMLButtonElement>('download-'+role).disabled=true;
  }
  const labels:Record<string,string>={personal:'我本人作出此决定；没有由他人代签。',electronic:'我同意用电子签名作出本次签署。',
    read:'我已经阅读上方完整协议，并同意这个版本和哈希对应的文本。',identity:'该 GitHub 数字身份属于我；姓名和地址是我本人填写的。',
    adult:'我已满十八岁。',own_rights:'我拥有本次贡献所需的权利；不属于他人或雇主所有。',independent:'我以个人身份签署；若涉及雇主或公司权利，我会先寻求人工审查。',
    reviewed:'我作为权利人已独立审阅此协议及贡献者的签署证据，并愿意签署。'};
  function packetPage(own:any,reset=false){
    const select=$<HTMLSelectElement>('packets');
    if(reset)select.replaceChildren(new Option('选择签署记录',''));
    for(const p of own.packets)if(![...select.options].some(option=>option.value===p.id))select.add(new Option(p.id,p.id));
    nextCursor=own.nextCursor ?? null;hasPackets=select.options.length>1;
    // Only a freshly authorized historical selection may be pinned beyond this page.
    if(packet && state?.packet?.id===packet && state.actor?.id===own.actor.id &&
      ![...select.options].some(option=>option.value===packet))select.add(new Option(packet,packet));
    select.value=packet;
    text('packets-page',nextCursor?'还有更早的记录；可继续加载。':'已载入全部可访问记录。');
  }
  async function refresh(){
    let own:any;const previousActor=lastActor;
    cap=await api('/capabilities');text('mode',cap.signingEnabled?'签署入口已开放':'签署入口已关闭');
    try{own=await api('/session');if(previousActor!==undefined && previousActor!==own.actor.id){clearConsent();clearPersonalFields();}
      lastActor=own.actor.id;cap.actor=own.actor;state=null;text('actor',`${own.actor.login} · GitHub ID ${own.actor.id} · ${own.actor.role==='holder'?'权利人':'贡献者'}`);
      if(packet && !/^[A-Za-z0-9_-]{43}$/.test(packet))throw new Error('packet_unavailable');
      if(!packet && own.packets.length===1 && !own.nextCursor)packet=own.packets[0].id;
      packetPage(own,true);
      if(packet){state=await api('/status?packet='+encodeURIComponent(packet));packetPage(own,true);sessionStorage.setItem('cla.packet',packet);
        history.replaceState(null,'','/?packet='+encodeURIComponent(packet));
        await agreement('/agreement?packet='+encodeURIComponent(packet));
        text('contributor-state',state.signatures.some((r:any)=>r.role==='contributor')?'已签署':'尚未签署');
        text('holder-state',state.signatures.some((r:any)=>r.role==='holder')?'已签署':'等待本人审阅与签署');
        text('eligibility',state.blocked?'本地资格已停止，检查撤销待后台核对':!state.current?'此记录已过期或版本不再适用':
          state.signatures.length===2?'双方签署记录齐全；检查与合并状态另行核对':'等待双方各自签署');
        text('record-time',state.completed ? '双方已签署；待签截止不限制已完成记录资格。' : '待签完成截止 '+new Date(state.packet.valid_until*1000).toLocaleString());
        const box=$('attestations'),legend=document.createElement('legend');legend.textContent='逐项确认本人的意愿';box.replaceChildren(legend);
        const keys=['personal','electronic','read','identity',...(state.actor.role==='holder'?['reviewed']:['adult','own_rights','independent'])];
        for(const key of keys){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.name=key;input.required=true;
          label.append(input,document.createTextNode(labels[key]!));box.append(label);}
        text('sign',state.actor.role==='holder'?'由我本人签署（权利人）':'由我本人签署（贡献者）');
        ($('download-contributor') as HTMLButtonElement).disabled=!state.signatures.some((r:any)=>r.role==='contributor');
        ($('download-holder') as HTMLButtonElement).disabled=!state.signatures.some((r:any)=>r.role==='holder');
      }else{state=null;await agreement('/agreement');text('eligibility',hasPackets?'请选择已有签署记录。':'创建本人记录后才能签署。');}
    }catch(error){state=null;if((error as Error).message==='packet_unavailable' && own){
        discardPacket();packetPage(own,true);await agreement('/agreement');text('eligibility',hasPackets?'之前选择的记录不可访问；请选择当前账号的签署记录。':'之前选择的记录不可访问；可创建当前账号的签署记录。');
      }else if((error as Error).message==='session_rejected'){text('actor','尚未登录 · 先确认您的 GitHub 身份');await agreement('/agreement');}
      else throw error;}
    uncertain=false;controls();
  }
  async function action(fn:()=>Promise<void>){if(busy)return;busy=true;controls();try{await fn();}
    catch(error){uncertain=true;clearConsent();message('操作没有确认完成（'+(error as Error).message+'）。请点击“刷新状态”先核对；确认未签署后，重新阅读、勾选并手动提交。不会自动重发。',true);}
    finally{busy=false;controls();}}
  $('login').onclick=()=>void action(async()=>{sessionStorage.setItem('cla.packet',packet);const r=await api('/oauth/start',{});location.assign(r.authorizationURL);});
  $('create').onclick=()=>void action(async()=>{if(!canCreate())throw new Error('packet_creation_unavailable');
    packet=(await api('/packets',{kind:'individual'})).id;clearConsent();await refresh();message('记录已创建。请完整阅读协议后，亲自决定是否签署。');});
  $('refresh').onclick=()=>void action(async()=>{await refresh();message('状态已重新读取。刷新不会签署或重复提交。');});
  $('packets-more').onclick=()=>void action(async()=>{if(!nextCursor)return;
    const own=await api('/session?cursor='+encodeURIComponent(nextCursor));
    if(own.actor.id!==cap.actor?.id)throw new Error('session_changed');packetPage(own);message('已加载更多签署记录。请选择要查看的记录。');});
  $<HTMLSelectElement>('packets').onchange=()=>void action(async()=>{packet=$<HTMLSelectElement>('packets').value;clearConsent();await refresh();});
  $<HTMLFormElement>('sign-form').onsubmit=event=>{event.preventDefault();void action(async()=>{
    if(!state?.canSign)throw new Error('signing_unavailable');
    const role=state.actor.role,input={name:$<HTMLInputElement>('name').value,address:$<HTMLTextAreaElement>('address').value,
      signature:$<HTMLInputElement>('signature').value,attestations:Object.fromEntries([...document.querySelectorAll<HTMLInputElement>('#attestations input')].map(x=>[x.name,x.checked]))};
    const intent=(await api('/intents',{packet,role})).intent;
    await api('/sign',{packet,intent,role,evidence:input});clearConsent();$<HTMLFormElement>('sign-form').reset();await refresh();message('本人的签署已保存。另一方仍须独立审阅并自行签署。');
  });};
  $('hold').onclick=()=>void action(async()=>{if(!$<HTMLInputElement>('hold-confirm').checked)throw new Error('personal_confirmation_required');
    const intent=(await api('/intents',{packet,role:'hold'})).intent;await api('/hold',{packet,intent});clearConsent();await refresh();
    message('本地资格已停止并记录核对任务。这不撤销协议中的法律权利，也不保证 GitHub 绿灯已消失；远端失败须读回确认。');});
  for(const role of ['contributor','holder'])$('download-'+role).onclick=()=>void action(async()=>{
    const evidence=await api('/evidence?packet='+encodeURIComponent(packet)+'&role='+role),document=await api('/agreement?packet='+encodeURIComponent(packet));
    const link=window.document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify({agreement:document,evidence},null,2)],{type:'application/json'}));
    link.href=url;link.download='cla-'+packet+'-'+role+'.json';link.click();URL.revokeObjectURL(url);message('证据已下载到您的设备；请妥善保存个人资料。');});
  void refresh().catch(error=>{controls();message('无法读取当前协议或身份（'+(error as Error).message+'）。请重新登录或稍后手动刷新；签署保持不可用。',true);});
}
