import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { canonical } from '../src/core/crypto.ts';
import { LiveStore, nativeSQL } from '../src/native/store.ts';
import { FREE_BUDGET, InvocationBudget } from '../src/native/budget.ts';
import { reconcileOne } from '../src/native/background.ts';
import { GitHub } from '../src/native/github.ts';

// Independent rejection/state-machine units only. All source journals and all
// proposals are failures. No existing test module is imported or registered.
// These tests do not exercise the refresh driver's qualified-publication branch.
let outbound = 0;
globalThis.fetch = async () => {
  outbound++;
  throw new Error('OUTBOUND_FORBIDDEN');
};
const NOW = 1_800_000_000;
const HEAD = 'a'.repeat(40);
const OTHER_HEAD = 'b'.repeat(40);
const KEY = 'pr:7002:17';
const CONFIG = Object.freeze({
  backgroundEnabled: true, checkPublishingEnabled: true,
  repositoryId: 7002, repository: 'synthetic/refresh-boundaries',
  appId: 7001, appSlug: 'synthetic-refresh', checkName: 'synthetic-refresh',
});

function failureProposal(revision = 0, head = HEAD) {
  const proof = {
    protocol: 'tavotto-cla-v1', repository_id: CONFIG.repositoryId,
    kind: 'pr', pr: 17, head_sha: head, base_sha: 'c'.repeat(40),
    members: [], contributors: [], policy_sha256: 'd'.repeat(64),
    revision, issued_at: NOW, expires_at: NOW + 120,
  };
  return {
    name: CONFIG.checkName, appId: CONFIG.appId, appSlug: CONFIG.appSlug,
    headSha: head, conclusion: 'failure', proof,
    text: 'tavotto-cla-v1\n' + canonical(proof),
  };
}

