import {CIPending,verifyCI} from './consumer.ts';
import type {CIAnchor,CIConfig,Locator,Read} from './consumer.ts';
import {Rejected} from '../core/types.ts';

export interface WaitTools {clock?:()=>number;wallMilliseconds?:()=>number;monotonic?:()=>number;pause?:(ms:number)=>Promise<void>}
export function waiting(c:CIConfig):{maxAttempts:number;intervalMs:number;timeoutMs:number}|undefined {
  const w=c.wait;if(w===undefined)return;
  if(!w || typeof w!=='object' || Array.isArray(w) || typeof w.enabled!=='boolean')throw new Rejected('ci_wait_configuration_invalid');
  const names=Object.keys(w).sort().join(',');
  if(!w.enabled){if(names!=='enabled')throw new Rejected('ci_wait_configuration_invalid');return;}
  const {maxAttempts,intervalMs,timeoutMs}=w;
  if(names!=='enabled,intervalMs,maxAttempts,timeoutMs' || !Number.isSafeInteger(maxAttempts) || maxAttempts!<2 || maxAttempts!>8 ||
    !Number.isSafeInteger(intervalMs) || intervalMs!<1000 || intervalMs!>10000 || !Number.isSafeInteger(timeoutMs) || timeoutMs!<1000 || timeoutMs!>60000)
    throw new Rejected('ci_wait_configuration_invalid');
  return {maxAttempts:maxAttempts!,intervalMs:intervalMs!,timeoutMs:timeoutMs!};
}
// Waiting never publishes, reuses eligibility or replaces proof expiry. A pending
// result is admitted only by verifyCI after its trusted-source/head checks.
export async function verifyWhenReady(c:CIConfig,input:Locator,read:Read,tools:WaitTools={}):Promise<unknown> {
  const policy=waiting(c),clock=tools.clock??(()=>Math.floor(Date.now()/1000));
  if(!policy)return verifyCI(c,input,read,clock);
  const tick=tools.monotonic??(()=>performance.now());
  // Production retains the fractional wall second. A seconds-only injected
  // clock conservatively budgets from the end of its reported second.
  const wallMilliseconds=tools.wallMilliseconds??(tools.clock?()=> (clock()+1)*1000:()=>Date.now());
  const pause:(ms:number)=>Promise<void>=tools.pause??(ms=>new Promise<void>(resolve=>setTimeout(resolve,ms)));
  let last=tick(),lastWall=clock(),lastMilliseconds=wallMilliseconds(),deadline=last+policy.timeoutMs,anchor:CIAnchor|undefined;
  if(!Number.isFinite(last) || !Number.isSafeInteger(lastWall) || !Number.isSafeInteger(lastMilliseconds))throw new Rejected('ci_wait_deadline');
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
  const aborted=new Promise<never>((_,reject)=>controller.signal.addEventListener('abort',()=>reject(new Rejected('ci_wait_deadline')),{once:true}));
  const guard=()=>{
    const now=tick(),wall=clock(),milliseconds=wallMilliseconds();
    if(!Number.isFinite(now) || now<last || !Number.isSafeInteger(wall) || wall<lastWall || now>=deadline || controller.signal.aborted ||
      !Number.isSafeInteger(milliseconds) || milliseconds<lastMilliseconds ||
      anchor?.expiresAt!==undefined && milliseconds>=anchor.expiresAt*1000 ||
      anchor?.expiresAt!==undefined && wall>=anchor.expiresAt)throw new Rejected('ci_wait_deadline');
    last=now;lastWall=wall;lastMilliseconds=milliseconds;
    return {now,milliseconds};
  };
  const arm=()=>{const {now}=guard();clearTimeout(timer);timer=setTimeout(()=>controller.abort(),Math.max(0,deadline-now));};
  const bounded=async<T>(work:()=>Promise<T>):Promise<T>=>{guard();try{const result=await Promise.race([work(),aborted]);guard();return result;}catch(error){guard();throw error;}};
  const guardedRead:Read=(url,channel)=>bounded(()=>read(url,channel,controller.signal));
  const observe=(next:CIAnchor)=>{
    const {now,milliseconds}=guard();anchor=next;
    if(anchor.expiresAt!==undefined){deadline=Math.min(deadline,now+anchor.expiresAt*1000-milliseconds);arm();}
    guard();
  };
  arm();
  try {
    for(let attempt=0;attempt<policy.maxAttempts;attempt++){
      guard();
      try{const result=await verifyCI(c,input,guardedRead,clock,{pending:true,anchor,observe});guard();return result;}
      catch(error){
        guard();if(!(error instanceof CIPending) || !error.anchor)throw error;
        if(anchor && (anchor.source!==error.anchor.source || anchor.candidate && anchor.candidate!==error.anchor.candidate))throw new Rejected('ci_candidate_changed');
        observe(error.anchor);
        guard();if(attempt+1>=policy.maxAttempts)throw new Rejected('ci_wait_exhausted');
        if(deadline-tick()<=policy.intervalMs)throw new Rejected('ci_wait_deadline');
        await bounded(()=>pause(policy.intervalMs));
      }
    }
    throw new Rejected('ci_wait_exhausted');
  }finally{clearTimeout(timer);}
}
