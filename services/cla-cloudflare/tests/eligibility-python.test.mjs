import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {eligibility} from '../src/native/eligibility.ts';
import {fixture} from './publication-lifecycle-fixture.mjs';

const NONCE='SYNTHETIC_NONCE_0123456789_abcdefghijklmnop';
const gate=fileURLToPath(new URL('../../../scripts/ci/cla_gate.py',import.meta.url));
// Execute the real Python consumer. Its expected identities and queue members
// come from the independent GitHub peer, never from eligibility's answer.
const python=String.raw`
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('cla_gate_contract', sys.argv[1])
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
payload = json.load(sys.stdin)
signed, reason = gate.signed_logins(None, payload['policy'], payload['context'])
print(json.dumps({'signed': sorted(signed), 'reason': reason}))
`;
function consume(f,answer){
 const context={kind:'merge_group',repository_id:f.c.repositoryId,head_sha:f.groupTarget.headSha,
  base_sha:f.groupTarget.baseSha,not_before:f.now(),now:f.now(),nonce:NONCE,eligibility:answer,
  group_members:f.entries.map(e=>({pr:e.pullRequest.number,headSha:e.pullRequest.headRefOid})),
  contributors:[...f.members.values()].map(p=>({login:p.user.login,ids:[p.user.id]}))};
 const result=spawnSync(process.env.PYTHON??'python3',['-I','-c',python,gate],{
  input:JSON.stringify({policy:f.c.policy,context}),encoding:'utf8',timeout:10_000});
 assert.equal(result.status,0,result.stderr||String(result.error));
 return JSON.parse(result.stdout);
}

test('real Node eligibility answer passes Python with reverse PR queue order and strict proof members',async()=>{
 const f=await fixture({group:true,delegated:false,prefix:2});try{
  // Queue PR 12 before PR 11, keeping each original PR's independent head.
  const first=f.members.get(11),second=f.members.get(12);
  first.number=12;second.number=11;f.members.clear();f.members.set(12,first);f.members.set(11,second);
  f.entries[0].pullRequest.number=12;f.entries[1].pullRequest.number=11;
  const target={kind:'merge_group',headSha:f.groupTarget.headSha,baseSha:f.groupTarget.baseSha};
  const answer=await eligibility(f.invocation().service,target,target.headSha,NONCE);
  assert.equal(answer.eligible,true);
  assert.deepEqual(f.entries.map(e=>e.pullRequest.number),[12,11]);
  assert.deepEqual(answer.members.map(m=>m.pr),[11,12]);
  for(const m of answer.members)assert.deepEqual(Object.keys(m).sort(),['head_sha','pr']);
  assert.deepEqual(consume(f,answer),{signed:['*'],reason:null});
  const denied={signed:[],reason:'provider_eligibility_invalid'};
  const aliases=structuredClone(answer);
  for(const m of aliases.members){m.headSha=m.head_sha;delete m.head_sha;}
  assert.deepEqual(consume(f,aliases),denied);
  const unordered=structuredClone(answer);unordered.members.reverse();
  assert.deepEqual(consume(f,unordered),denied);
  const wrongHead=structuredClone(answer);wrongHead.members[0].head_sha='1'.repeat(40);
  assert.deepEqual(consume(f,wrongHead),denied);
 }finally{f.close();}
});