// Minimal real SQLite implementation of the D1 operations used by LiveStore.
// It intentionally copies no lifecycle/signing fixture or account information.
function fixture({ persistent = false } = {}) {
  let now = NOW;
  const directory = persistent ? fs.mkdtempSync(path.join(os.tmpdir(), 'cla-refresh-negative-')) : null;
  const file = directory ? path.join(directory, 'journal.sqlite') : ':memory:';
  let sqlite;
  const events = [];
  const forbiddenCalls = [];
  const adapt = sql => sql.replaceAll("strftime('%s','now')", 'independent_now()');
  function open() {
    sqlite = new DatabaseSync(file);
    sqlite.exec('PRAGMA foreign_keys=ON');
    sqlite.function('independent_now', () => now);
  }
  open();
  for (const name of ['base.sql', 'native/schema.sql']) {
    sqlite.exec(adapt(nativeSQL(fs.readFileSync(new URL('../src/' + name, import.meta.url), 'utf8'))));
  }
  const run = (query, ...values) => sqlite.prepare(adapt(nativeSQL(query))).run(...values);
  const first = (query, ...values) => {
    const row = sqlite.prepare(adapt(nativeSQL(query))).get(...values);
    return row ? { ...row } : null;
  };
  const all = (query, ...values) => sqlite.prepare(adapt(nativeSQL(query))).all(...values).map(row => ({ ...row }));
  function execute(query, values) {
    const statement = sqlite.prepare(adapt(query));
    const result = statement.columns().length ? statement.all(...values) : statement.run(...values);
    return {
      success: true, results: Array.isArray(result) ? result.map(row => ({ ...row })) : [],
      meta: { changes: Number(sqlite.prepare('SELECT changes() AS count').get().count) },
    };
  }
  const prepare = (query, values = []) => ({
    bind: (...next) => prepare(query, next),
    first: async () => execute(query, values).results[0] ?? null,
    all: async () => execute(query, values),
    run: async () => execute(query, values),
    execute: () => execute(query, values),
  });
  const db = {
    prepare,
    batch: async statements => {
      sqlite.exec('BEGIN IMMEDIATE');
      try {
        const results = statements.map(statement => statement.execute());
        sqlite.exec('COMMIT');
        events.push('commit');
        return results;
      } catch (error) {
        sqlite.exec('ROLLBACK');
        events.push('rollback');
        throw error;
      }
    },
  };
  run('INSERT INTO targets VALUES(?,?)', KEY, canonical({ kind: 'pr', number: 17 }));

  function source({ id = 'synthetic-failure-source', head = HEAD, phase = 'resolved', revision = 0 } = {}) {
    const check = failureProposal(revision, head);
    run(`INSERT INTO publication_attempts
      (id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text,observation)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`, id, KEY, head, revision, check.proof.expires_at,
    phase, 7003, now, now, 'failure', check.text, 'synthetic_failure_observed');
    return first('SELECT * FROM publication_attempts WHERE id=?', id);
  }
  function enqueue() {
    run(`INSERT INTO outbox(target_key,wanted_revision,due) VALUES(?,(SELECT revision FROM state WHERE id=1),0)
      ON CONFLICT(target_key) DO UPDATE SET due=0,lease='',lease_until=0`, KEY);
  }
  function pending(predecessor, overrides = {}) {
    const request = {
      target_key: KEY, source_attempt: predecessor.id, revision: 0,
      status: 'pending', attempts: 0, due: now, reason: 'reconciliation_resolved', ...overrides,
    };
    run('INSERT INTO refresh_requests VALUES(?,?,?,?,?,?,?)', request.target_key, request.source_attempt,
      request.revision, request.status, request.attempts, request.due, request.reason);
    enqueue();
    return request;
  }
  function invocation() {
    let elapsed = 0;
    const budget = new InvocationBudget(() => elapsed, () => now * 1000);
    const store = new LiveStore(db, budget);
    const sends = [];
    const github = {
      budget,
      publish: async (check, attempt) => {
        // This is a failure-only, in-memory transport for the journal unit.
        assert.equal(check.conclusion, 'failure');
        budget.externalSignal();
        assert.equal(first('SELECT phase FROM publication_attempts WHERE id=?', attempt)?.phase, 'issued');
        assert.equal(events.at(-1), 'commit', 'send callback sees the committed issued transaction');
        events.push('memory_failure_send');
        sends.push({ attempt, conclusion: check.conclusion });
        return { id: 7003, appId: CONFIG.appId, appSlug: CONFIG.appSlug, name: CONFIG.checkName, headSha: HEAD };
      },
      publishFresh: async () => { forbiddenCalls.push('publishFresh'); throw new Error('DRIVER_PUBLICATION_BRANCH_FORBIDDEN'); },
      observe: async () => { forbiddenCalls.push('observe'); throw new Error('DRIVER_RECONCILIATION_BRANCH_FORBIDDEN'); },
      withdraw: async () => { forbiddenCalls.push('withdraw'); throw new Error('DRIVER_WITHDRAWAL_BRANCH_FORBIDDEN'); },
    };
    return { store, budget, github, sends, setElapsed: value => { elapsed = value; } };
  }
  return {
    run, first, all, events, forbiddenCalls, source, enqueue, pending, invocation,
    now: () => now,
    advanceLeaseClock: seconds => { now += seconds; },
    restart: () => { assert.ok(directory, 'restart requires a disk-backed fixture'); sqlite.close(); open(); },
    close: () => { sqlite.close(); if (directory) fs.rmSync(directory, { recursive: true, force: true }); },
  };
}

