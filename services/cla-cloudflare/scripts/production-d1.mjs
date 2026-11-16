// PRODUCTION database initialization for https://cla.tavotto.com. Two steps, run by the
// operator with their own Cloudflare login (see deployment/production/README.md):
//
//   schema CONFIG.json            → stdout: schema-only SQL for `wrangler d1 execute --remote --file`.
//                                   Contains no seed row. Applied to a brand-new, empty database only.
//   apply  CONFIG.json MANIFEST   → checks the remote schema matches this checkout exactly, then
//                                   submits the bootstrap seed as ONE db.batch (a D1 SQL transaction:
//                                   any failing statement rolls back the whole sequence), then runs the
//                                   service's own readiness read. Prints counts only.
//
// Why the split: the seed is the part that must be atomic (README "Explicit database bootstrap");
// D1 documents db.batch as a transaction, while the REST/CLI multi-statement path is not documented
// as atomic. The schema goes through the CLI because its trigger bodies contain semicolons and
// `wrangler d1 execute --file` is the path the staging rehearsal proved. If the schema check fails,
// nothing has been seeded and the empty database can be deleted and recreated.
//
// No secret is read or printed. `apply` refuses a database that already holds signing data rows.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {nativeSQL} from '../src/native/store.ts';
import {LiveStore} from '../src/native/store.ts';
import {bootstrapStatements} from './generate-bootstrap.mjs';
import {PRODUCTION_ORIGIN,deploymentPlan} from './deployment-plan.mjs';

const schemaFiles=['../src/base.sql','../src/native/schema.sql'];
const PREFIX='cla_native_v2_';
// Tables that carry signing history or live state. The seed tables (authority, agreements) are
// allowed to hold exactly the configured rows so that a repeated apply is a no-op.
const OBJECTS_SQL=`SELECT type,name FROM sqlite_master WHERE name LIKE '${PREFIX}%' AND name NOT LIKE 'sqlite_autoindex_%' ORDER BY type,name`;

function requireProduction(config){
  if(config?.origin!==PRODUCTION_ORIGIN)throw Error('production_origin_required');
  return {...config,githubEnabled:false,oauthEnabled:false,signingEnabled:false,checkPublishingEnabled:false};
}
function schemaText(){
  // D1 enforces foreign keys itself; the PRAGMA line is SQLite-local setup.
  return schemaFiles.map(file=>nativeSQL(fs.readFileSync(new URL(file,import.meta.url),'utf8'))
    .split('\n').filter(line=>!/^PRAGMA foreign_keys\b/i.test(line.trim())).join('\n')).join('\n');
}
export function productionSchemaSQL(config){
  requireProduction(config);
  return `-- PRODUCTION schema only (no seed). Apply once to an empty database; seed with \`production-d1.mjs apply\`.\n${schemaText()}\n`;
}
// The exact object list this checkout's schema creates, from an in-memory SQLite build.
export function expectedObjects(){
  const db=new DatabaseSync(':memory:');db.exec(schemaText());
  return db.prepare(OBJECTS_SQL).all().map(r=>`${r.type}:${r.name}`);
}
const rows=async(db,sql)=>(await db.prepare(sql).all()).results ?? [];
export async function productionBootstrap(db,config){
  const live=requireProduction(config);
  const actual=(await rows(db,OBJECTS_SQL)).map(r=>`${r.type}:${r.name}`),expected=expectedObjects();
  if(JSON.stringify(actual)!==JSON.stringify(expected))throw Error('production_schema_mismatch');
  const tables=expected.filter(o=>o.startsWith('table:')).map(o=>o.slice(6));
  // The schema itself creates the single revision row (1,0); any write since then moves it.
  const seeded=new Set([PREFIX+'authority',PREFIX+'agreements',PREFIX+'_guards',PREFIX+'state']);
  const state=await rows(db,`SELECT id,revision FROM ${PREFIX}state`);
  if(state.length!==1 || state[0].id!==1 || state[0].revision!==0)throw Error('production_database_not_fresh');
  for(const table of tables.filter(t=>!seeded.has(t))){
    const [{n}]=await rows(db,`SELECT count(*) n FROM ${table}`);
    if(n!==0)throw Error('production_database_not_fresh');
  }
  // One transaction: the guard statements inside roll the whole batch back on any mismatch.
  await db.batch(bootstrapStatements(live).map(sql=>db.prepare(sql)));
  await new LiveStore(db,undefined,live).assertReady();
  const [r]=await rows(db,`SELECT (SELECT count(*) FROM ${PREFIX}authority) authority,(SELECT holder FROM ${PREFIX}authority) holder,`+
    `(SELECT count(*) FROM ${PREFIX}agreements) agreements,(SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE '${PREFIX}%') triggers`);
  return {authority:r.authority,holder:r.holder,agreements:r.agreements,triggers:r.triggers};
}

// wrangler is not a dependency of this package (CI never needs it). The operator runs
//   npx --yes --package=node@24 --package=wrangler@4.158.0 -- node scripts/production-d1.mjs apply …
// which puts wrangler's bin on PATH; resolve the package from there.
async function loadWrangler(){
  for(const dir of (process.env.PATH ?? '').split(path.delimiter)){
    const bin=path.join(dir,'wrangler');
    if(!fs.existsSync(bin))continue;
    let root=path.dirname(fs.realpathSync(bin));
    while(root!==path.dirname(root) && !fs.existsSync(path.join(root,'package.json')))root=path.dirname(root);
    const pkg=JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8'));
    if(pkg.name!=='wrangler')continue;
    return import(pathToFileURL(path.join(root,pkg.main)).href);
  }
  throw Error('wrangler_not_on_path: run through npx --package=wrangler@4.158.0');
}
async function applyRemote(config,manifest){
  deploymentPlan(manifest,{profile:'production'});
  const d1=manifest.d1_databases[0];
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cla-prod-d1-')),configPath=path.join(dir,'wrangler.json');
  fs.writeFileSync(configPath,JSON.stringify({name:'tavotto-cla-bootstrap',compatibility_date:manifest.compatibility_date,
    d1_databases:[{binding:'DB',database_name:d1.database_name,database_id:d1.database_id,remote:true}]}));
  const {getPlatformProxy}=await loadWrangler();
  const proxy=await getPlatformProxy({configPath,persist:false,remoteBindings:true});
  try{return await productionBootstrap(proxy.env.DB,config);}
  finally{await proxy.dispose();fs.rmSync(dir,{recursive:true,force:true});}
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
  try{const [cmd,...args]=process.argv.slice(2);const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
    if(cmd==='schema' && args.length===1)process.stdout.write(productionSchemaSQL(read(args[0])));
    else if(cmd==='apply' && args.length===2)console.log(JSON.stringify(await applyRemote(read(args[0]),read(args[1]))));
    else throw Error('usage: node scripts/production-d1.mjs schema CONFIG.json | apply CONFIG.json MANIFEST.json');
  }catch(error){console.error(error.message);process.exitCode=1;}
}
