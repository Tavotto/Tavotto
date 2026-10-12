#!/usr/bin/env node
// Launch gate for the Tavotto support page.  Usage:  node check-config.mjs
// Exit 0 only when every item that must be real and confirmed is real and confirmed.
// It reads the two data files the page ships, in a sandbox, without a browser.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const sandbox = { window: {} };
vm.createContext(sandbox);
for (const f of ['support.config.js', 'acknowledgements.js']) {
  vm.runInContext(readFileSync(join(here, f), 'utf8'), sandbox, { filename: f });
}
const cfg = sandbox.window.TAVOTTO_SUPPORT_CONFIG || {};
const ack = sandbox.window.TAVOTTO_ACKNOWLEDGEMENTS;
const problems = [];
const fail = (msg) => problems.push(msg);

if (cfg.preview !== false) fail('preview must be false for a public page (it shows draft / not-configured markers).');
if (!/^https:\/\/ko-fi\.com\/[A-Za-z0-9_]{2,}\/?$/.test(cfg.kofiUrl || '')) fail('kofiUrl is not a ko-fi.com page URL. Ask the maintainer for the real one; never guess it.');
if (!cfg.maintainer || !cfg.maintainer.name) fail('maintainer.name is empty.');
if (!cfg.maintainer || cfg.maintainer.noteConfirmed !== true) fail('maintainer.noteConfirmed is not true: the maintainer has not confirmed the note.');
if (cfg.fundingConfirmed !== true) fail('fundingConfirmed is not true: "Where your support goes" is unconfirmed wording.');
if (!/^(mailto:\S+@\S+|https:\/\/\S+)$/.test(cfg.acknowledgementsContact || '')) fail('acknowledgementsContact must be a mailto: or https: address people can use to change or remove their name.');
for (const r of cfg.recentWork || []) {
  if (!/^https:\/\/github\.com\/Tavotto\/Tavotto\//.test(r.href || '')) fail(`recentWork ${r.version}: link must point at the Tavotto repository.`);
  if (!r.en || !r.zh) fail(`recentWork ${r.version}: needs both English and Chinese text.`);
}

if (!Array.isArray(ack)) fail('acknowledgements.js must define window.TAVOTTO_ACKNOWLEDGEMENTS as an array.');
else {
  const seen = new Map();
  ack.forEach((r, i) => {
    const at = `acknowledgements[${i}]`;
    const extra = Object.keys(r || {}).filter((k) => k !== 'name' && k !== 'month');
    if (extra.length) fail(`${at}: only "name" and "month" may be public; remove ${extra.join(', ')}.`);
    const name = typeof r?.name === 'string' ? r.name.normalize('NFKC').replace(/\s+/g, ' ').trim() : '';
    if (!name) fail(`${at}: name is empty.`);
    if (/@|\d{6,}/.test(name)) fail(`${at}: "${name}" looks like an e-mail or an ID, not a display name.`);
    if (r?.month !== undefined && !/^\d{4}-(0[1-9]|1[0-2])$/.test(r.month)) fail(`${at}: month must be "YYYY-MM".`);
    const key = name.toLocaleLowerCase();
    if (seen.has(key)) fail(`${at}: "${name}" duplicates acknowledgements[${seen.get(key)}].`);
    else seen.set(key, i);
  });
}

if (problems.length) {
  console.error('Not ready to publish:\n  - ' + problems.join('\n  - '));
  process.exit(1);
}
console.log('Support page configuration is ready to publish.');