async function isolated(body, options) {
  const f = fixture(options);
  try {
    await body(f);
    assert.deepEqual(f.forbiddenCalls, [], 'no forbidden driver branch was reached');
  } finally { f.close(); }
  assert.equal(outbound, 0);
}
function requestRow(f) { return f.first('SELECT * FROM refresh_requests WHERE target_key=?', KEY); }
function publicationRows(f) { return f.all('SELECT * FROM publication_attempts ORDER BY rowid'); }
async function claimed(f) {
  const invocation = f.invocation();
  const job = await invocation.store.claim(0);
  assert.ok(job);
  invocation.budget.bindLease(job.lease_until, job.lease_clock);
  return { ...invocation, job };
}
async function finishResolvedPass(f) {
  const current = await claimed(f);
  const revision = await current.store.revision();
  const progress = await current.store.openProgress(current.job, revision);
  assert.deepEqual(await current.store.publicationBatch(current.job, progress), []);
  current.budget.settlement();
  await current.store.finishBatch(current.job, revision);
  assert.equal(current.sends.length, 0);
}

// Durable refresh-request deduplication is tested directly at the store boundary.
test('refresh tuple survives SQLite close/reopen and completed tuple does not replay', () => isolated(async f => {
  const predecessor = f.source();
  f.enqueue();
  await finishResolvedPass(f);
  const initial = requestRow(f);
  assert.equal(initial.status, 'pending');
  assert.equal(initial.source_attempt, predecessor.id);
  f.restart();
  assert.deepEqual(requestRow(f), initial);
  f.enqueue();
  await finishResolvedPass(f);
  assert.deepEqual(requestRow(f), initial, 'same source/revision does not reset the pending request');
  f.enqueue();
  const current = await claimed(f);
  const request = await current.store.refreshRequest(current.job);
  assert.ok(request);
  current.budget.settlement();
  await current.store.completeRefresh(current.job, request, 'qualification_not_complete');
  const done = requestRow(f);
  assert.equal(done.status, 'done');
  f.restart();
  f.enqueue();
  await finishResolvedPass(f);
  assert.deepEqual(requestRow(f), done, 'completed source/revision stays consumed after restart');
  assert.equal(f.first('SELECT * FROM outbox WHERE target_key=?', KEY), null);
  assert.deepEqual(publicationRows(f), [predecessor]);
}, { persistent: true }));

test('refresh deduplication admits changed revision or changed failure source exactly once', () => isolated(async f => {
  const original = f.source();
  f.pending(original, { status: 'done', reason: 'qualification_not_complete' });
  f.run('UPDATE state SET revision=revision+1 WHERE id=1');
  await finishResolvedPass(f);
  const revised = requestRow(f);
  assert.equal(revised.revision, 1);
  assert.equal(revised.source_attempt, original.id);
  assert.equal(revised.status, 'pending');
  const replacement = f.source({ id: 'synthetic-new-failure-source', revision: 1 });
  f.enqueue();
  await finishResolvedPass(f);
  const replaced = requestRow(f);
  assert.equal(replaced.source_attempt, replacement.id);
  assert.equal(replaced.revision, 1);
  f.enqueue();
  await finishResolvedPass(f);
  assert.deepEqual(requestRow(f), replaced);
  assert.equal(publicationRows(f).length, 2);
}));

test('manual refresh exhaustion stays blocked across newer revision and failure source', () => isolated(async f => {
  const predecessor = f.source();
  f.pending(predecessor, { status: 'manual', attempts: 3, reason: 'refresh_retry_exhausted' });
  const before = requestRow(f);
  f.run('UPDATE state SET revision=revision+1 WHERE id=1');
  f.source({ id: 'synthetic-later-failure-source', revision: 1 });
  await finishResolvedPass(f);
  assert.deepEqual(requestRow(f), before);
  assert.equal(f.first('SELECT * FROM outbox WHERE target_key=?', KEY), null);
}));

