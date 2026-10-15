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
 {name:'verification producer capacity',file:'src/native/service.ts',before:'length>MAX_SUCCESS_CONTRIBUTORS)',after:'length>MAX_SUCCESS_CONTRIBUTORS+1)',pattern:'seven-person producer rejects capacity before qualification or any publication',testFile:'tests/independent-bootstrap-budget.test.mjs'},
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
 {name:'completed deadline',file:'src/native/store.ts',before:'WHERE p.subject=? AND p.digest=?\n',after:'WHERE p.subject=? AND p.digest=? AND p.valid_until>${DB_NOW}\n',pattern:'completed survives8days'},
 {name:'proof lifetime',file:'src/native/service.ts',before:'expires=now+120;',after:'expires=now+121;',pattern:'completed survives8days'},
 {name:'evidence hash',file:'src/native/service.ts',before:"if (await sha256(raw)!==row.evidence_hash) throw new Rejected('evidence_unavailable');",after:'// mutation: skip evidence hash validation',pattern:'wrong-hash'},
];
for(const c of cases){const tmp=mkdtempSync(path.join(os.tmpdir(),'cla-mutation-'));try{
 for(const p of ['src','tests','scripts','package.json'])cpSync(path.join(root,p),path.join(tmp,p),{recursive:true});
 const file=path.join(tmp,c.file),before=readFileSync(file,'utf8');assert.equal(before.split(c.before).length-1,1,c.name+' mutation location');
 const changed=before.replace(c.before,c.after);assert.notEqual(changed,before);writeFileSync(file,changed);assert.ok(readFileSync(file,'utf8').includes(c.after));
 const r=spawnSync(process.execPath,['--test','--test-name-pattern='+c.pattern,c.testFile??'tests/independent-lifecycle.test.mjs'],{cwd:tmp,encoding:'utf8'});
 assert.equal(r.error,undefined);assert.notEqual(r.status,0,c.name+' must make selected test fail');assert.ok((r.stdout+r.stderr).includes(c.pattern),'selected test was executed');console.log('Detected mutation: '+c.name);
}finally{rmSync(tmp,{recursive:true,force:true});}}
