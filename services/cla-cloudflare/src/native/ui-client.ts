export function client():void {
  const $=<T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as T;
  const text=(id:string,value:unknown)=>{const el=$(id);if(el)el.textContent=String(value);};
  const show=(id:string,visible:boolean)=>{const el=$(id);if(el)el.hidden=!visible;};
  let packet=new URL(location.href).searchParams.get('packet') ?? sessionStorage.getItem('cla.packet') ?? '',state:any=null,busy=false,uncertain=false,cap:any={};
  let nextCursor:string|null=null,hasPackets=false,lastActor:number|undefined,signedIn=false,holderName='',counterparty='',autoCreated=false,readDone=false,lastMessage:[string,unknown[],boolean]|null=null;
  // Wording version 2 (CONSENT_UI_VERSION in service.ts). The Chinese attestation texts are
  // version 1's, unchanged; the English ones are new. Attestation keys never change.
  const WORDS:Record<'en'|'zh',Record<string,string>>={en:{
    product:'Contributor License Agreement',lang:'中文',checking:'Checking who you are…',signedOut:'Not signed in',login:'Sign in with GitHub',relogin:'Switch account',refresh:'Refresh',
    actor:'{0} · GitHub ID {1}',roleHolder:'rights holder',roleContributor:'contributor',
    eyebrow:'Thank you for contributing',title:'Your work makes Tavotto better for every researcher who uses it.',
    lede:'Before we can merge your first pull request, we ask you to sign this agreement once. You keep the copyright in what you write; you give us permission to use and distribute it. It covers every contribution you make afterwards.',
    holderEyebrow:'Rights holder',holderTitle:'A contributor signed and is waiting for you.',holderLede:'Review what they signed, then countersign as the rights holder. They can download their signed PDF as soon as you do.',holderLedeNoPdf:'Review what they signed, then countersign as the rights holder.',
    holderIdleTitle:'Countersign contributor agreements here.',holderIdleLede:'Choose a signing record to review it.',
    step1:'Sign in',step2:'Read & sign',step3:'Countersignature',step4:'Your copy',
    modeOpen:'',modeClosed:'Signing is paused right now. You can still read the agreement.',
    fingerprint:'Fingerprint',loadingShort:'Loading',signInFirst:'Sign in with GitHub first. Nothing is pre-filled or ticked for you.',version:'{0} CLA · version {1}',kindIndividual:'Individual',loading:'Loading the agreement…',
    readPct:'Read {0}%',readDone:'You have reached the end',readHint:'Scroll to the end to confirm you have read it',
    signTitle:'Sign as yourself',signHint:'Only you can sign for yourself. Nothing is pre-filled or ticked for you.',
    holderHint:'Your name must read exactly “{0}”, as registered for this service.',
    name:'Full legal name',address:'Postal address',addressHint:'Kept privately in the signed record and in your PDF copy. Never shown on GitHub.',addressHintNoPdf:'Kept privately in the signed record. Never shown on GitHub.',
    signature:'Type your full name to sign',signatureHint:'Must match the name above exactly.',
    style:'Signature style',styleHint:'This is how your signature looks on the signed PDF. Typing your name is what signs; the style only changes how it looks.',
    style_signature:'Signature',style_formal:'Formal script',style_pen:'Pen',style_hand:'Handwriting',stylePlaceholder:'Your name',legend:'Confirm each statement yourself',
    signContributor:'Sign the agreement',signHolder:'Countersign as rights holder',
    recordTitle:'Signing status',packets:'Record',choose:'Choose a signing record',noRecord:'No record yet',create:'Sign again',more:'Load earlier records',
    restartHint:'Your last signing was not finished within 7 days, so it has lapsed. Start again here; you will read and confirm everything afresh.',
    pageMore:'Earlier records are available.',pageAll:'All your records are shown.',
    contributor:'Contributor',holder:'Rights holder',notSigned:'Not signed yet',signed:'Signed',holderSigned:'Countersigned',holderWaiting:'Waiting for countersignature',
    delegatedCurrent:'Countersigned automatically under a limited authorisation',delegatedHistory:'Automatic countersignature on record · {0}',
    d_disabled:'switched off',d_expired:'authorisation expired',d_revoked:'authorisation revoked',d_suspended:'authorisation suspended',d_superseded:'needs countersigning again',d_blocked:'eligibility stopped',d_unverifiable:'material cannot be verified right now',d_not_started:'authorisation not yet in force',d_other:'needs manual review',
    eBlocked:'This record has been withdrawn. A maintainer will review it with you.',eBlockedHolder:'The contributor withdrew this signature.',eExpired:'This record has expired or applies to an older version.',
    eComplete:'Both parties have signed. Pull requests are checked separately.',eDelegated:'Signed and countersigned under a limited authorisation. Each pull request is still checked.',
    eWaiting:'Waiting for the rights holder to countersign.',eChoose:'Choose one of your signing records.',eCreate:'Getting your signing form ready…',
    eGone:'The record you had open is not available to this account. Choose one of your own records.',eGoneCreate:'The record you had open is not available to this account. You can start your own.',
    tDelegated:'Automatic authorisation valid until {0}',tDeadline:'Please finish signing by {0}',
    thanksEyebrow:'Signed',thanksTitle:'Thank you. You’re all set.',thanksLede:'Both signatures are on the record, and every future contribution is already covered. Push any new commit to your pull request and the CLA check will pass within about 15 minutes. We’re glad to have you here.',
    waitEyebrow:'You signed · {0}',waitTitle:'Thank you. Your part is done.',waitLede:'{0}, who maintains Tavotto, will countersign, usually within a day or two. You don’t need to wait on this page or do anything else: once it is countersigned, the CLA check on your pull request turns green by itself. Come back here afterwards to download your signed PDF.',waitLedeNoPdf:'{0}, who maintains Tavotto, will countersign, usually within a day or two. You don’t need to wait on this page or do anything else: once it is countersigned, the CLA check on your pull request turns green by itself.',waitHolder:'The rights holder',
    thanksHolderTitle:'Countersigned. Thank you.',thanksHolderLede:'Both signatures are on the record. The contributor can now download their signed PDF, and their next push will pass the CLA check.',thanksHolderLedeNoPdf:'Both signatures are on the record. The contributor’s next push will pass the CLA check.',
    pdf:'Download signed PDF',records:'Technical records',recordsHint:'Machine-readable evidence for this record: the exact agreement text, its fingerprint and the chosen party’s signature. Only the two parties can download it.',
    dlContributor:'Contributor evidence',dlHolder:'Rights holder evidence',dlAudit:'Delegation audit',
    withdrawOpen:'Withdraw my signature…',withdrawTitle:'Withdraw your signature?',withdrawBody:'Your pull requests will stop passing the CLA check. You will not be able to sign again until a maintainer has talked it through with you.',
    withdrawNote:'This does not undo the licence already granted for contributions you made before today.',withdrawConfirm:'I understand, and I want to withdraw.',withdrawCancel:'Keep my signature',withdraw:'Withdraw',
    releaseNote:'Withdrawn on {0}. After you have talked it through with them, you can let them sign again.',releaseConfirm:'I have reviewed this with the contributor',release:'Allow signing again',
    releasedNote:'Released on {0}. They can now sign a new agreement.',restartReleasedHint:'The maintainer has reviewed your withdrawal with you. Sign again here; you will read and confirm everything afresh.',mReleased:'They can sign again now.',
    foot:'Questions about the agreement? Write to contact@tavotto.com. A real person reads every message.',foot2:'If a step seems stuck, choose Refresh first. Nothing is ever signed or sent again automatically.',
    mFailed:'That did not go through ({0}). Choose Refresh to check what was saved; if you have not signed yet, read, tick and submit again yourself. Nothing is resent automatically.',
    mLoadFailed:'The agreement or your identity could not be loaded ({0}). Sign in again or refresh later; signing stays unavailable until then.',
    mCreated:'Started again. Read the agreement, then sign below.',mRefreshed:'Status refreshed. Refreshing never signs or submits anything.',mMore:'Earlier records loaded.',
    mSignedDelegated:'Your signature is saved, and the limited authorisation countersigned it. Pull requests are still checked.',mSignedManual:'Your signature is saved. The automatic countersignature is not confirmed yet; the rights holder will review it.',
    mSigned:'Your signature is saved. Thank you. The rights holder will countersign separately.',mHeld:'Your signature is withdrawn and queued for review. Pull requests that already passed are re-checked in the background.',
    mEvidence:'Downloaded to your device. It contains personal details, so keep it safe.',mAudit:'Delegation audit downloaded. A historical record is not a verdict on any current pull request.',mPdf:'Your signed PDF is downloaded. Keep it with your records.',
    a_personal:'I am making this decision myself; no one is signing for me.',a_electronic:'I agree to sign electronically.',a_read:'I have read the whole agreement above, and I agree to this exact version and fingerprint.',
    a_identity:'This GitHub account is mine, and I entered my own name and address.',a_adult:'I am 18 or older.',a_own_rights:'I hold the rights in my contributions; they do not belong to someone else or an employer.',
    a_independent:'I am signing as an individual. If an employer or company has rights, I will ask for review first.',a_reviewed:'As the rights holder, I have independently reviewed this agreement and the contributor’s signature, and I want to sign.'},
  zh:{
    product:'贡献者许可协议',lang:'English',checking:'正在确认身份…',signedOut:'尚未登录',login:'用 GitHub 登录',relogin:'切换账号',refresh:'刷新',
    actor:'{0} · GitHub ID {1}',roleHolder:'权利人',roleContributor:'贡献者',
    eyebrow:'感谢你的贡献',title:'你的每一份贡献，都让 Tavotto 对每一位科研工作者更好用。',
    lede:'合并你的第一个 Pull Request 之前，请签一次这份协议。你写的内容著作权仍归你；你授权我们使用和分发它。签一次，之后的所有贡献都适用。',
    holderEyebrow:'权利人',holderTitle:'有一位贡献者已经签好，正在等你联署。',holderLede:'先看看对方签了什么，再以权利人身份联署。你签完后，对方马上就能下载签署好的 PDF。',holderLedeNoPdf:'先看看对方签了什么，再以权利人身份联署。',
    holderIdleTitle:'在这里联署贡献者协议。',holderIdleLede:'选择一条签署记录开始审阅。',
    step1:'登录',step2:'阅读并签署',step3:'权利人联署',step4:'你的副本',
    modeOpen:'',modeClosed:'签署暂时关闭，协议仍可阅读。',
    fingerprint:'指纹',loadingShort:'读取中',signInFirst:'请先用 GitHub 登录。任何内容都不会预先填好或替你勾选。',version:'{0} CLA · 版本 {1}',kindIndividual:'个人',loading:'正在读取协议全文…',
    readPct:'已读 {0}%',readDone:'已读到结尾',readHint:'读到结尾后才能确认「已阅读」',
    signTitle:'由你本人签署',signHint:'只有你本人能签。任何内容都不会预先填好或替你勾选。',
    holderHint:'姓名须与本服务登记的完全一致：「{0}」。',
    name:'本人的完整姓名',address:'本人地址',addressHint:'只保存在签署记录和你的 PDF 副本里，不会出现在 GitHub 上。',addressHintNoPdf:'只保存在签署记录里，不会出现在 GitHub 上。',
    signature:'键入完整姓名作为电子签名',signatureHint:'必须与上方姓名完全一致。',
    style:'签名样式',styleHint:'签署 PDF 上的签名按这个样式显示。真正签署的是你键入的姓名，样式只影响外观。',
    style_signature:'签名体',style_formal:'正式花体',style_pen:'钢笔',style_hand:'手写',stylePlaceholder:'你的姓名',legend:'逐项确认本人的意愿',
    signContributor:'由我本人签署（贡献者）',signHolder:'由我本人签署（权利人）',
    recordTitle:'签署进度',packets:'签署记录',choose:'选择签署记录',noRecord:'尚无记录',create:'重新签署',more:'加载更早的记录',
    restartHint:'上一次签署超过 7 天没有完成，已经失效。点下面重新签一次，需要重新阅读和确认。',
    pageMore:'还有更早的记录，可继续加载。',pageAll:'已显示你的全部记录。',
    contributor:'贡献者',holder:'权利人',notSigned:'尚未签署',signed:'已签署',holderSigned:'本人已签署',holderWaiting:'等待权利人联署',
    delegatedCurrent:'已按限定授权自动会签',delegatedHistory:'自动会签历史记录 · {0}',
    d_disabled:'入口已关闭',d_expired:'授权已到期',d_revoked:'授权已撤销',d_suspended:'授权已暂停',d_superseded:'需重新会签',d_blocked:'资格已停止',d_unverifiable:'材料暂不可验证',d_not_started:'授权尚未生效',d_other:'需人工核对',
    eBlocked:'这条记录已撤回，维护者会和你一起核对。',eBlockedHolder:'贡献者撤回了这份签署。',eExpired:'这条记录已过期，或对应的是旧版本协议。',
    eComplete:'双方都已签署。每个 Pull Request 另行检查。',eDelegated:'已签署，并已按限定授权会签。每个 Pull Request 仍会另行检查。',
    eWaiting:'等待权利人联署。',eChoose:'请选择你的一条签署记录。',eCreate:'正在准备签署表单…',
    eGone:'之前打开的记录不属于当前账号。请选择你自己的记录。',eGoneCreate:'之前打开的记录不属于当前账号。你可以新建自己的记录。',
    tDelegated:'自动授权有效至 {0}',tDeadline:'请在 {0} 前完成签署',
    thanksEyebrow:'已签署',thanksTitle:'谢谢你，一切就绪。',thanksLede:'双方签名都已记录，你之后的所有贡献也都已覆盖。往你的 Pull Request 推一个新提交，大约 15 分钟内 CLA 检查就会通过。很高兴你加入。',
    waitEyebrow:'你已签署 · {0}',waitTitle:'谢谢你，你这边已经完成。',waitLede:'Tavotto 的维护者 {0} 会来联署，通常一两天内。你不用守在这个页面，也不用再做什么：联署完成后，你 Pull Request 上的 CLA 检查会自动变绿。之后回到这里就能下载签署好的 PDF。',waitLedeNoPdf:'Tavotto 的维护者 {0} 会来联署，通常一两天内。你不用守在这个页面，也不用再做什么：联署完成后，你 Pull Request 上的 CLA 检查会自动变绿。',waitHolder:'权利人',
    thanksHolderTitle:'已联署，谢谢。',thanksHolderLede:'双方签名都已记录。贡献者现在可以下载签署好的 PDF，下一次推送就会通过 CLA 检查。',thanksHolderLedeNoPdf:'双方签名都已记录。贡献者的下一次推送就会通过 CLA 检查。',
    pdf:'下载签署 PDF',records:'技术记录',recordsHint:'这条记录的机器可读证据：精确的协议全文、指纹和所选一方的签署。只有双方可以下载。',
    dlContributor:'贡献者证据',dlHolder:'权利人证据',dlAudit:'限定授权审计',
    withdrawOpen:'撤回我的签署…',withdrawTitle:'确定撤回签署吗？',withdrawBody:'撤回后，你的 Pull Request 将不再通过 CLA 检查。撤回后，需要维护者和你核对过，你才能重新签署。',
    withdrawNote:'撤回不会收回你此前贡献已经授予的许可。',withdrawConfirm:'我明白后果，确定撤回。',withdrawCancel:'保留签署',withdraw:'撤回',
    releaseNote:'已于 {0} 撤回。和对方核对过之后，可以允许对方重新签署。',releaseConfirm:'我已和贡献者核对过',release:'允许重新签署',
    releasedNote:'已于 {0} 解除，对方现在可以重新签一份。',restartReleasedHint:'维护者已和你核对过撤回的事。点下面重新签一次，需要重新阅读和确认。',mReleased:'对方现在可以重新签署了。',
    foot:'对协议有疑问？请写信到 contact@tavotto.com，每一封都有人亲自阅读。',foot2:'如果某一步像是卡住了，先点「刷新」。系统从不自动重签或重复提交。',
    mFailed:'操作没有确认完成（{0}）。请先点「刷新」核对已保存的内容；确认尚未签署后，再自己重新阅读、勾选并提交。系统不会自动重发。',
    mLoadFailed:'无法读取协议或你的身份（{0}）。请重新登录或稍后刷新；在此之前签署保持不可用。',
    mCreated:'已重新开始。读完协议后在下方签署。',mRefreshed:'状态已刷新。刷新不会签署或提交任何内容。',mMore:'已加载更早的记录。',
    mSignedDelegated:'你的签署已保存，并已按限定授权会签。每个 Pull Request 仍会另行检查。',mSignedManual:'你的签署已保存。自动会签尚未确认，权利人会另行审阅。',
    mSigned:'你的签署已保存，谢谢。权利人会另行联署。',mHeld:'签署已撤回，并已登记待核对。已通过的 Pull Request 会在后台重新检查。',
    mEvidence:'已下载到你的设备。其中含有个人信息，请妥善保存。',mAudit:'已下载限定授权审计材料。历史记录不代表任何当前 Pull Request 的结论。',mPdf:'签署 PDF 已下载，请与你的记录一起保存。',
    a_personal:'我本人作出此决定；没有由他人代签。',a_electronic:'我同意用电子签名作出本次签署。',a_read:'我已经阅读上方完整协议，并同意这个版本和哈希对应的文本。',
    a_identity:'该 GitHub 数字身份属于我；姓名和地址是我本人填写的。',a_adult:'我已满十八岁。',a_own_rights:'我拥有本次贡献所需的权利；不属于他人或雇主所有。',
    a_independent:'我以个人身份签署；若涉及雇主或公司权利，我会先寻求人工审查。',a_reviewed:'我作为权利人已独立审阅此协议及贡献者的签署证据，并愿意签署。'}};
  const storedLang=():string=>{try{return typeof localStorage==='undefined'?'':localStorage.getItem('cla.lang') ?? '';}catch{return '';}};
  let lang:'en'|'zh'=storedLang()==='zh'||(!storedLang() && typeof navigator!=='undefined' && /^zh\b/i.test(navigator.language ?? ''))?'zh':'en';
  const t=(key:string,...args:unknown[])=>(WORDS[lang][key] ?? WORDS.en[key] ?? key).replace(/\{(\d)\}/g,(_,i)=>String(args[Number(i)] ?? ''));
  const STATIC:Record<string,string>={'t-product':'product',lang:'lang',refresh:'refresh','step-1':'step1','step-2':'step2','step-3':'step3','step-4':'step4',
    't-fingerprint':'fingerprint','t-sign-title':'signTitle','t-sign-hint':'signHint','t-name':'name','t-address':'address','t-address-hint':'addressHint',
    't-signature':'signature','t-signature-hint':'signatureHint','t-style':'style','t-style-hint':'styleHint',
    't-style-signature':'style_signature','t-style-formal':'style_formal','t-style-pen':'style_pen','t-style-hand':'style_hand','t-record-title':'recordTitle','t-restart-hint':'restartHint','t-packets':'packets',create:'create','packets-more':'more',
    't-contributor':'contributor','t-holder':'holder','download-pdf':'pdf','t-records':'records','t-records-hint':'recordsHint','download-contributor':'dlContributor',
    'download-holder':'dlHolder','download-audit':'dlAudit','withdraw-open':'withdrawOpen','t-withdraw-title':'withdrawTitle','t-withdraw-body':'withdrawBody',
    't-withdraw-note':'withdrawNote','t-withdraw-confirm':'withdrawConfirm','withdraw-cancel':'withdrawCancel',hold:'withdraw',release:'release','t-release-confirm':'releaseConfirm','t-foot':'foot','t-foot-2':'foot2'};
  let consentLabels:Array<{input:HTMLInputElement;words:HTMLElement;key:string}>=[],legendEl:HTMLElement|null=null,docInfo:any=null;
  const csrf=()=>document.cookie.split(';').map(x=>x.trim()).find(x=>x.startsWith('__Host-cla-csrf='))?.split('=')[1] ?? '';
  async function api(path:string,body?:unknown){
    const r=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',
      headers:body===undefined?{}:{'content-type':'application/json','x-csrf-token':csrf()},body:body===undefined?undefined:JSON.stringify(body)});
    const data=await r.json();if(!r.ok)throw new Error(data.error ?? 'request_unavailable');return data;
  }
  function message(key:string,args:unknown[]=[],error=false){lastMessage=[key,args,error];text('message',t(key,...args));$('message').className=error?'notice error':'notice';}
  const signedBy=(role:string)=>!!state?.signatures?.some((row:any)=>row.role===role);
  const hasPdf=()=>cap?.signedPdf===true;
  const bothSigned=()=>signedBy('contributor') && signedBy('holder');
  const canCreate=()=>cap.signingEnabled && cap.actor?.role==='contributor' && (!state?!hasPackets:
    state.actor?.id===cap.actor.id && state.packet?.subject===cap.actor.id && (state.blocked===true && state.released===true ||
    state.completed===false && state.blocked===false && Number.isSafeInteger(state.now) && Number.isSafeInteger(state.packet.valid_until) && state.packet.valid_until<=state.now));
  function controls(){
    ($('sign') as HTMLButtonElement).disabled=busy || uncertain || !state?.canSign;
    ($('hold') as HTMLButtonElement).disabled=busy || uncertain || !state?.canHold;
    ($('release') as HTMLButtonElement).disabled=busy || uncertain || !state?.canRelease;
    ($('create') as HTMLButtonElement).disabled=busy || uncertain || !canCreate();
    ($('packets-more') as HTMLButtonElement).disabled=busy || uncertain || !nextCursor;
    ($('login') as HTMLButtonElement).disabled=busy || !cap.oauthEnabled;
    ($('refresh') as HTMLButtonElement).disabled=busy;
    ($('download-pdf') as HTMLButtonElement).disabled=busy || uncertain || !hasPdf() || !bothSigned();
    for(const role of ['contributor','holder','audit'])$<HTMLButtonElement>('download-'+role).disabled=busy || uncertain || !state ||
      (role==='audit'?!state.delegated?.canAudit:!state.signatures.some((row:any)=>row.role===role));
    for(const {input,key} of consentLabels)if(key==='read')input.disabled=!readDone;
  }
  // The agreement is Markdown. Build DOM nodes only (never innerHTML); links open only for https.
  function inline(parent:HTMLElement,source:string){
    const pattern=/(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(\[[^\]]+\]\([^)\s]+\))|(<https:\/\/[^>\s]+>)/g;let at=0,m:RegExpExecArray|null;
    while((m=pattern.exec(source))){
      if(m.index>at)parent.append(document.createTextNode(source.slice(at,m.index)));
      const token=m[0];let node:HTMLElement;
      if(m[1]){node=document.createElement('code');node.textContent=token.slice(1,-1);}
      else if(m[2]){node=document.createElement('strong');inline(node,token.slice(2,-2));}
      else if(m[3]){node=document.createElement('em');inline(node,token.slice(1,-1));}
      else{const label=m[4]?token.slice(1,token.indexOf('](')):token.slice(1,-1),href=m[4]?token.slice(token.indexOf('](')+2,-1):label;
        if(/^https:\/\//.test(href)){node=document.createElement('a');(node as HTMLAnchorElement).href=href;(node as HTMLAnchorElement).rel='noopener noreferrer';(node as HTMLAnchorElement).target='_blank';}
        else node=document.createElement('span');
        inline(node,label);}
      parent.append(node);at=m.index+token.length;
    }
    if(at<source.length)parent.append(document.createTextNode(source.slice(at)));
  }
  function markdown(target:HTMLElement,source:string){
    const out:HTMLElement[]=[],lines=String(source).replace(/\r\n?/g,'\n').split('\n');let i=0;
    const block=(tag:string,content:string)=>{const el=document.createElement(tag);inline(el,content);out.push(el);};
    const cells=(row:string)=>row.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(cell=>cell.trim());
    while(i<lines.length){
      const line=lines[i]!;
      if(!line.trim()){i++;continue;}
      if(/^```/.test(line)){const pre=document.createElement('pre'),code=document.createElement('code'),body:string[]=[];i++;
        while(i<lines.length && !/^```/.test(lines[i]!))body.push(lines[i++]!);i++;code.textContent=body.join('\n');pre.append(code);out.push(pre);continue;}
      const heading=/^(#{1,4})\s+(.*)$/.exec(line);
      if(heading){block('h'+heading[1]!.length,heading[2]!.replace(/\s+#+\s*$/,''));i++;continue;}
      if(/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)){out.push(document.createElement('hr'));i++;continue;}
      if(/^\s*>/.test(line)){const quote:string[]=[];while(i<lines.length && /^\s*>/.test(lines[i]!))quote.push(lines[i++]!.replace(/^\s*>\s?/,''));
        const el=document.createElement('blockquote');inline(el,quote.join(' '));out.push(el);continue;}
      if(/^\s*\|/.test(line) && i+1<lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i+1]!)){
        const wrap=document.createElement('div'),table=document.createElement('table'),head=document.createElement('tr');wrap.className='tablewrap';
        for(const cell of cells(line)){const th=document.createElement('th');inline(th,cell);head.append(th);}table.append(head);i+=2;
        while(i<lines.length && /^\s*\|/.test(lines[i]!)){const tr=document.createElement('tr');for(const cell of cells(lines[i++]!)){const td=document.createElement('td');inline(td,cell);tr.append(td);}table.append(tr);}
        wrap.append(table);out.push(wrap);continue;}
      const ordered=/^\s*\d+[.)]\s+/.test(line);
      if(ordered || /^\s*[-*+]\s+/.test(line)){const list=document.createElement(ordered?'ol':'ul');
        while(i<lines.length && (ordered?/^\s*\d+[.)]\s+/:/^\s*[-*+]\s+/).test(lines[i]!)){
          let item=lines[i++]!.replace(/^\s*(\d+[.)]|[-*+])\s+/,'');
          while(i<lines.length && /^\s{2,}\S/.test(lines[i]!) && !/^\s*(\d+[.)]|[-*+])\s+/.test(lines[i]!))item+=' '+lines[i++]!.trim();
          const li=document.createElement('li');inline(li,item);list.append(li);}
        out.push(list);continue;}
      const para:string[]=[];
      while(i<lines.length && lines[i]!.trim() && !/^(#{1,4}\s|```|\s*>|\s*\||\s*(\d+[.)]|[-*+])\s+|\s*(-{3,}|\*{3,})\s*$)/.test(lines[i]!))para.push(lines[i++]!.trim());
      if(para.length)block('p',para.join(' '));else i++;
    }
    target.replaceChildren(...out);
  }
  function readProgress(){
    const el=$('agreement') as HTMLElement,height=el?.scrollHeight,view=el?.clientHeight;
    const pct=!Number.isFinite(height) || !Number.isFinite(view) || height<=view+24?100:Math.min(100,Math.round((el.scrollTop+view)/height*100));
    if(pct>=98)readDone=true;
    text('read-label',readDone?t('readDone'):t('readPct',pct));
    const bar=$('read-bar');if(bar?.style)bar.style.width=(readDone?100:pct)+'%';
    if(!readDone && $('read-label'))text('read-label',t('readPct',pct)+' · '+t('readHint'));
    controls();
  }
  // Labels follow the language; the agreement body does not, so switching language never
  // rebuilds it (and never resets how far the signer has read).
  function renderAgreementMeta(){
    if(!docInfo){text('version',t('loadingShort'));return;}
    text('version',t('version',t('kindIndividual'),docInfo.version));text('digest',docInfo.digest);
  }
  // Loading the same text again (e.g. once the signing record exists) keeps the page, the scroll and "read to the end".
  async function agreement(path:string){const d=await api(path),same=!!docInfo && docInfo.digest===d.digest && docInfo.content===d.content;
    docInfo=d;renderAgreementMeta();if(!same){readDone=false;if(typeof d.content==='string')markdown($('agreement'),d.content);}readProgress();return d;}
  // How the typed signature is drawn on the PDF (src/pdf/styles.ts); stored with the evidence. Not a consent item.
  const STYLES=['signature','formal','pen','hand'];let sigStyle='signature';
  function renderStyles(){
    const typed=$<HTMLInputElement>('signature').value.trim() || $<HTMLInputElement>('name').value.trim();
    for(const style of STYLES){const sample=$('sample-'+style),button=$('style-'+style);
      if(sample){sample.textContent=typed || t('stylePlaceholder');sample.className='sample s-'+style+(typed?'':' empty');}
      if(button){button.className=style===sigStyle?'sty on':'sty';button.setAttribute?.('aria-checked',String(style===sigStyle));}}
  }
  function clearConsent(){($('signature') as HTMLInputElement).value='';renderStyles();
    document.querySelectorAll<HTMLInputElement>('#attestations input').forEach(x=>{x.checked=false;});}
  function clearPersonalFields(){
    $<HTMLInputElement>('name').value='';$<HTMLTextAreaElement>('address').value='';$<HTMLInputElement>('hold-confirm').checked=false;$<HTMLInputElement>('release-confirm').checked=false;show('withdraw-panel',false);
  }
  function discardPacket(){
    packet='';state=null;clearConsent();sessionStorage.removeItem('cla.packet');history.replaceState(null,'','/');
    clearPersonalFields();
    $('attestations').replaceChildren();consentLabels=[];legendEl=null;text('contributor-state',t('notSigned'));text('holder-state',t('holderWaiting'));text('record-time','');
    for(const role of ['contributor','holder','audit'])$<HTMLButtonElement>('download-'+role).disabled=true;
  }
  function packetPage(own:any,reset=false){
    const select=$<HTMLSelectElement>('packets');
    if(reset)select.replaceChildren(new Option(t('choose'),''));
    for(const p of own.packets)if(![...select.options].some(option=>option.value===p.id))select.add(new Option(p.id,p.id));
    nextCursor=own.nextCursor ?? null;hasPackets=select.options.length>1;
    // Only a freshly authorized historical selection may be pinned beyond this page.
    if(packet && state?.packet?.id===packet && state.actor?.id===own.actor.id &&
      ![...select.options].some(option=>option.value===packet))select.add(new Option(packet,packet));
    select.value=packet;
    text('packets-page',nextCursor?t('pageMore'):t('pageAll'));
  }
  // Everything below is derived from the last server observation; switching language re-runs it.
  function render(){
    for(const [id,key] of Object.entries(STATIC))text(id,t(key));
    text('t-address-hint',t(hasPdf()?'addressHint':'addressHintNoPdf'));
    if(typeof document.documentElement==='object' && document.documentElement)document.documentElement.lang=lang==='zh'?'zh-CN':'en';
    text('mode',cap.signingEnabled===false?t('modeClosed'):'');
    text('login',signedIn?t('relogin'):t('login'));
    if(!signedIn && !cap.actor)text('actor',t('signedOut'));
    else if(cap.actor)text('actor',t('actor',cap.actor.login,cap.actor.id)+' · '+t(cap.actor.role==='holder'?'roleHolder':'roleContributor'));
    const holder=cap.actor?.role==='holder';
    text('hero-eyebrow',holder?t('holderEyebrow'):t('eyebrow'));
    text('hero-title',holder?(signedBy('contributor') && !signedBy('holder')?t('holderTitle'):t('holderIdleTitle')):t('title'));
    text('hero-lede',holder?(signedBy('contributor') && !signedBy('holder')?t(hasPdf()?'holderLede':'holderLedeNoPdf'):t('holderIdleLede')):t('lede'));
    const stage=!signedIn?1:bothSigned()?4:signedBy('contributor')?3:2;
    for(let n=1;n<=4;n++){const li=$('step-'+n);if(li)li.className=n<stage?'done':n===stage?'now':'';}
    const done=bothSigned() && state?.current===true && state?.blocked!==true;
    // A contributor whose own signature is recorded is finished: show where things stand, never the form again.
    const waiting=!holder && !done && signedBy('contributor') && !signedBy('holder') && state?.current===true && state?.blocked!==true;
    const own=state?.signatures?.find((row:any)=>row.role==='contributor');
    const signedAt=Number.isSafeInteger(own?.created)?new Date(own.created*1000).toLocaleString(lang==='zh'?'zh-CN':'en-GB',{dateStyle:'long',timeStyle:'short'}):'';
    show('download-pdf',done && hasPdf());show('thanks',done || waiting);show('hero',!done && !waiting);show('personal-signature',!done && !waiting && !(holder && state?.blocked===true));
    const seal=$('thanks-seal');if(seal)seal.className=waiting?'seal wait':'seal';
    text('thanks-eyebrow',waiting?(signedAt?t('waitEyebrow',signedAt):t('signed')):t('thanksEyebrow'));
    text('thanks-title',waiting?t('waitTitle'):holder?t('thanksHolderTitle'):t('thanksTitle'));
    text('thanks-lede',waiting?t(hasPdf()?'waitLede':'waitLedeNoPdf',counterparty || t('waitHolder')):holder?t(hasPdf()?'thanksHolderLede':'thanksHolderLedeNoPdf'):t('thanksLede'));
    show('holder-hint',holder && !!holderName);if(holder && holderName)text('holder-hint',t('holderHint',holderName));
    renderStyles();
    if(legendEl)legendEl.textContent=t('legend');for(const {words,key} of consentLabels)words.textContent=t('a_'+key);
    text('sign',holder?t('signHolder'):t('signContributor'));
    show('withdraw-open',!!state?.canHold);
    // A contributor only ever deals with "sign again" when their last signing lapsed; the record list is for the holder,
    // for accounts with more than one record, and whenever records exist but none is selected (so "choose" is possible).
    // Only the rights holder is offered the release; the contributor just sees "Sign again" once it has happened.
    const block=holder && state?.blocked===true?state.block:null,day=(sec:number)=>new Date(sec*1000).toLocaleDateString(lang==='zh'?'zh-CN':'en-GB',{dateStyle:'long'});
    show('release-panel',!!block && Number.isSafeInteger(block.heldAt));show('release-form',!!state?.canRelease);
    if(block && Number.isSafeInteger(block.heldAt))text('release-note',Number.isSafeInteger(block.releasedAt)?t('releasedNote',day(block.releasedAt)):t('releaseNote',day(block.heldAt)));
    if(canCreate() && state?.blocked===true)text('t-restart-hint',t('restartReleasedHint'));
    show('restart',canCreate());show('sign-form',!canCreate() && state?.blocked!==true);show('record-picker',holder || (!state && hasPackets) || $<HTMLSelectElement>('packets').options.length>2 || !!nextCursor);
    if(state){
      text('contributor-state',signedBy('contributor')?t('signed'):t('notSigned'));
      const delegated=state.delegated;
      text('holder-state',signedBy('holder')?t('holderSigned'):delegated?.current?t('delegatedCurrent'):
        delegated?.recorded?t('delegatedHistory',t('d_'+(['disabled','expired','revoked','suspended','superseded','blocked','unverifiable','not_started'].includes(delegated.state)?delegated.state:'other'))):t('holderWaiting'));
      text('eligibility',state.blocked?t(holder?'eBlockedHolder':'eBlocked'):!state.current?t('eExpired'):
        state.signatures.length===2?t('eComplete'):delegated?.current?t('eDelegated'):t('eWaiting'));
      text('record-time',state.signatures.length===2?'':delegated?.current?
        t('tDelegated',new Date(delegated.expiresAt*1000).toLocaleString(lang==='zh'?'zh-CN':'en-GB')):t('tDeadline',new Date(state.packet.valid_until*1000).toLocaleString(lang==='zh'?'zh-CN':'en-GB')));
    }
    // A lapsed record is explained once, above "Sign again"; no stale deadline below it.
    if(canCreate() && state){text('record-time','');text('eligibility','');}
    renderAgreementMeta();
    if(!state){text('contributor-state',t('notSigned'));text('holder-state',t('holderWaiting'));}
    if(!signedIn)text('t-sign-hint',t('signInFirst'));
    if(!docInfo)text('agreement',t('loading'));
    if(lastMessage)text('message',t(lastMessage[0],...lastMessage[1]));
  }
  async function refresh(){
    let own:any;const previousActor=lastActor;
    cap=await api('/capabilities');
    try{own=await api('/session');if(previousActor!==undefined && previousActor!==own.actor.id){clearConsent();clearPersonalFields();}
      lastActor=own.actor.id;cap.actor=own.actor;signedIn=true;holderName=typeof own.actor.holderName==='string'?own.actor.holderName:'';counterparty=typeof own.holderName==='string'?own.holderName:'';state=null;
      if(packet && !/^[A-Za-z0-9_-]{43}$/.test(packet))throw new Error('packet_unavailable');
      if(!packet && own.packets.length===1 && !own.nextCursor)packet=own.packets[0].id;
      packetPage(own,true);
      // A contributor with no record yet gets one straight away (creating it signs nothing), so the form is ready to fill.
      if(!packet && !hasPackets && !autoCreated && canCreate()){autoCreated=true;packet=(await api('/packets',{kind:'individual'})).id;
        own=await api('/session');lastActor=own.actor.id;packetPage(own,true);}
      if(packet){state=await api('/status?packet='+encodeURIComponent(packet));packetPage(own,true);sessionStorage.setItem('cla.packet',packet);
        history.replaceState(null,'','/?packet='+encodeURIComponent(packet));
        await agreement('/agreement?packet='+encodeURIComponent(packet));
        const box=$('attestations'),legend=document.createElement('legend');legendEl=legend;box.replaceChildren(legend);consentLabels=[];
        const keys=['personal','electronic','read','identity',...(state.actor.role==='holder'?['reviewed']:['adult','own_rights','independent'])];
        for(const key of keys){const label=document.createElement('label'),input=document.createElement('input'),words=document.createElement('span');input.type='checkbox';input.name=key;input.required=true;
          label.append(input,words);box.append(label);consentLabels.push({input,words,key});}
        ($('download-contributor') as HTMLButtonElement).disabled=!signedBy('contributor');
        ($('download-holder') as HTMLButtonElement).disabled=!signedBy('holder');
        ($('download-audit') as HTMLButtonElement).disabled=!state.delegated?.canAudit;
        render();
      }else{state=null;await agreement('/agreement');render();text('eligibility',hasPackets?t('eChoose'):t('eCreate'));}
    }catch(error){state=null;if((error as Error).message==='packet_unavailable' && own){
        discardPacket();packetPage(own,true);await agreement('/agreement');render();text('eligibility',hasPackets?t('eGone'):t('eGoneCreate'));
      }else if((error as Error).message==='session_rejected'){signedIn=false;cap.actor=undefined;holderName='';counterparty='';await agreement('/agreement');render();}
      else throw error;}
    uncertain=false;controls();
  }
  async function action(fn:()=>Promise<void>){if(busy)return;busy=true;controls();try{await fn();}
    catch(error){uncertain=true;clearConsent();message('mFailed',[(error as Error).message],true);}
    finally{busy=false;controls();}}
  function save(name:string,blob:Blob){const link=window.document.createElement('a'),url=URL.createObjectURL(blob);link.href=url;link.download=name;link.click();URL.revokeObjectURL(url);}
  async function pdfBuilder():Promise<any>{
    const g=globalThis as any;if(g.TavottoClaPdf)return g.TavottoClaPdf;
    await new Promise<void>((resolve,reject)=>{const s=document.createElement('script') as HTMLScriptElement;s.src='/cla-pdf.js';s.onload=()=>resolve();s.onerror=()=>reject(new Error('pdf_unavailable'));document.head.append(s);});
    if(!g.TavottoClaPdf)throw new Error('pdf_unavailable');return g.TavottoClaPdf;
  }
  $('lang').onclick=()=>{lang=lang==='zh'?'en':'zh';try{localStorage.setItem('cla.lang',lang);}catch{}render();if(state || docInfo)readProgress();
    if(!state && $('eligibility'))text('eligibility',signedIn?(hasPackets?t('eChoose'):t('eCreate')):'');};
  $('agreement').onscroll=()=>readProgress();
  $<HTMLInputElement>('signature').oninput=renderStyles;$<HTMLInputElement>('name').oninput=renderStyles;
  for(const style of STYLES){const button=$('style-'+style);if(button)button.onclick=()=>{sigStyle=style;renderStyles();};}
  $('withdraw-open').onclick=()=>show('withdraw-panel',true);
  $('withdraw-cancel').onclick=()=>{$<HTMLInputElement>('hold-confirm').checked=false;show('withdraw-panel',false);};
  $('login').onclick=()=>void action(async()=>{sessionStorage.setItem('cla.packet',packet);const r=await api('/oauth/start',{});location.assign(r.authorizationURL);});
  $('create').onclick=()=>void action(async()=>{if(!canCreate())throw new Error('packet_creation_unavailable');
    packet=(await api('/packets',{kind:'individual'})).id;clearConsent();await refresh();message('mCreated');});
  $('refresh').onclick=()=>void action(async()=>{await refresh();message('mRefreshed');});
  $('packets-more').onclick=()=>void action(async()=>{if(!nextCursor)return;
    const own=await api('/session?cursor='+encodeURIComponent(nextCursor));
    if(own.actor.id!==cap.actor?.id)throw new Error('session_changed');packetPage(own);message('mMore');});
  $<HTMLSelectElement>('packets').onchange=()=>void action(async()=>{packet=$<HTMLSelectElement>('packets').value;clearConsent();await refresh();});
  $<HTMLFormElement>('sign-form').onsubmit=event=>{event.preventDefault();void action(async()=>{
    if(!state?.canSign)throw new Error('signing_unavailable');
    const role=state.actor.role,input={name:$<HTMLInputElement>('name').value,address:$<HTMLTextAreaElement>('address').value,
      signature:$<HTMLInputElement>('signature').value,signature_style:sigStyle,attestations:Object.fromEntries([...document.querySelectorAll<HTMLInputElement>('#attestations input')].map(x=>[x.name,x.checked]))};
    const intent=(await api('/intents',{packet,role})).intent;
    const result=await api('/sign',{packet,intent,role,evidence:input});clearConsent();$<HTMLFormElement>('sign-form').reset();await refresh();
    message(result.countersignature==='recorded'?'mSignedDelegated':result.countersignature==='pending_manual_review'?'mSignedManual':'mSigned');
  });};
  $('release').onclick=()=>void action(async()=>{if(!$<HTMLInputElement>('release-confirm').checked)throw new Error('personal_confirmation_required');
    if(!state?.canRelease || typeof state.block?.packet!=='string')throw new Error('release_unavailable');
    await api('/release',{packet:state.block.packet});$<HTMLInputElement>('release-confirm').checked=false;await refresh();message('mReleased');});
  $('hold').onclick=()=>void action(async()=>{if(!$<HTMLInputElement>('hold-confirm').checked)throw new Error('personal_confirmation_required');
    const intent=(await api('/intents',{packet,role:'hold'})).intent;await api('/hold',{packet,intent});clearConsent();clearPersonalFields();await refresh();
    message('mHeld');});
  for(const role of ['contributor','holder'])$('download-'+role).onclick=()=>void action(async()=>{
    const evidence=await api('/evidence?packet='+encodeURIComponent(packet)+'&role='+role),document=await api('/agreement?packet='+encodeURIComponent(packet));
    save('cla-'+packet+'-'+role+'.json',new Blob([JSON.stringify({agreement:document,evidence},null,2)],{type:'application/json'}));message('mEvidence');});
  $('download-audit').onclick=()=>void action(async()=>{
    const audit=await api('/audit?packet='+encodeURIComponent(packet));
    save('cla-'+packet+'-audit.json',new Blob([JSON.stringify(audit,null,2)],{type:'application/json'}));message('mAudit');
  });
  $('download-pdf').onclick=()=>void action(async()=>{
    if(!bothSigned())throw new Error('signatures_incomplete');
    const q='?packet='+encodeURIComponent(packet),contributor=await api('/evidence'+q+'&role=contributor'),holder=await api('/evidence'+q+'&role=holder'),doc=await api('/agreement'+q);
    const bytes=await (await pdfBuilder()).build({agreement:{kind:contributor.agreement_kind,version:doc.version,sha256:doc.digest},contributor,holder});
    save('Tavotto-CLA-'+String(contributor.github_login_at_signing ?? 'contributor')+'-signed.pdf',new Blob([bytes],{type:'application/pdf'}));message('mPdf');
  });
  show('withdraw-panel',false);render();
  void refresh().catch(error=>{controls();message('mLoadFailed',[(error as Error).message],true);});
}
