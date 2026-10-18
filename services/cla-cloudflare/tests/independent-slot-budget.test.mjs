import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { canonical, randomToken } from '../src/core/crypto.ts';
import { LiveStore, nativeSQL, DB_NOW } from '../src/native/store.ts';
import { LiveService } from '../src/native/service.ts';
import { InvocationBudget, FREE_BUDGET } from '../src/native/budget.ts';
import { GitHub } from '../src/native/github.ts';
import { verifyHeadOne } from '../src/native/automatic.ts';
import { reconcileOne } from '../src/native/background.ts';
import { fixture as signedFixture } from './delegation-fixture.mjs';

// Subject: actual outer D1 statements of one fixed-time local invocation. These
// tests never send a publication request, run worker.fetch, or advance a clock.
// Positive proposals stop at the Store's unsent issued journal and settle unknown.
const NOW = 1_800_000_000;
let outbound = 0;
globalThis.fetch = async () => { outbound++; throw Error('OUTBOUND_FORBIDDEN'); };
const CONFIG = {
  origin: 'https://cla.invalid', repositoryId: 707, repository: 'synthetic/slot-budget',
  referenceSha: 'a'.repeat(40), holderId: 1, holderName: 'SYNTHETIC HOLDER',
  appId: 123, appSlug: 'synthetic-slot', checkName: 'synthetic-slot', installationId: 2,
  clientId: 'synthetic', autoHolder: false, syntheticSigning: false, mockCheckPublishing: false,
  githubEnabled: true, oauthEnabled: false, signingEnabled: true, checkPublishingEnabled: true,
  backgroundEnabled: true, automaticVerificationEnabled: true, automaticPublishingEnabled: true,
  confirmationSlotEnabled: true, exemptionIds: { erwanjun: 1 },
  policy: {
    agreements: {
      individual: { path: 'individual', version: '1.0', sha256: 'b'.repeat(64) },
      corporate: { path: 'corporate', version: '1.0', sha256: 'c'.repeat(64) },
    },
    exemptions: [{ login: 'erwanjun', kind: 'rights_holder', reason: 'synthetic' }],
    provider: { configured: true, protocol: 'tavotto-cla-v1', app_id: 123, app_slug: 'synthetic-slot', check_name: 'synthetic-slot' },
  },
};
const key = pr => `pr:707:${pr}`;
function proposal(pr = 9, { revision = 0, head = 'd'.repeat(40), expired = false } = {}) {
  const proof = {
    protocol: 'tavotto-cla-v1', repository_id: 707, kind: 'pr', pr, head_sha: head,
    base_sha: 'e'.repeat(40), members: [], contributors: [], policy_sha256: 'f'.repeat(64), revision,
    issued_at: expired ? NOW - 120 : NOW, expires_at: expired ? NOW : NOW + 120,
  };
  return { name: CONFIG.checkName, appId: CONFIG.appId, appSlug: CONFIG.appSlug,
    headSha: head, conclusion: 'success', proof, text: 'tavotto-cla-v1\n' + canonical(proof) };
}

