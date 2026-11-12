// The consent wording a signer saw must stay recorded: README's current-version table
// equals the attestation texts shipped in the signing page.
import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';
import {CONSENT_UI_VERSION,CONSENT_UI_VERSIONS} from '../src/native/service.ts';
const ui=fs.readFileSync(new URL('../src/native/ui-client.ts',import.meta.url),'utf8');
const doc=fs.readFileSync(new URL('../../../docs/implementation/cla-consent-ui/README.md',import.meta.url),'utf8');
const keys=['personal','electronic','read','identity','adult','own_rights','independent','reviewed'];
const shipped=lang=>{const from=ui.indexOf(lang+':{');return Object.fromEntries(keys.map(k=>[k,new RegExp('a_'+k+":'([^']*)'").exec(ui.slice(from))[1]]));};
test('the current consent version is recorded with the exact shipped wording',()=>{
  assert.ok(CONSENT_UI_VERSIONS.includes(CONSENT_UI_VERSION));
  const section=doc.split('\n## Version ').find(part=>part.startsWith(CONSENT_UI_VERSION+' '));assert.ok(section,'missing section for version '+CONSENT_UI_VERSION);
  const rows=Object.fromEntries(section.split('\n').filter(line=>/^\| `/.test(line)).map(line=>{const cells=line.split('|').slice(1,-1).map(c=>c.trim());return [cells[0].replaceAll('`',''),cells.slice(1)];}));
  const en=shipped('en'),zh=shipped('zh');
  for(const k of keys)assert.deepEqual(rows[k],[en[k],zh[k]],k);
});
test('every readable consent version has a recorded section',()=>{
  for(const v of CONSENT_UI_VERSIONS)assert.ok(doc.includes('\n## Version '+v+' '),'version '+v);
});