const casMutations = {
  'lease token': (f, current) => f.run("UPDATE outbox SET lease='synthetic-replacement-lease' WHERE target_key=?", KEY),
  'lease deadline': f => f.advanceLeaseClock(120),
  generation: f => f.run('UPDATE outbox SET generation=generation+1 WHERE target_key=?', KEY),
  'global revision': (f, current) => {
    f.run('UPDATE state SET revision=revision+1 WHERE id=1');
    // Isolate revision CAS from the generation increment performed by its trigger.
    f.run('UPDATE outbox SET generation=? WHERE target_key=?', current.job.generation, KEY);
  },
  'request revision': f => f.run('UPDATE refresh_requests SET revision=revision+1 WHERE target_key=?', KEY),
  'request attempts': f => f.run('UPDATE refresh_requests SET attempts=attempts+1 WHERE target_key=?', KEY),
  'request due': f => f.run('UPDATE refresh_requests SET due=due+1 WHERE target_key=?', KEY),
  'request reason': f => f.run("UPDATE refresh_requests SET reason='synthetic-changed-reason' WHERE target_key=?", KEY),
  'request status': f => f.run("UPDATE refresh_requests SET status='done' WHERE target_key=?", KEY),
};
for (const [label, mutate] of Object.entries(casMutations)) {
  test('refresh completion/retry guards reject stale ' + label, () => isolated(async f => {
    const predecessor = f.source();
    const request = f.pending(predecessor);
    const current = await claimed(f);
    mutate(f, current);
    const before = publicationRows(f);
    const requestBefore = requestRow(f);
    await assert.rejects(current.store.completeRefresh(current.job, request, 'qualification_not_complete'),
      error => error.code === 'transaction_rejected');
    await assert.rejects(current.store.failRefresh(current.job, request),
      error => error.code === 'transaction_rejected');
    assert.deepEqual(publicationRows(f), before);
    assert.deepEqual(requestRow(f), requestBefore);
    assert.equal(current.sends.length, 0);
  }));
}

for (const phase of ['issued', 'unknown', 'reconcile', 'manual', 'confirmed']) {
  test('older ' + phase + ' failure source blocks refresh completion/retry for the entire target', () => isolated(async f => {
    // The blocker is older, so the resolved source remains globally newest.
    // The confirmed case is historical on the same head; all others use another
    // head. Rejection cannot be explained only by the latest-source CAS.
    f.source({ id: 'synthetic-older-blocker', head: phase === 'confirmed' ? HEAD : OTHER_HEAD, phase });
    const predecessor = f.source();
    const request = f.pending(predecessor);
    const current = await claimed(f);
    assert.equal(await current.store.refreshRequest(current.job), null);
    assert.equal((await current.store.refreshPredecessor(KEY, HEAD)).id, predecessor.id);
    const before = publicationRows(f);
    await assert.rejects(current.store.completeRefresh(current.job, request, 'qualification_not_complete'),
      error => error.code === 'transaction_rejected');
    await assert.rejects(current.store.failRefresh(current.job, request),
      error => error.code === 'transaction_rejected');
    assert.deepEqual(publicationRows(f), before);
    assert.deepEqual(requestRow(f), request);
    assert.equal(current.sends.length, 0);
  }));
}

test('newer resolved failure source invalidates an older refresh request', () => isolated(async f => {
  const predecessor = f.source();
  const request = f.pending(predecessor);
  const current = await claimed(f);
  f.source({ id: 'synthetic-newest-failure-source', head: OTHER_HEAD });
  assert.equal(await current.store.refreshRequest(current.job), null);
  const before = publicationRows(f);
  await assert.rejects(current.store.completeRefresh(current.job, request, 'qualification_not_complete'),
    error => error.code === 'transaction_rejected');
  await assert.rejects(current.store.failRefresh(current.job, request),
    error => error.code === 'transaction_rejected');
  assert.deepEqual(publicationRows(f), before);
  assert.equal(current.sends.length, 0);
}));

