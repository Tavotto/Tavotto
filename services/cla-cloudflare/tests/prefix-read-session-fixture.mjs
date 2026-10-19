import assert from 'node:assert/strict';
import {fixture as adapterFixture,sha,user,networkCalls} from './merge-prefix-fixture.mjs';
import {validateLive} from '../src/native/config.ts';
import {randomToken,sha256} from '../src/core/crypto.ts';

// Actual GitHub adapter/RSA installation handshake and SQLite. All peers are
// synthetic in-process responses; this is not real GitHub/platform acceptance.
// The existing #1172 fixture and its measurements remain unchanged.
export {sha,user,networkCalls};
export async function fixture({session=true,personalContributors=0,...options}={}){
 const f=await adapterFixture({...options,...(personalContributors?{signed:false}:{})});
 if(personalContributors){
  assert.equal(personalContributors,2);
  f.c.delegatedCountersigningEnabled=false;f.s.c.delegatedCountersigningEnabled=false;
  const p=await f.create();await f.sign(p);await f.sign(p,'holder');
  const token=randomToken(),csrf=randomToken();
  await f.s.store.addSession({token_hash:await sha256(token),actor:3,login:'user3',csrf_hash:await sha256(csrf),expires:f.now()+86400});
  const args=[token,csrf,f.c.origin],q=await f.s.packet(...args,'individual');
  await f.s.sign(...args,q.id,await f.s.intent(...args,q.id,'contributor'),'contributor',f.evidence('contributor'));await f.sign(q,'holder');
  f.prs.get(12).user=user(3);f.commits.get(12)[0].author=user(3);
 }
 if(session==='absent')delete f.c.mergePrefixReadSessionEnabled;else f.c.mergePrefixReadSessionEnabled=session;
 validateLive(f.c);
 return f;
}
export function coauthor(f){f.commits.get(11)[0].commit.message='Co-authored-by: Synthetic <3+user3@users.noreply.github.com>\nCo-authored-by: Synthetic <3+USER3@users.noreply.github.com>';}
export function metrics(f){return {budget:f.budget.snapshot(),calls:f.calls,SQL:f.sqlReads,installationGET:f.state.installationReads,tokenPOST:f.state.tokenReads,metadataGET:f.state.metadataReads,mainGET:f.state.branchReads,queueQueries:f.queries.length,identityGET:f.state.userReads,PRReads:[...f.state.prReads],compareReads:[...f.state.compareReads]};}
export async function isolated(fn,options){
 const f=await fixture(options),before=await f.digest();
 try{await fn(f);f.assertReadOnly();assert.equal(await f.digest(),before,'read session must leave SQLite unchanged');}
 finally{f.close();}
}