async function fixture({ persistent = false } = {}) {
  const directory = persistent ? fs.mkdtempSync(path.join(os.tmpdir(), 'cla-slot-budget-')) : null;
  const filename = directory ? path.join(directory, 'journal.sqlite') : ':memory:';
  let sqlite;
  const adapt = query => query.replaceAll("strftime('%s','now')", 'independent_now()');
  const open = () => {
    sqlite = new DatabaseSync(filename);
    sqlite.exec('PRAGMA foreign_keys=ON');
    sqlite.function('independent_now', () => NOW);
  };
  open();
  for (const filename of ['base.sql', 'native/schema.sql']) {
    sqlite.exec(adapt(nativeSQL(fs.readFileSync(new URL('../src/' + filename, import.meta.url), 'utf8'))));
  }
  const execute = (query, values) => {
    const statement = sqlite.prepare(adapt(query));
    const result = statement.columns().length ? statement.all(...values) : statement.run(...values);
    return { success: true, results: Array.isArray(result) ? result.map(row => ({ ...row })) : [] };
  };
  const prepare = (query, values = []) => ({
    bind: (...next) => prepare(query, next),
    first: async () => execute(query, values).results[0] ?? null,
    all: async () => execute(query, values), run: async () => execute(query, values),
    execute: () => execute(query, values),
  });
  const db = { prepare, batch: async statements => {
    sqlite.exec('BEGIN IMMEDIATE');
    try { const results = statements.map(statement => statement.execute()); sqlite.exec('COMMIT'); return results; }
    catch (error) { sqlite.exec('ROLLBACK'); throw error; }
  } };
  const run = (query, ...values) => sqlite.prepare(adapt(nativeSQL(query))).run(...values);
  const first = (query, ...values) => {
    const row = sqlite.prepare(adapt(nativeSQL(query))).get(...values); return row ? { ...row } : null;
  };
  const all = (query, ...values) => sqlite.prepare(adapt(nativeSQL(query))).all(...values).map(row => ({ ...row }));
  const c = structuredClone(CONFIG);
  await new LiveStore(db).initialize(c);
  const invocation = (enabled = true) => {
    const budget = new InvocationBudget(() => 0, () => NOW * 1000);
    return { budget, store: new LiveStore(db, budget, { ...c, confirmationSlotEnabled: enabled }) };
  };
  const target = (pr = 9) => run('INSERT OR IGNORE INTO targets VALUES(?,?)', key(pr), canonical({ kind: 'pr', number: pr }));
  const enqueue = (pr = 9, due = 0) => {
    target(pr);
    run(`INSERT INTO outbox(target_key,wanted_revision,due) VALUES(?,(SELECT revision FROM state WHERE id=1),?)
      ON CONFLICT(target_key) DO UPDATE SET due=excluded.due,lease='',lease_until=0`, key(pr), due);
  };
  const head = (pr = 9, check = proposal(pr), status = 'running') => {
    const job = { request_key: `707:${pr}:${check.headSha}`, pr, head: check.headSha, attempt: randomToken() };
    run('INSERT INTO head_requests VALUES(?,?,?,?,?,?,?,?)', job.request_key, pr, job.head, status,
      status === 'running' ? job.attempt : '', status === 'running' ? NOW + 120 : 0, NOW, '');
    return job;
  };
  const source = (pr = 9, overrides = {}) => {
    target(pr); const check = proposal(pr, overrides), id = randomToken();
    run(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text)
      VALUES(?,?,?,?,?,'resolved',101,?,?,'failure',?)`, id, key(pr), check.headSha, check.proof.revision, check.proof.expires_at, NOW, NOW, check.text);
    return first('SELECT * FROM publication_attempts WHERE id=?', id);
  };
  const pending = (pr, predecessor, attempts = 0) => {
    run("INSERT INTO refresh_requests VALUES(?,?,(SELECT revision FROM state WHERE id=1),'pending',?,?,'reconciliation_resolved')",
      key(pr), predecessor.id, attempts, NOW); enqueue(pr);
  };
  // Static pre-existing owner snapshots only: expiry is already at the fixed
  // instant. No successful dispatch, confirmation, or time transition occurs.
  const historicalOwner = (pr = 9, { phase = 'confirmed', liveFence = false } = {}) => {
    target(pr); const check = proposal(pr, { expired: true }), id = randomToken();
    run('INSERT INTO _guards VALUES(?,1)', 'confirmation-slot:' + id);
    run(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text,send_until)
      VALUES(?,?,?,?,?,'issued',101,?,?,'success',?,?)`, id, key(pr), check.headSha, 0, check.proof.expires_at, NOW - 120, NOW, check.text, NOW + 120);
    if (!liveFence) run("UPDATE publication_attempts SET phase='unknown',send_until=0 WHERE id=?", id);
    if (phase !== 'issued') run('UPDATE publication_attempts SET phase=? WHERE id=?', phase, id);
    enqueue(pr); return id;
  };
  return { db, c, run, first, all, invocation, target, enqueue, head, source, pending, historicalOwner,
    restart: () => { assert.ok(directory); sqlite.close(); open(); },
    close: () => { sqlite.close(); if (directory) fs.rmSync(directory, { recursive: true, force: true }); } };
}
async function isolated(body, options) {
  const f = await fixture(options); try { await body(f); } finally { f.close(); }
  assert.equal(outbound, 0);
}
function negativeAdapter(f, current, p) {
  const requests = [], run = { id: p.run_id ?? 101, app: { id: f.c.appId, slug: f.c.appSlug }, name: f.c.checkName,
    head_sha: p.head_sha, external_id: p.id, status: 'completed', conclusion: 'failure' };
  const github = new GitHub({ ...f.c, confirmationSlotEnabled: current.store.expectedConfig.confirmationSlotEnabled },
    { clientSecret: '', pkcs8: '', evidenceKey: '' }, async (url, init) => {
      assert.equal(init.method, 'GET', 'the only adapter transport is a synthetic read');
      const pathname = new URL(url).pathname; requests.push(pathname);
      if (pathname.endsWith('/check-runs/' + run.id)) return Response.json(run);
      assert.equal(pathname, `/repos/${f.c.repository}/commits/${p.head_sha}/check-runs`);
      return Response.json({ total_count: 1, check_runs: [run] });
    }, () => NOW, current.budget);
  github.installationToken = async () => 'SYNTHETIC_READ_ONLY';
  return { github, requests };
}
function ledger(budget) {
  const stages = [];
  return { stages, step: async (name, operation) => {
    const before = budget.snapshot().sql, result = await operation();
    stages.push({ name, sql: budget.snapshot().sql - before, cumulative: budget.snapshot().sql }); return result;
  } };
}
async function qualificationFixture() {
  const f = await signedFixture(); await f.install(); const packet = await f.create(); await f.sign(packet);
  Object.assign(f.c, { githubEnabled: true, backgroundEnabled: true, checkPublishingEnabled: true,
    automaticVerificationEnabled: true, automaticPublishingEnabled: true, confirmationSlotEnabled: true });
  const budget = new InvocationBudget(() => 0, () => NOW * 1000), store = new LiveStore(f.db, budget, f.c);
  const github = { ...f.gh, budget };
  for (const name of ['publish', 'publishNew', 'publishFresh', 'observe', 'withdraw']) github[name] = async () => { throw Error(name + '_FORBIDDEN'); };
  return { ...f, budget, store, service: new LiveService(f.c, store, github, f.key), ...ledger(budget) };
}
function measured(t, scenario, current, work, expectedWork, expectedTotal) {
  assert.equal(work, expectedWork); assert.equal(current.budget.snapshot().sql, expectedTotal);
  assert.ok(work <= FREE_BUDGET.sqlWork); assert.ok(current.budget.snapshot().sql <= FREE_BUDGET.sql);
  t.diagnostic(JSON.stringify({ scenario, work_sql: work, total_sql: current.budget.snapshot().sql, stages: current.stages }));
}

