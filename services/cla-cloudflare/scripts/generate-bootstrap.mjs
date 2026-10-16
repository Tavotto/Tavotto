// Offline SQL generation only. Never opens a database or contacts a service.
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {validateLive} from '../src/native/config.ts';
import {nativeSQL} from '../src/native/store.ts';
export function bootstrapSQL(c){
  validateLive(c);
  const a=c.policy.agreements,individual=a.individual,corporate=a.corporate;
  // All interpolated values have been constrained to integer/version/hex by validation.
  const state={holder:c.holderId,individual:{version:individual.version,digest:individual.sha256},corporate:{version:corporate.version,digest:corporate.sha256}};
  const digest=createHash('sha256').update(JSON.stringify(state)).digest('hex');
  return `-- OFFLINE rehearsal only; seed configuration SHA256 ${digest}\nBEGIN IMMEDIATE;\n`+nativeSQL(`
INSERT OR ROLLBACK INTO _guards(id,ok) SELECT 'bootstrap-config',CASE WHEN
 NOT EXISTS(SELECT 1 FROM authority WHERE id!=1 OR holder!=${c.holderId}) AND
 NOT EXISTS(SELECT 1 FROM agreements WHERE NOT
  (kind='individual' AND version='${individual.version}' AND digest='${individual.sha256}') AND NOT
  (kind='corporate' AND version='${corporate.version}' AND digest='${corporate.sha256}'))
 THEN 1 ELSE 0 END;
INSERT OR IGNORE INTO authority(id,holder) VALUES(1,${c.holderId});
INSERT OR IGNORE INTO agreements(kind,version,digest) VALUES('individual','${individual.version}','${individual.sha256}');
INSERT OR IGNORE INTO agreements(kind,version,digest) VALUES('corporate','${corporate.version}','${corporate.sha256}');
DELETE FROM _guards WHERE id='bootstrap-config';
INSERT OR ROLLBACK INTO _guards(id,ok) SELECT 'bootstrap-complete',CASE WHEN
 (SELECT count(*) FROM authority)=1 AND (SELECT count(*) FROM agreements)=2 THEN 1 ELSE 0 END;
DELETE FROM _guards WHERE id='bootstrap-complete';
`)+`COMMIT;\n`;
}
export function bootstrapStatements(c){
  // Same validated source as the offline rehearsal, excluding its transaction wrapper.
  return bootstrapSQL(c).split('\n').filter(line=>!line.startsWith('--') && line!=='BEGIN IMMEDIATE;' && line!=='COMMIT;')
    .join('\n').split(';').map(sql=>sql.trim()).filter(Boolean);
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
  if(process.argv.length<3 || process.argv.length>4 || process.argv.length===4 && process.argv[3]!=='--d1-statements')
    throw Error('usage: node scripts/generate-bootstrap.mjs CONFIG.json [--d1-statements]');
  if(fs.statSync(process.argv[2]).size>65536)throw Error('configuration too large');
  const config=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  process.stdout.write(process.argv[3]?JSON.stringify(bootstrapStatements(config),null,2)+'\n':bootstrapSQL(config));
}
