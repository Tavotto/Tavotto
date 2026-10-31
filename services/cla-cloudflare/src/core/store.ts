import type { Config, D1Database, D1Statement, IntentRole, Job, Packet, Role, Session, Target } from './types.ts';
import { Rejected } from './types.ts';
import { canonical, randomToken } from './crypto.ts';
export class Store {
  db: D1Database;
  constructor(db: D1Database) { this.db = db; }
  stmt(sql: string, ...values: unknown[]): D1Statement { return this.db.prepare(sql).bind(...values); }
  async batch(stmts: D1Statement[]): Promise<void> {
    try { const results = await this.db.batch(stmts); if (results.some(r => !r.success)) throw new Error(); }
    catch { throw new Rejected('transaction_rejected'); }
  }
  guard(sql: string, ...values: unknown[]): D1Statement[] {
    const id = randomToken();
    return [this.stmt(`INSERT INTO _guards(id,ok) SELECT ?,CASE WHEN (${sql}) THEN 1 ELSE 0 END`, id, ...values), this.stmt('DELETE FROM _guards WHERE id=?', id)];
  }
  cas(expected: number): D1Statement[] {
    return [this.stmt('UPDATE state SET revision=revision+1 WHERE id=1 AND revision=?', expected), ...this.guard('changes()=1')];
  }
  async initialize(c: Config): Promise<void> {
    const statements = [this.stmt('INSERT OR IGNORE INTO authority VALUES(1,?)', c.holderId), ...this.guard('(SELECT holder FROM authority WHERE id=1)=?', c.holderId)];
    for (const [kind, a] of Object.entries(c.policy.agreements)) {
      statements.push(this.stmt('INSERT OR IGNORE INTO agreements VALUES(?,?,?)', kind, a.version, a.sha256),
        ...this.guard('EXISTS(SELECT 1 FROM agreements WHERE kind=? AND version=? AND digest=?)', kind, a.version, a.sha256));
    }
    await this.batch(statements);
  }
  async revision(): Promise<number> { return (await this.stmt('SELECT revision FROM state WHERE id=1').first<{revision:number}>())!.revision; }
  async session(hash: string, now: number): Promise<Session | null> { return this.stmt('SELECT * FROM sessions WHERE token_hash=? AND expires>?', hash, now).first<Session>(); }
  async addSession(s: Session): Promise<void> { await this.batch([this.stmt('INSERT INTO sessions VALUES(?,?,?,?,?)', s.token_hash,s.actor,s.login,s.csrf_hash,s.expires)]); }
  async addOAuth(hash: string, browser: string, cipher: string, expires: number): Promise<void> {
    await this.batch([this.stmt('INSERT INTO oauth_states(state_hash,browser_hash,verifier_cipher,expires) VALUES(?,?,?,?)',hash,browser,cipher,expires)]);
  }
  async consumeOAuth(hash: string, browser: string, now: number): Promise<string> {
    // Commit the single-use transition BEFORE external identity exchange. The
    // returned cipher comes from exactly the row atomically changed by this call.
    let row: {verifier_cipher:string}|undefined;
    try {
      const result=await this.db.batch<{verifier_cipher:string}>([
        this.stmt('UPDATE oauth_states SET used=1 WHERE state_hash=? AND browser_hash=? AND expires>? AND used=0 RETURNING verifier_cipher',hash,browser,now),
      ]);
      if(result.some(r=>!r.success))throw new Error();
      row=result[0]?.results[0];
    }catch{throw new Rejected('transaction_rejected');}
    if(!row)throw new Rejected('oauth_rejected');
    return row.verifier_cipher;
  }
  async packet(id: string): Promise<Packet | null> { return this.stmt('SELECT * FROM packets WHERE id=?',id).first<Packet>(); }
  async createPacket(p: Packet): Promise<Packet> {
    await this.batch([this.stmt('INSERT OR IGNORE INTO packets VALUES(?,?,?,?,?,?,?)',p.id,p.subject,p.kind,p.version,p.digest,p.created,p.valid_until)]);
    // Explicit column names below keep this query correct if migration fields are added.
    const row = await this.stmt('SELECT * FROM packets WHERE subject=? AND digest=?',p.subject,p.digest).first<Packet>();
    if (!row) throw new Rejected('packet_unavailable');
    return row;
  }
  async blocked(id: string): Promise<boolean> { return !!await this.stmt('SELECT 1 AS n FROM eligibility_blocks WHERE packet=?',id).first(); }
  async hasSignature(id: string, role: Role): Promise<boolean> { return !!await this.stmt('SELECT 1 AS n FROM signatures WHERE packet=? AND role=?',id,role).first(); }
  async addIntent(hash: string, s: Session, p: Packet, role: IntentRole, expires: number): Promise<void> {
    await this.batch([this.stmt('INSERT INTO intents(token_hash,session_hash,packet,role,digest,expires) VALUES(?,?,?,?,?,?)',hash,s.token_hash,p.id,role,p.digest,expires)]);
  }
  async intentRole(hash: string, session: string, packet: string, now: number): Promise<IntentRole> {
    const i = await this.stmt('SELECT role FROM intents WHERE token_hash=? AND session_hash=? AND packet=? AND expires>? AND used=0',hash,session,packet,now).first<{role:IntentRole}>();
    if (!i) throw new Rejected('intent_rejected');
    return i.role;
  }
  async sign(input: {session:Session; packet:Packet; intentHash:string; role:Role; cipher:string; evidenceHash:string; now:number; expectedRevision:number; fileKey:string|null}): Promise<void> {
    const {session:s,packet:p,intentHash,role,now} = input;
    const sql = `EXISTS(SELECT 1 FROM intents i JOIN sessions s ON s.token_hash=i.session_hash JOIN packets p ON p.id=i.packet
      WHERE i.token_hash=? AND s.token_hash=? AND p.id=? AND i.used=0 AND i.expires>? AND s.expires>? AND p.valid_until>?
      AND i.digest=p.digest AND p.digest=? AND p.kind=? AND p.version=? AND i.role=? AND s.actor=?
      AND NOT EXISTS(SELECT 1 FROM eligibility_blocks b WHERE b.packet=p.id)
      AND NOT EXISTS(SELECT 1 FROM signatures g WHERE g.packet=p.id AND g.role=i.role)
      AND ((i.role='contributor' AND s.actor=p.subject) OR
       (i.role='holder' AND s.actor=(SELECT holder FROM authority WHERE id=1) AND s.actor!=p.subject
        AND EXISTS(SELECT 1 FROM signatures g WHERE g.packet=p.id AND g.role='contributor' AND g.actor=p.subject))))`;
    await this.batch([
      ...this.cas(input.expectedRevision),
      ...this.guard(sql,intentHash,s.token_hash,p.id,now,now,now,p.digest,p.kind,p.version,role,s.actor),
      this.stmt('INSERT INTO signatures VALUES(?,?,?,?,?,?,?)',p.id,role,s.actor,input.cipher,input.evidenceHash,input.fileKey,now),
      this.stmt('UPDATE intents SET used=1 WHERE token_hash=?',intentHash),
      this.stmt('INSERT INTO audit(packet,actor,event,created) VALUES(?,?,?,?)',p.id,s.actor,'signed:'+role,now),
    ]);
  }
  async hold(s: Session, p: Packet, intent: string, now: number, revision: number): Promise<void> {
    await this.batch([
      ...this.cas(revision),
      ...this.guard(`EXISTS(SELECT 1 FROM intents i JOIN sessions s ON s.token_hash=i.session_hash JOIN packets p ON p.id=i.packet
        WHERE i.token_hash=? AND i.session_hash=? AND i.packet=? AND i.role='hold' AND i.used=0 AND i.expires>?
        AND s.expires>? AND s.actor=? AND i.digest=p.digest AND p.digest=?
        AND (s.actor=p.subject OR s.actor=(SELECT holder FROM authority WHERE id=1)))`,intent,s.token_hash,p.id,now,now,s.actor,p.digest),
      this.stmt('INSERT INTO eligibility_blocks VALUES(?,?,?,?)',p.id,s.actor,now,'party_requested_review'),
      this.stmt('UPDATE intents SET used=1 WHERE token_hash=?',intent),
      this.stmt('INSERT INTO audit(packet,actor,event,created) VALUES(?,?,?,?)',p.id,s.actor,'hold',now),
    ]);
  }
  async evidence(packet: string, role: Role): Promise<{evidence_cipher:string;evidence_hash:string;file_key:string|null}|null> {
    return this.stmt('SELECT evidence_cipher,evidence_hash,file_key FROM signatures WHERE packet=? AND role=?',packet,role).first();
  }
  async qualifiedUntil(subject: number, digest: string, now: number): Promise<number|null> {
    const row=await this.stmt(`SELECT p.valid_until FROM packets p JOIN signatures c ON c.packet=p.id AND c.role='contributor'
      JOIN signatures h ON h.packet=p.id AND h.role='holder'
      WHERE p.subject=? AND p.digest=? AND p.valid_until>? AND c.actor=p.subject
      AND h.actor=(SELECT holder FROM authority WHERE id=1) AND h.actor!=c.actor
      AND NOT EXISTS(SELECT 1 FROM eligibility_blocks b WHERE b.packet=p.id)`,subject,digest,now).first<{valid_until:number}>();
    return row?.valid_until??null;
  }
  targetKey(repository: number, t: Target): string { return t.kind==='pr' ? `pr:${repository}:${t.number}` : `merge:${repository}:${t.headSha}:${t.baseSha}`; }
  async track(repository: number,t: Target): Promise<string> {
    const key=this.targetKey(repository,t), json=canonical(t);
    await this.batch([this.stmt('INSERT OR IGNORE INTO targets VALUES(?,?)',key,json),...this.guard('EXISTS(SELECT 1 FROM targets WHERE target_key=? AND target_json=?)',key,json)]);
    return key;
  }
  async enqueue(key: string): Promise<void> {
    await this.batch([this.stmt(`INSERT INTO outbox(target_key,wanted_revision,due) VALUES(?,(SELECT revision FROM state WHERE id=1),0)
      ON CONFLICT(target_key) DO UPDATE SET wanted_revision=excluded.wanted_revision,generation=outbox.generation+1,due=0,attempts=0`,key)]);
  }
  async delivery(id: string, bodyHash: string, repository: number, target: Target, now: number): Promise<void> {
    const key=this.targetKey(repository,target),json=canonical(target);
    await this.batch([
      this.stmt('INSERT OR IGNORE INTO targets VALUES(?,?)',key,json),
      ...this.guard('EXISTS(SELECT 1 FROM targets WHERE target_key=? AND target_json=?)',key,json),
      ...this.guard('NOT EXISTS(SELECT 1 FROM deliveries WHERE id=? AND (body_sha!=? OR target_key!=?))',id,bodyHash,key),
      this.stmt('INSERT OR IGNORE INTO deliveries VALUES(?,?,?,?)',id,bodyHash,key,now),
    ]);
  }
  async claim(now: number): Promise<Job|null> {
    const lease=randomToken();
    const results=await this.db.batch<Job>([this.stmt(`UPDATE outbox SET lease=?,lease_until=? WHERE target_key=(SELECT target_key FROM outbox
      WHERE due<=? AND lease_until<=? AND attempts<3 ORDER BY due,target_key LIMIT 1) AND lease_until<=? RETURNING *`,lease,now+30,now,now,now)]);
    return results[0]?.results[0]??null;
  }
  async target(key: string): Promise<Target> { return JSON.parse((await this.stmt('SELECT target_json FROM targets WHERE target_key=?',key).first<{target_json:string}>())!.target_json); }
  async owns(job: Job,now: number): Promise<boolean> { return !!await this.stmt('SELECT 1 AS n FROM outbox WHERE target_key=? AND lease=? AND lease_until>?',job.target_key,job.lease,now).first(); }
  async finish(job: Job, observedRevision: number, now: number): Promise<void> {
    await this.batch([
      this.stmt('DELETE FROM outbox WHERE target_key=? AND lease=? AND wanted_revision=? AND generation=? AND (SELECT revision FROM state WHERE id=1)=?',job.target_key,job.lease,observedRevision,job.generation,observedRevision),
      this.stmt('UPDATE outbox SET lease=\'\',lease_until=0,due=? WHERE target_key=? AND lease=?',now,job.target_key,job.lease),
    ]);
  }
  async fail(job: Job,now: number): Promise<void> {
    await this.batch([this.stmt(`UPDATE outbox SET attempts=CASE WHEN generation>? THEN 0 ELSE min(3,attempts+1) END,
      due=CASE WHEN generation>? THEN ? ELSE ? END,lease='',lease_until=0 WHERE target_key=? AND lease=?`,job.generation,job.generation,now,now+Math.min(60,2**(job.attempts+1)),job.target_key,job.lease)]);
  }
  async published(key:string,run:number,head:string,conclusion:'success'|'failure',revision:number,now:number):Promise<void> {
    await this.batch([this.stmt(`INSERT INTO published_checks VALUES(?,?,?,?,?,?) ON CONFLICT(target_key) DO UPDATE SET
      run_id=excluded.run_id,head_sha=excluded.head_sha,conclusion=excluded.conclusion,revision=excluded.revision,created=excluded.created`,key,run,head,conclusion,revision,now)]);
  }
}
