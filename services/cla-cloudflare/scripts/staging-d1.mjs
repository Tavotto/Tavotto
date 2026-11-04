// STAGING ONLY. Offline SQL generation: emits schema + seed for a brand-new,
// disposable staging D1. Opens no database and contacts no service.
//
// Production still requires one atomic db.batch (see README "Explicit database
// bootstrap"). Staging deliberately uses `wrangler d1 execute --file`, which is not
// atomic: a partial apply is detected by the readiness query in the runbook and
// fixed by deleting and recreating the staging database. Never use this on a
// database that holds real signatures.
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {nativeSQL} from '../src/native/store.ts';
import {bootstrapStatements} from './generate-bootstrap.mjs';
import {STAGING_ORIGIN} from './deployment-plan.mjs';
const schemaFiles=['../src/base.sql','../src/native/schema.sql'];
// --rebuild: drop every service table first, in reverse creation order, in the same file. DROP TABLE runs no
// triggers, so it also clears rows the schema keeps immutable (signatures, withdrawals). Same database ID, so no
// config changes; secrets are Worker secrets and stay. Use it when staging test data must go (a held test account,
// a changed holderName or agreement text); it destroys every staging signature.
export function stagingRebuildSQL(config){
  const sql=stagingSQL(config),tables=[...sql.matchAll(/^CREATE TABLE (?:IF NOT EXISTS )?([A-Za-z0-9_]+)/gm)].map(m=>m[1]);
  if(tables.length===0)throw Error('staging_tables_missing');
  return '-- STAGING ONLY: drops all service tables, then recreates them.\nPRAGMA defer_foreign_keys = on;\n'+
    tables.reverse().map(t=>`DROP TABLE IF EXISTS ${t};`).join('\n')+'\n'+sql;
}
export function stagingSQL(config){
  if(config?.origin!==STAGING_ORIGIN)throw Error('staging_origin_required');
  const live={...config,githubEnabled:false,oauthEnabled:false,signingEnabled:false,checkPublishingEnabled:false};
  // D1 enforces foreign keys by default; the PRAGMA line is SQLite-local setup.
  const schema=schemaFiles.map(file=>nativeSQL(fs.readFileSync(new URL(file,import.meta.url),'utf8'))
    .split('\n').filter(line=>!/^PRAGMA foreign_keys\b/i.test(line.trim())).join('\n')).join('\n');
  return `-- STAGING ONLY: fresh disposable database. Not atomic; see scripts/staging-d1.mjs.\n${schema}\n`+
    bootstrapStatements(live).map(sql=>sql+';').join('\n')+'\n';
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
  try{const args=process.argv.slice(2),rebuild=args[0]==='--rebuild';if(rebuild)args.shift();
    if(args.length!==1)throw Error('usage: node scripts/staging-d1.mjs [--rebuild] deployment/staging/config.json');
    const config=JSON.parse(fs.readFileSync(args[0],'utf8'));process.stdout.write(rebuild?stagingRebuildSQL(config):stagingSQL(config));
  }catch(error){console.error(error.message);process.exitCode=1;}
}
