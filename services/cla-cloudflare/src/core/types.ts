export const MAX_CONTRIBUTORS = 1000;
export type Role = 'contributor' | 'holder';
export type IntentRole = Role | 'hold';
export type AgreementKind = 'individual' | 'corporate';
export interface Agreement { path: string; version: string; sha256: string }
export interface Policy {
  agreements: Record<AgreementKind, Agreement>;
  exemptions: Array<{ login: string; kind: string; reason: string }>;
  provider: { configured: boolean; [key: string]: unknown };
}
export interface Config {
  origin: string; repositoryId: number; referenceSha: string; holderId: number;
  appId: number; appSlug: string; checkName: string; policy: Policy;
  exemptionIds: Record<string, number>;
  syntheticSigning: boolean; mockCheckPublishing: boolean;
  autoHolder: false;
}
export interface Identity { id: number; login: string; type: 'User' | 'Bot' }
export interface Commit { sha: string; author: Identity | null; email: string; message: string }
export interface PrSnapshot {
  number: number; headSha: string; baseSha: string; state: 'open' | 'closed';
  author: Identity; expectedCommits: number; commits: Commit[];
}
export type Target = { kind: 'pr'; number: number } | { kind: 'merge_group'; headSha: string; baseSha: string };
export interface Candidate {
  kind: 'pr' | 'merge_group'; repositoryId: number; headSha: string; baseSha: string;
  policy: Policy; prs: PrSnapshot[];
  members: Array<{ pr: number; headSha: string }>;
}
export interface Binding {
  repository_id: number; head_sha: string; base_sha: string;
  kind: 'pr' | 'merge_group'; pr?: number;
  members: Array<{ pr: number; head_sha: string }>;
  contributors: Array<{ id: number; login: string }>;
  policy_sha256: string;
}
export interface Proof extends Binding {
  protocol: 'tavotto-cla-v1'; revision: number; issued_at: number; expires_at: number;
}
export interface CheckProposal {
  name: string; appId: number; appSlug: string; headSha: string;
  conclusion: 'success' | 'failure'; proof: Proof; text: string;
  authorityExpires?: number;
}
export interface CheckReceipt { id: number; appId: number; appSlug: string; name: string; headSha: string }
// The frozen prototype selects mock/disabled; the isolated native candidate adds real.
export interface GitHubPort {
  mode: 'mock' | 'disabled' | 'real';
  oauthIdentity(code: string, verifier: string): Promise<Identity>;
  resolveLogin(login: string): Promise<Identity>;
  snapshot(target: Target): Promise<Candidate>;
  publish(check: CheckProposal): Promise<CheckReceipt>;
}
export interface EncryptedFiles {
  putIfAbsent(key: string, ciphertext: string): Promise<void>;
  get(key: string): Promise<string | null>;
}
export interface D1Result<T = Record<string, unknown>> {
  success: boolean; results: T[]; meta: { changes: number; [key: string]: unknown };
}
export interface D1Statement {
  bind(...values: unknown[]): D1Statement;
  all<T = Record<string, unknown>>(): Promise<D1Result<T>>;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  run(): Promise<D1Result>;
}
export interface D1Database {
  prepare(sql: string): D1Statement;
  batch<T = Record<string, unknown>>(statements: D1Statement[]): Promise<D1Result<T>[]>;
}
export interface Session { token_hash: string; actor: number; login: string; csrf_hash: string; expires: number }
export interface Packet {
  id: string; subject: number; kind: AgreementKind; version: string; digest: string;
  created: number; valid_until: number;
}
export interface Job { target_key: string; wanted_revision: number; generation: number; due: number; attempts: number; lease: string; lease_until: number }
export interface SyntheticEvidence { marker: 'SYNTHETIC TEST ONLY'; accepted: true; statement: string }
export class Rejected extends Error {
  code: string;
  constructor(code = 'request_rejected') { super(code); this.code = code; }
}
