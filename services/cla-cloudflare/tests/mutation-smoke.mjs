import assert from 'node:assert/strict';
import {cpSync,mkdtempSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
// Each mutation changes one asserted source location in an isolated copy; a
// nonzero test exit proves the named behavioral regression is actually detected.
const cases=[
 ...JSON.parse(readFileSync(new URL('./independent-slot-recovery-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./slot-recovery-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./v2-upgrade-durability-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./v2-upgrade-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./v2-upgrade-cli-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./confirmation-slot-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./slot-budget-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./slot-config-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./agreement-activation-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./historical-revocation-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./independent-review-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./inflight-mutation-cases.json',import.meta.url),'utf8')),
 ...JSON.parse(readFileSync(new URL('./ci-wait-mutation-cases.json',import.meta.url),'utf8')),
 {name:'head admission capacity',file:'src/native/automatic.ts',before:'LIMIT 100))<100',after:'LIMIT 100))<101',pattern:'head request admission rejects the 101st distinct head',testFile:'tests/automatic.test.mjs'},
 {name:'delivery admission capacity',file:'src/native/automatic.ts',before:'LIMIT 1000))<1000',after:'LIMIT 1000))<1001',pattern:'delivery admission rejects the 1001st record while exact duplicate remains idempotent',testFile:'tests/automatic.test.mjs'},
 {name:'delegated disabled direct operation',file:'src/native/delegation.ts',before:"if(this.s.c.delegatedCountersigningEnabled!==true || !this.s.c.signingEnabled)throw new Rejected('delegated_countersigning_disabled');",after:'// mutation: skip delegation capability guard',pattern:'disabled delegated operation cannot use an existing personally signed packet',testFile:'tests/delegation.test.mjs'},
 {name:'delegated receipt hash',file:'src/native/delegation.ts',before:'await sha256(raw)!==r.hash || ',after:'',pattern:'authentic receipt ciphertext with wrong stored hash is rejected',testFile:'tests/delegation.test.mjs'},
 {name:'delegated holder identity',file:'src/native/delegation.ts',before:'g.holder_id!==c.holderId ||',after:'',pattern:'authorization rejects exact scope mismatch holder_id',testFile:'tests/delegation.test.mjs'},
 {name:'delegated exact agreement hash',file:'src/native/delegation.ts',before:'g.agreement_sha256!==a.sha256 ||',after:'',pattern:'authorization rejects exact scope mismatch agreement_sha256',testFile:'tests/delegation.test.mjs'},
 {name:'delegated authorization signature',file:'src/native/delegation.ts',before:"if(!await crypto.subtle.verify('Ed25519',key,sig,bytes(record.body)))throw new Error();",after:'// mutation: skip grant cryptographic signature',pattern:'tampered signature, changed body and unregistered key fail closed',testFile:'tests/delegation.test.mjs'},
 {name:'delegated personal evidence verification',file:'src/native/delegation.ts',before:"await this.s.stagedVerifyEvidence(p,contributor);\n    const record=",after:'// mutation: skip personally signed contributor evidence\n    const record=',pattern:'unverifiable stored contributor evidence cannot receive a delegated receipt',testFile:'tests/delegation.test.mjs'},
 {name:'CI default-branch binding',file:'src/ci/consumer.ts',before:'p.base?.ref===meta.default_branch && ',after:'',pattern:'CI candidate refuses a retargeted base branch at first and final read',testFile:'tests/ci-candidate.test.mjs'},
 {name:'delegated active control',file:'src/native/delegation.ts',before:" || event.action!=='enable'",after:'',pattern:'revocation is terminal and signed old enable cannot replay',testFile:'tests/delegation.test.mjs'},
 {name:'delegated receipt identity',file:'src/native/delegation.ts',before:'raw!==canonical(expected)',after:'false',pattern:'decrypted receipt identity must match its packet and holder even with a valid ciphertext hash',testFile:'tests/delegation.test.mjs'},
 {name:'delegated expiry ceiling',file:'src/native/service.ts',before:'expires=Math.min(expires,ceiling.expires);',after:'// mutation: proof ignores authorization expiry',pattern:'authorization deadline caps proof and expiration rejects all old delegated receipts',testFile:'tests/delegation.test.mjs'},
 {name:'automatic expected head',file:'src/native/automatic.ts',before:"if(check.headSha!==job.head)throw new Rejected('automatic_head_changed');",after:'// mutation: omit requested head guard',pattern:'old head event rejects before enqueue and queued old head becomes manual with empty result',testFile:'tests/automatic.test.mjs'},
 {name:'automatic final binding',file:'src/native/automatic.ts',before:'!sameBinding(check.proof,fresh) || ',after:'',pattern:'base changes during final read never commits observation',testFile:'tests/automatic.test.mjs'},
 {name:'automatic evidence revision requeue',file:'src/native/schema.sql',before:"WHERE status IN ('done','running');",after:"WHERE status='__never__';",pattern:'signing after an initial negative observation queues a new revision and clears old result',testFile:'tests/automatic.test.mjs'},
 {name:'verification producer capacity',file:'src/native/service.ts',before:'length>successCapacity(this.c))',after:'length>successCapacity(this.c)+1)',pattern:'seven-person producer rejects capacity before qualification or any publication',testFile:'tests/independent-bootstrap-budget.test.mjs'},
 {name:'production bootstrap readiness',file:'src/native/worker.ts',before:'new LiveStore(env.DB,budget,c)',after:'new LiveStore(env.DB,budget)',pattern:'production service fresh schema refuses writes without fixture initialization',testFile:'tests/independent-bootstrap-budget.test.mjs'},
 {name:'identity lookup preflight cap',file:'src/core/identity.ts',before:"if(resolve.size>10)throw new Rejected('identity_lookup_limit');",after:"if(resolve.size>11)throw new Rejected('identity_lookup_limit');",pattern:'11 unique lookup identities fail before any remote lookup',testFile:'tests/independent-identity-admission.test.mjs'},
 {name:'fresh exemption identity',file:'src/core/identity.ts',before:'resolve.set(normalized,id);',after:'// mutation: trust configured exemption ID',pattern:'exemption login match still requires fresh trusted ID resolution',testFile:'tests/independent-identity-admission.test.mjs'},
 {name:'OAuth bounded burst',file:'src/native/store.ts',before:'WHERE expires>${DB_NOW} LIMIT 10))<10',after:'WHERE expires>${DB_NOW} LIMIT 10))<11',pattern:'OAuth burst accepts 10 and rejects 11th without adding a row',testFile:'tests/independent-public-abuse.test.mjs'},
 {name:'public agreement pinned digest',file:'src/native/worker.ts',before:"if(await sha256(data)!==a.sha256)throw new Rejected('agreement_digest_mismatch');",after:'// mutation: omit public document hash',pattern:'public agreement changed policy digest cannot return old asset',testFile:'tests/independent-public-abuse.test.mjs'},
 {name:'target-wide refresh blocker',file:'src/native/store.ts',before:"AND NOT EXISTS(SELECT 1 FROM publication_attempts WHERE target_key=? AND phase!='resolved') AND (${extra})",after:"AND NOT EXISTS(SELECT 1 FROM publication_attempts WHERE target_key=? AND phase='__never__') AND (${extra})",pattern:'older unknown failure source blocks refresh completion/retry for the entire target',testFile:'tests/independent-refresh-boundaries.test.mjs'},
 {name:'cached account selection',file:'src/native/ui-client.ts',before:"packet='';state=null;clearConsent();sessionStorage.removeItem('cla.packet');history.replaceState(null,'','/');",after:'state=null;clearConsent();',pattern:'unauthorized session storage packet is discarded while current account history remains usable',testFile:'tests/independent-ui-packets.test.mjs'},
 {name:'expired UI replacement',file:'src/native/ui-client.ts',before:'disabled=busy || uncertain || !canCreate();',after:'disabled=busy || uncertain || !!state;',pattern:'auto-selected expired incomplete record permits a fresh packet with cleared consent',testFile:'tests/independent-ui-packets.test.mjs'},
 {name:'holder page continuation',file:'src/native/service.ts',before:'ORDER BY created DESC,id DESC LIMIT 21',after:'ORDER BY created DESC,id DESC LIMIT 20',pattern:'holder discovers 45 packets, including same-second boundaries, once each in bounded pages',testFile:'tests/independent-packet-pagination.test.mjs'},
 {name:'stateless token length',file:'src/native/github.ts',before:'data.token.length>8192',after:'data.token.length>256',pattern:'accepts stateless 520-character',testFile:'tests/github-installation-token.test.mjs'},
 {name:'completed deadline',file:'src/native/store.ts',before:"h.role='holder' WHERE p.subject=? AND p.digest=?\n",after:"h.role='holder' WHERE p.subject=? AND p.digest=? AND p.valid_until>${DB_NOW}\n",pattern:'completed survives8days'},
 {name:'proof lifetime',file:'src/native/service.ts',before:'expires=now+120;',after:'expires=now+121;',pattern:'completed survives8days'},
 {name:'evidence hash',file:'src/native/service.ts',before:"if (await sha256(raw)!==row.evidence_hash) throw new Rejected('evidence_unavailable');",after:'// mutation: skip evidence hash validation',pattern:'wrong-hash'},
];
for(const c of cases){const tmp=mkdtempSync(path.join(os.tmpdir(),'cla-mutation-'));try{
 for(const p of ['src','tests','scripts','migrations','deployment','package.json'])cpSync(path.join(root,p),path.join(tmp,p),{recursive:true});
 const file=path.join(tmp,c.file),before=readFileSync(file,'utf8');assert.equal(before.split(c.before).length-1,1,c.name+' mutation location');
 const changed=before.replace(c.before,c.after);assert.notEqual(changed,before);writeFileSync(file,changed);assert.ok(readFileSync(file,'utf8').includes(c.after));
 const r=c.runner==='python'?spawnSync('python3',[c.testFile,'Migration.'+c.pattern,'-v'],{cwd:tmp,encoding:'utf8'}):
   spawnSync(process.execPath,['--test','--test-name-pattern='+c.pattern,c.testFile??'tests/independent-lifecycle.test.mjs'],{cwd:tmp,encoding:'utf8'});
 assert.equal(r.error,undefined);assert.notEqual(r.status,0,c.name+' must make selected test fail');assert.ok((r.stdout+r.stderr).includes(c.selectedTitle??c.pattern),'selected test was executed');
 if(c.assertionError){assert.equal(r.status,1);assert.ok((r.stdout+r.stderr).includes('AssertionError'),'actual assertion must fail');}
 console.log('Detected mutation: '+c.name+' (selected exit '+r.status+')');
}finally{rmSync(tmp,{recursive:true,force:true});}}