test('slot new-head one delegated person actual issued and unknown settlement SQL', async t => {
  const f = await qualificationFixture();
  try {
    const request_key = `707:9:${f.gh.head}`, attempt = randomToken();
    // Fixture setup SQL is outside the invocation's meter.
    await new LiveStore(f.db).stmt("INSERT INTO head_requests VALUES(?,?,?,'pending','',0,?,'')", request_key, 9, f.gh.head, NOW).run();
    await f.step('readiness', () => f.store.assertReady());
    await f.step('expired head cleanup', () => f.store.stmt(`UPDATE head_requests SET status='manual',result='' WHERE status='running' AND lease_until<=${DB_NOW}`).run());
    const result = await f.step('head claim', () => f.store.db.batch([f.store.stmt(`UPDATE head_requests SET status='running',attempt=?,lease_until=${DB_NOW}+120
      WHERE request_key=(SELECT request_key FROM head_requests WHERE status='pending' AND (${f.store.positiveAdmission()}) ORDER BY created,request_key LIMIT 1)
      AND status='pending' RETURNING *`, attempt)]));
    const check = await f.step('real delegated qualification', () => f.service.evaluate(9, 8));
    assert.equal(check.conclusion, 'success'); assert.equal(check.proof.contributors.length, 1);
    await f.service.freshBinding(9); await f.step('final clock', () => f.store.databaseNow());
    const id = await f.step('atomic unsent issued journal', () => f.store.beginAutomaticPublication(result[0].results[0], check));
    const work = f.budget.snapshot().sql;
    assert.equal((await new LiveStore(f.db).latestPublication('pr:707:9')).confirmation_slot, 1);
    f.budget.settlement(); await f.step('unknown settlement', () => f.store.settleAutomaticPublication(id, 'unknown'));
    measured(t, 'new-head delegated N=1, no publication', f, work, 17, 21);
  } finally { f.close(); }
});