// These are driver state-machine units: evaluate is deliberately replaced with
// a failure result or a budget rejection. They make no claim to retest real
// qualification/evidence verification, which has separate existing coverage.
function negativeDriver(current, evaluate, overrides = {}) {
  return {
    c: { ...CONFIG, ...overrides }, store: current.store, github: current.github,
    evaluate,
    freshBinding: async () => { throw new Error('FRESH_BINDING_AFTER_FAILURE_FORBIDDEN'); },
  };
}
test('mocked negative evaluation completes refresh with no publication', () => isolated(async f => {
  const predecessor = f.source();
  f.pending(predecessor);
  const current = f.invocation();
  let evaluations = 0;
  const result = await reconcileOne(negativeDriver(current, async (pr, reservedSQL) => {
    assert.equal(pr, 17);
    assert.equal(reservedSQL, 6);
    current.budget.checkWork();
    evaluations++;
    return failureProposal();
  }));
  assert.equal(result.disposition, 'refresh_not_qualified');
  assert.equal(evaluations, 1);
  assert.equal(requestRow(f).status, 'done');
  assert.equal(requestRow(f).reason, 'qualification_not_complete');
  assert.equal(f.first('SELECT * FROM outbox WHERE target_key=?', KEY), null);
  assert.deepEqual(publicationRows(f), [predecessor]);
  assert.equal(current.sends.length, 0);
}));

test('disabled background leaves pending refresh untouched and sends nothing', () => isolated(async f => {
  const predecessor = f.source();
  const request = f.pending(predecessor);
  const current = f.invocation();
  const result = await reconcileOne(negativeDriver(current,
    async () => { throw new Error('EVALUATION_WHILE_DISABLED_FORBIDDEN'); }, { backgroundEnabled: false }));
  assert.equal(result.disposition, 'background_disabled');
  assert.deepEqual(requestRow(f), request);
  assert.deepEqual(current.budget.snapshot(), { sql: 0, external: 0 });
  assert.equal(current.sends.length, 0);
}));

test('disabled publication records refresh retry without evaluation or sends', () => isolated(async f => {
  const predecessor = f.source();
  f.pending(predecessor);
  const current = f.invocation();
  let evaluations = 0;
  const result = await reconcileOne(negativeDriver(current,
    async () => { evaluations++; throw new Error('EVALUATION_WHILE_DISABLED_FORBIDDEN'); }, { checkPublishingEnabled: false }));
  assert.equal(evaluations, 0);
  assert.equal(result.disposition, 'refresh_retry_pending_or_manual');
  assert.equal(requestRow(f).status, 'pending');
  assert.equal(requestRow(f).attempts, 1);
  assert.deepEqual(publicationRows(f), [predecessor]);
  assert.equal(current.sends.length, 0);
}));

for (const reason of ['time', 'sql', 'external']) {
  test('mocked evaluation ' + reason + ' budget rejection sends nothing and preserves source', () => isolated(async f => {
    const predecessor = f.source();
    f.pending(predecessor);
    const current = f.invocation();
    let observedReason;
    const result = await reconcileOne(negativeDriver(current, async () => {
      try {
        if (reason === 'time') {
          current.setElapsed(FREE_BUDGET.workMs);
          current.budget.checkWork();
        } else if (reason === 'sql') {
          while (current.budget.snapshot().sql < FREE_BUDGET.sqlWork) await current.store.databaseNow();
          await current.store.databaseNow();
        } else {
          // In-memory reads charge the exact shared production budget; there is
          // no HTTP, credential exchange, check mutation, or synthetic success.
          for (let index = 0; index <= FREE_BUDGET.externalWork; index++) current.github.budget.externalSignal();
        }
        assert.fail('budget boundary must reject');
      } catch (error) {
        observedReason = error.reason;
        throw error;
      }
    }));
    assert.equal(observedReason, reason);
    assert.equal(result.disposition, 'refresh_retry_pending_or_manual');
    assert.equal(requestRow(f).status, 'pending');
    assert.equal(requestRow(f).attempts, 1);
    assert.deepEqual(publicationRows(f), [predecessor]);
    assert.equal(current.sends.length, 0);
    assert.ok(current.budget.snapshot().sql <= FREE_BUDGET.sql);
    assert.ok(current.budget.snapshot().external <= FREE_BUDGET.external);
  }));
}

