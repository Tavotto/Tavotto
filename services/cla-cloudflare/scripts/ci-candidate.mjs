// Review-only consumer entry. No workflow invokes this file in this PR.
import {readFile} from 'node:fs/promises';
import {parseArgs} from 'node:util';
import {pathToFileURL} from 'node:url';
import {eventLocator,readOnly} from '../src/ci/consumer.ts';
import {verifyWhenReady,waiting} from '../src/ci/wait.ts';
export async function candidate(config,event,eventName,token,{transport,clock,wallMilliseconds,monotonic,pause}={}){
  // Reject disabled or unsupported events before constructing a reader. Configuration
  // and this source must come from a trusted main commit, never from the PR checkout.
  if(config?.enabled!==true)throw Error('ci_provider_disabled');
  if(eventName!=='pull_request' && eventName!=='merge_group')throw Error('ci_candidate_event_rejected');
  const locator=eventLocator(event,eventName);
  const trusted=structuredClone(config);Object.freeze(trusted.wait);Object.freeze(trusted);Object.freeze(locator);
  const enabled=!!waiting(trusted);
  return verifyWhenReady(trusted,locator,readOnly(trusted,token,transport,enabled),{clock,wallMilliseconds,monotonic,pause});
}
async function main(){
  const {values}=parseArgs({options:{config:{type:'string'},'event-file':{type:'string'},event:{type:'string'}},strict:true,allowPositionals:false});
  if(Object.values(values).length!==3 || Object.values(values).some(v=>typeof v!=='string' || !v))throw Error('ci_candidate_arguments');
  const config=JSON.parse(await readFile(values.config,'utf8')),event=JSON.parse(await readFile(values['event-file'],'utf8'));
  const result=await candidate(config,event,values.event,process.env.GH_TOKEN??'');
  process.stdout.write(JSON.stringify(result)+'\n');
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href)main().catch(e=>{
  // Do not print exception messages from transports, payloads, or credentials.
  process.stderr.write('CLA CI candidate rejected ('+(typeof e?.code==='string' && /^[a-z_]+$/.test(e.code)?e.code:'closed')+').\n');process.exitCode=1;
});
