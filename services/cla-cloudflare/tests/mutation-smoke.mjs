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
 {name:'completed deadline',file:'src/native/store.ts',before:'WHERE p.subject=? AND p.digest=?\n',after:'WHERE p.subject=? AND p.digest=? AND p.valid_until>${DB_NOW}\n',pattern:'completed survives8days'},
 {name:'proof lifetime',file:'src/native/service.ts',before:'expires=now+120;',after:'expires=now+121;',pattern:'completed survives8days'},
 {name:'evidence hash',file:'src/native/service.ts',before:"if (await sha256(raw)!==row.evidence_hash) throw new Rejected('evidence_unavailable');",after:'// mutation: skip evidence hash validation',pattern:'wrong-hash'},
];
for(const c of cases){const tmp=mkdtempSync(path.join(os.tmpdir(),'cla-mutation-'));try{
 for(const p of ['src','tests','package.json'])cpSync(path.join(root,p),path.join(tmp,p),{recursive:true});
 const file=path.join(tmp,c.file),before=readFileSync(file,'utf8');assert.equal(before.split(c.before).length-1,1,c.name+' mutation location');
 const changed=before.replace(c.before,c.after);assert.notEqual(changed,before);writeFileSync(file,changed);assert.ok(readFileSync(file,'utf8').includes(c.after));
 const r=spawnSync(process.execPath,['--test','--test-name-pattern='+c.pattern,'tests/independent-lifecycle.test.mjs'],{cwd:tmp,encoding:'utf8'});
 assert.equal(r.error,undefined);assert.notEqual(r.status,0,c.name+' must make selected test fail');assert.ok((r.stdout+r.stderr).includes(c.pattern),'selected test was executed');console.log('Detected mutation: '+c.name);
}finally{rmSync(tmp,{recursive:true,force:true});}}