// beginFreshPublication has a positive-only defensive gate. This negative unit
// intentionally stops there; the shared CAS is tested through complete/fail above.
test('beginFreshPublication rejects a failure proposal before SQL or journal writes', () => isolated(async f => {
  const predecessor = f.source();
  const request = f.pending(predecessor);
  const current = await claimed(f);
  const before = current.budget.snapshot();
  await assert.rejects(current.store.beginFreshPublication(current.job, request, failureProposal(), predecessor),
    error => error.code === 'fresh_proof_unavailable');
  assert.deepEqual(current.budget.snapshot(), before);
  assert.deepEqual(publicationRows(f), [predecessor]);
  assert.deepEqual(requestRow(f), request);
  assert.equal(current.sends.length, 0);
}));

// Original low-level publication journal with failure proposals only. These
// tests never run the refresh driver's qualified-publication branch.
test('original failure-only issued journal commits before the in-memory failure send', () => isolated(async f => {
  const predecessor = f.source();
  const current = f.invocation();
  const check = failureProposal();
  const attempt = await current.store.beginPublication(KEY, check);
  await current.github.publish(check, attempt);
  assert.deepEqual(current.sends, [{ attempt, conclusion: 'failure' }]);
  assert.deepEqual(f.events.slice(-2), ['commit', 'memory_failure_send']);
  assert.deepEqual(f.first('SELECT * FROM publication_attempts WHERE id=?', predecessor.id), predecessor);
}));