test('slot same-head one delegated person actual issued and unknown settlement SQL', async t => {
  const f = await qualificationFixture(), seed = new LiveStore(f.db);
  try {
    const check = await new LiveService(f.c, seed, f.gh, f.key).evaluate(9), id = randomToken(), target = 'pr:707:9';
    await seed.track(707, { kind: 'pr', number: 9 });
    await seed.stmt(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text)
      VALUES(?,?,?,?,?,'resolved',101,?,?,'failure',?)`, id, target, check.headSha, check.proof.revision, check.proof.expires_at, NOW, NOW, check.text).run();
    await seed.stmt("INSERT INTO refresh_requests VALUES(?,?,?,'pending',0,?,'reconciliation_resolved')", target, id, check.proof.revision, NOW).run();
    await seed.enqueue(target);
    await f.step('readiness/revision', () => f.store.revision());
    const job = await f.step('target claim', () => f.store.claim(0)); f.budget.bindLease(job.lease_until, job.lease_clock);
    const request = await f.step('refresh decision', () => f.store.refreshRequest(job)); await f.step('target read', () => f.store.target(target));
    const fresh = await f.step('real delegated qualification', () => f.service.evaluate(9, 7));
    const predecessor = await f.step('exact resolved predecessor', () => f.store.refreshPredecessor(target, fresh.headSha));
    await f.service.freshBinding(9); await f.step('final clock', () => f.store.databaseNow());
    const issued = await f.step('atomic unsent fresh journal', () => f.store.beginFreshPublication(job, request, fresh, predecessor));
    const work = f.budget.snapshot().sql, row = await seed.latestPublication(target);
    assert.equal(row.send_until, NOW + 120); assert.ok(row.send_until - NOW >= FREE_BUDGET.cleanupMs / 1000);
    assert.equal(row.confirmation_slot, 1); assert.equal(row.run_id, 101);
    f.budget.settlement(); await f.step('unknown settlement', () => f.store.settleFreshPublication(job, issued, 'unknown'));
    assert.equal((await seed.latestPublication(target)).send_until, 0);
    measured(t, 'same-head delegated N=1, no publication', f, work, 17, 21);
  } finally { f.close(); }
});

test('slot two different-target issuing transactions commit exactly one owner', () => isolated(async f => {
  const a = f.invocation(), b = f.invocation(), ca = proposal(9), cb = proposal(10, { head: 'a'.repeat(40) });
  const ja = f.head(9, ca), jb = f.head(10, cb);
  const results = await Promise.allSettled([a.store.beginAutomaticPublication(ja, ca), b.store.beginAutomaticPublication(jb, cb)]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  assert.equal(results.filter(result => result.status === 'rejected').length, 1);
  const rows = f.all('SELECT * FROM publication_attempts'); assert.equal(rows.length, 1);
  assert.equal(f.first('SELECT active_attempt FROM slot_policy').active_attempt, rows[0].id);
  assert.equal(f.first("SELECT count(*) n FROM head_requests WHERE status='dispatched'").n, 1);
  assert.equal(f.first("SELECT count(*) n FROM head_requests WHERE status='running'").n, 1);
  assert.equal(f.first('SELECT count(*) n FROM outbox').n, 1);
  assert.equal(f.first("SELECT count(*) n FROM _guards WHERE id LIKE 'confirmation-slot:%'").n, 0);
}));

test('slot busy queues stay pending without consuming refresh retries', () => isolated(async f => {
  f.historicalOwner(); const predecessor = f.source(10); f.pending(10, predecessor, 2);
  const check = proposal(11); const pendingHead = f.head(11, check, 'pending');
  const current = f.invocation(); let forbidden = 0;
  const service = { c: f.c, store: current.store, github: { budget: current.budget },
    evaluate: async () => { forbidden++; throw Error('EVALUATION_WHILE_BUSY_FORBIDDEN'); } };
  assert.equal(await verifyHeadOne(service), 'idle');
  assert.equal(f.first('SELECT status FROM head_requests WHERE request_key=?', pendingHead.request_key).status, 'pending');
  assert.equal(forbidden, 0);
  // Claim the waiting target independently of the higher-priority owner.
  f.run('UPDATE outbox SET due=? WHERE target_key=?', NOW + 1, key(9));
  const waiting = f.invocation(), job = await waiting.store.claim(0), request = await waiting.store.refreshRequest(job);
  assert.equal(job.target_key, key(10)); assert.equal(request.capacity_blocked, true);
  waiting.budget.settlement(); await waiting.store.deferRefreshCapacity(job, request);
  let row = f.first('SELECT * FROM refresh_requests WHERE target_key=?', key(10));
  assert.equal(row.status, 'pending'); assert.equal(row.attempts, 2); assert.equal(row.reason, 'confirmation_slot_wait');
  f.run('UPDATE refresh_requests SET due=? WHERE target_key=?', NOW, key(10)); f.enqueue(10);
  const raced = f.invocation(), next = await raced.store.claim(0), reread = await raced.store.refreshRequest(next);
  raced.budget.settlement(); await raced.store.failRefresh(next, reread);
  row = f.first('SELECT * FROM refresh_requests WHERE target_key=?', key(10));
  assert.equal(row.status, 'pending'); assert.equal(row.attempts, 2); assert.equal(row.reason, 'confirmation_slot_wait');
}));

test('slot withdrawal has target and within-target priority over older ordinary work', () => isolated(async f => {
  const older = f.source(9, { head: 'a'.repeat(40) });
  f.run("UPDATE publication_attempts SET phase='reconcile' WHERE id=?", older.id);
  const owner = f.historicalOwner();
  f.enqueue(8, 0); f.run('UPDATE outbox SET due=? WHERE target_key=?', NOW, key(9));
  const current = f.invocation(), revision = await current.store.revision(), job = await current.store.claim(0);
  assert.equal(job.target_key, key(9));
  const progress = await current.store.openProgress(job, revision), records = await current.store.publicationBatch(job, progress);
  assert.deepEqual(records.map(row => row.id), [owner]); assert.equal(records[0].expires, NOW);
  assert.equal(records[0].confirmation_slot, 1);
}));

for (const phase of ['issued', 'unknown', 'reconcile', 'confirmed', 'manual']) {
  test('slot expired ' + phase + ' owner retains every-phase live send fence', () => isolated(async f => {
    const owner = f.historicalOwner(9, { phase, liveFence: true }), current = f.invocation();
    assert.equal(await current.store.claim(0), null);
    assert.equal(f.first('SELECT active_attempt FROM slot_policy').active_attempt, owner);
    const lease = randomToken(); f.run('UPDATE outbox SET lease=?,lease_until=? WHERE target_key=?', lease, NOW + 120, key(9));
    const job = { ...f.first('SELECT * FROM outbox WHERE target_key=?', key(9)), lease_clock: NOW };
    const progress = await current.store.openProgress(job, 0);
    assert.deepEqual(await current.store.publicationBatch(job, progress), []);
    const p = await current.store.latestPublication(key(9)); current.budget.settlement();
    await assert.rejects(current.store.recordReconciliation(job, p, 'manual', 101, 'must_not_terminalize_live_sender', 0),
      error => error.code === 'transaction_rejected');
    assert.equal(f.first('SELECT phase FROM publication_attempts WHERE id=?', owner).phase, phase);
  }));
}

test('slot restart flagfalse fences new positives and recovers exact owner by GET witness', t => isolated(async f => {
  const owner = f.historicalOwner(); f.source(9, { head: 'a'.repeat(40) });
  const pendingHead = f.head(10, proposal(10), 'pending'); f.restart();
  const current = f.invocation(false), p = (await current.store.latestPublication(key(9)));
  assert.equal(p.conclusion, 'failure', 'the globally newest row is a distinct historical failure');
  assert.equal(p.confirmation_slot, null);
  let evaluations = 0;
  assert.equal(await verifyHeadOne({ c: { ...f.c, confirmationSlotEnabled: false }, store: current.store,
    github: { budget: current.budget }, evaluate: async () => { evaluations++; throw Error('UNLATCHED_EVALUATION_FORBIDDEN'); } }), 'idle');
  assert.equal(evaluations, 0); assert.equal(f.first('SELECT status FROM head_requests WHERE request_key=?', pendingHead.request_key).status, 'pending');
  const legacy = proposal(10), legacyJob = f.head(12, proposal(12));
  await assert.rejects(f.invocation(false).store.beginAutomaticPublication(legacyJob, proposal(12)), error => error.code === 'transaction_rejected');
  f.target(10);
  assert.throws(() => f.run(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,created,updated,conclusion,proof_text)
    VALUES(?,?,?,?,?,'issued',?,?,'success',?)`, randomToken(), key(10), legacy.headSha, 0, NOW + 120, NOW, NOW, legacy.text), /slot_required/);
  assert.throws(() => f.run("UPDATE publication_attempts SET phase='resolved' WHERE id=?", owner), /fresh_negative_release_required/);
  const recovery = f.invocation(false), held = { ...f.first('SELECT * FROM publication_attempts WHERE id=?', owner), confirmation_slot: 1 };
  const { github, requests } = negativeAdapter(f, recovery, held);
  const result = await reconcileOne({ c: { ...f.c, confirmationSlotEnabled: false }, store: recovery.store, github,
    evaluate: async () => { throw Error('NEGATIVE_RECOVERY_REQUALIFICATION_FORBIDDEN'); } });
  assert.equal(result.disposition, 'remote_failure_observed'); assert.equal(requests.length, 2);
  assert.deepEqual(result.budget, { sql: 27, external: 2 });
  t.diagnostic(JSON.stringify({ scenario: 'actual negative GET recovery, flagfalse after restart', total_sql: 27, synthetic_gets: 2 }));
  const row = f.first('SELECT * FROM publication_attempts WHERE id=?', owner), release = JSON.parse(row.observation);
  assert.equal(row.phase, 'resolved'); assert.equal(row.proof_text, held.proof_text); assert.equal(row.expires, held.expires);
  assert.equal(release.attempt, owner); assert.equal(release.original_revision, held.revision); assert.equal(release.old_run_id, 101);
  assert.equal(f.first('SELECT active_attempt FROM slot_policy').active_attempt, null);
  assert.equal(f.first('SELECT first_attempt FROM slot_policy').first_attempt, owner);
  assert.throws(() => f.run('DELETE FROM slot_policy'), /slot_policy_immutable/);
  assert.throws(() => f.run('INSERT OR REPLACE INTO slot_policy SELECT * FROM slot_policy'), /slot_policy_enrollment_required/);
  assert.throws(() => f.run("UPDATE publication_attempts SET observation='discard' WHERE id=?", owner), /slot_release_immutable/);
  assert.throws(() => f.run('DELETE FROM publication_attempts WHERE id=?', owner), /slot_history_protected/);
  assert.throws(() => f.run(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,created,updated,conclusion,proof_text)
    VALUES(?,?,?,?,?,'issued',?,?,'success',?)`, randomToken(), key(10), legacy.headSha, 0, NOW + 120, NOW, NOW, legacy.text), /slot_required/);
}, { persistent: true }));

test('slot unknown observation becomes manual while retaining durable owner', () => isolated(async f => {
  const owner = f.historicalOwner(9, { phase: 'unknown' }), current = f.invocation();
  const result = await reconcileOne({ c: f.c, store: current.store, github: { budget: current.budget, observe: async () => null } });
  assert.equal(result.disposition, 'manual_review_required');
  assert.equal(f.first('SELECT active_attempt FROM slot_policy').active_attempt, owner);
  assert.equal(f.first('SELECT phase FROM publication_attempts WHERE id=?', owner).phase, 'manual');
  assert.equal(f.first('SELECT count(*) n FROM outbox WHERE target_key=?', key(9)).n, 1);
}));

test('slot conservative full qualification and withdrawal authorization prefix stays within SQL ceilings', async t => {
  const f = await qualificationFixture(), seed = new LiveStore(f.db);
  try {
    const check = await new LiveService(f.c, seed, f.gh, f.key).evaluate(9), target = 'pr:707:9', owner = randomToken();
    await seed.track(707, { kind: 'pr', number: 9 });
    await seed.stmt('INSERT INTO _guards VALUES(?,1)', 'confirmation-slot:' + owner).run();
    await seed.stmt(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text,send_until)
      VALUES(?,?,?,?,?,'issued',101,?,?,'success',?,?)`, owner, target, check.headSha, check.proof.revision, check.proof.expires_at, NOW, NOW, check.text, NOW + 120).run();
    await seed.stmt("UPDATE publication_attempts SET phase='unknown',send_until=0 WHERE id=?", owner).run(); await seed.enqueue(target);
    const revision = await f.step('readiness/revision', () => f.store.revision());
    const job = await f.step('target claim', () => f.store.claim(0)); f.budget.bindLease(job.lease_until, job.lease_clock);
    await f.step('refresh decision', () => f.store.refreshRequest(job)); await f.step('target read', () => f.store.target(target));
    const progress = await f.step('progress open/read', () => f.store.openProgress(job, revision));
    const [p] = await f.step('owner select', () => f.store.publicationBatch(job, progress));
    await f.step('expiry clock prefix', () => f.store.databaseNow());
    await f.step('full real delegated qualification prefix', () => f.service.evaluate(9));
    await f.step('withdrawal lease authorization prefix', () => f.store.owns(job, 0));
    await f.step('withdrawal revision authorization prefix', () => f.store.revision());
    // This deliberately sums the conservative prefix with a negative GET path.
    // Actual negative-observation recovery skips qualification and these reads.
    const { github, requests } = negativeAdapter({ c: f.c }, { store: f.store, budget: f.budget }, p);
    const negative = await github.observe(p, { job, revision }); assert.equal(requests.length, 2);
    const work = f.budget.snapshot().sql; f.budget.settlement();
    await f.step('fresh negative witness release', () => f.store.recordReconciliation(job, p, 'resolved', 101, 'negative_read', revision, null,
      { source: github, witness: negative.negativeWitness }));
    await f.step('finish pass', () => f.store.finishBatch(job, revision));
    measured(t, 'conservative qualification + authorization prefix + negative release, no publication', f, work, 19, 36);
  } finally { f.close(); }
});

test('slot suite performs zero outbound requests', () => assert.equal(outbound, 0));