test('concurrent original failure-only journal attempts produce exactly one issued row', () => isolated(async f => {
  f.source();
  const current = f.invocation();
  const results = await Promise.allSettled([
    current.store.beginPublication(KEY, failureProposal()),
    current.store.beginPublication(KEY, failureProposal()),
  ]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  const rejected = results.filter(result => result.status === 'rejected');
  assert.equal(rejected.length, 1);
  assert.equal(rejected[0].reason.code, 'transaction_rejected');
  assert.equal(f.first("SELECT count(*) AS count FROM publication_attempts WHERE phase='issued'").count, 1);
  assert.equal(publicationRows(f).length, 2);
  assert.equal(current.sends.length, 0);
}));

test('original failure-only unknown settlement remains a durable no-retry barrier', () => isolated(async f => {
  f.source();
  const current = f.invocation();
  const attempt = await current.store.beginPublication(KEY, failureProposal());
  current.budget.settlement();
  await current.store.settlePublication(attempt, 'unknown');
  const unknown = f.first('SELECT * FROM publication_attempts WHERE id=?', attempt);
  assert.equal(unknown.phase, 'unknown');
  assert.equal(unknown.conclusion, 'failure');
  assert.equal(unknown.run_id, null);
  const retry = f.invocation();
  await assert.rejects(retry.store.beginPublication(KEY, failureProposal()),
    error => error.code === 'transaction_rejected');
  assert.deepEqual(f.first('SELECT * FROM publication_attempts WHERE id=?', attempt), unknown);
  assert.equal(publicationRows(f).length, 2);
  assert.equal(current.sends.length + retry.sends.length, 0);
}));

test('fresh unknown settlement preserves pinned run on an explicitly failure-only issued fixture', () => isolated(async f => {
  const issued = f.source({ phase: 'issued' });
  f.enqueue();
  const current = await claimed(f);
  current.budget.settlement();
  await current.store.settleFreshPublication(current.job, issued.id, 'unknown');
  const row = f.first('SELECT * FROM publication_attempts WHERE id=?', issued.id);
  assert.equal(row.phase, 'unknown');
  assert.equal(row.conclusion, 'failure');
  assert.equal(row.run_id, issued.run_id);
  assert.equal(current.sends.length, 0);
}));

test('fresh settlement rejects a wrong receipt run on an explicitly failure-only issued fixture', () => isolated(async f => {
  const issued = f.source({ phase: 'issued' });
  f.enqueue();
  const current = await claimed(f);
  const outbox = f.first('SELECT * FROM outbox WHERE target_key=?', KEY);
  current.budget.settlement();
  await assert.rejects(current.store.settleFreshPublication(current.job, issued.id, 'reconcile', 7004),
    error => error.code === 'transaction_rejected');
  assert.deepEqual(f.first('SELECT * FROM publication_attempts WHERE id=?', issued.id), issued);
  assert.deepEqual(f.first('SELECT * FROM outbox WHERE target_key=?', KEY), outbox);
  assert.equal(current.sends.length, 0);
}));

test('superseded same-head confirmed failure history requires manual refresh review', () => isolated(async f => {
  f.source({ id: 'synthetic-historical-failure', phase: 'confirmed' });
  const latest = f.source({ id: 'synthetic-latest-resolved-failure' });
  const before = publicationRows(f);
  f.enqueue();
  await finishResolvedPass(f);
  const request = requestRow(f);
  assert.equal(request.status, 'manual');
  assert.equal(request.reason, 'superseded_history_requires_review');
  assert.equal(request.source_attempt, latest.id);
  assert.deepEqual(publicationRows(f), before);
  assert.equal(f.first('SELECT * FROM outbox WHERE target_key=?', KEY), null);
}));

const adapterMutations = {
  'predecessor wrong head': ({ predecessor }) => { predecessor.head_sha = OTHER_HEAD; },
  'remote wrong run': ({ remote }) => { remote.id = 7004; },
  'remote wrong external_id': ({ remote }) => { remote.external_id = 'synthetic-unrelated-attempt'; },
  'remote wrong output summary': ({ remote }) => { remote.output.summary = 'SYNTHETIC UNRELATED FAILURE'; },
};
for (const [label, mutate] of Object.entries(adapterMutations)) {
  test('GitHub publishFresh failure adapter rejects ' + label + ' before journal/send', () => isolated(async f => {
    const predecessor = f.source();
    const check = failureProposal();
    const remote = {
      id: predecessor.run_id, app: { id: CONFIG.appId, slug: CONFIG.appSlug },
      name: CONFIG.checkName, head_sha: HEAD, external_id: predecessor.id,
      status: 'completed', conclusion: 'failure', output: { summary: predecessor.proof_text },
    };
    mutate({ predecessor, remote });
    const current = f.invocation();
    let callbacks = 0;
    let mutations = 0;
    // Deliberate adapter unit: bypass construction/authentication and replace
    // only ordinary TypeScript-private methods with in-memory fixtures. No
    // secrets, app signing key, installation exchange, or actual HTTP exists.
    const github = Object.create(GitHub.prototype);
    Object.assign(github, {
      c: CONFIG, budget: current.budget, now: f.now,
      installationToken: async write => {
        assert.equal(write, true);
        current.budget.checkWork();
        return 'SYNTHETIC_IN_MEMORY_TOKEN';
      },
      runs: async head => {
        assert.equal(head, HEAD);
        current.budget.externalSignal();
        return [remote];
      },
      json: async () => { mutations++; throw new Error('ADAPTER_SEND_FORBIDDEN'); },
    });
    await assert.rejects(github.publishFresh(check, predecessor, async () => {
      callbacks++;
      throw new Error('ADAPTER_JOURNAL_CALLBACK_FORBIDDEN');
    }), error => ['refresh_predecessor_unavailable', 'check_source_mismatch'].includes(error.code));
    assert.equal(callbacks, 0);
    assert.equal(mutations, 0);
    assert.equal(current.sends.length, 0);
    assert.equal(publicationRows(f).length, 1);
    assert.equal(publicationRows(f)[0].conclusion, 'failure');
  }));
}

test('independent refresh boundary suite makes no outbound fetch calls', () => assert.equal(outbound, 0));
