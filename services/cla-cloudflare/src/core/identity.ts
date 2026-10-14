import type { Binding, Candidate, Config, GitHubPort, Identity, Target } from './types.ts';
import { Rejected } from './types.ts';
import { canonical } from './crypto.ts';
import { policyDigest, validId, validSha } from './policy.ts';
export function identity(user: Identity): Identity {
  if (!user || !validId(user.id) || !/^[A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?$/.test(user.login) || !['User', 'Bot'].includes(user.type)) throw new Rejected('identity_unresolved');
  return user;
}
export async function binding(c: Config, port: GitHubPort, target: Target, snapshot: Candidate): Promise<Binding> {
  if (snapshot.repositoryId !== c.repositoryId || snapshot.kind !== target.kind || !validSha(snapshot.headSha) || !validSha(snapshot.baseSha) ||
    await policyDigest(snapshot.policy) !== await policyDigest(c.policy)) throw new Rejected('trusted_snapshot_mismatch');
  if (target.kind === 'pr' && (snapshot.prs.length !== 1 || snapshot.prs[0]?.number !== target.number || snapshot.prs[0].headSha !== snapshot.headSha || snapshot.prs[0].baseSha !== snapshot.baseSha || snapshot.members.length!==0)) throw new Rejected('pr_mismatch');
  if (target.kind === 'merge_group' && (snapshot.headSha !== target.headSha || snapshot.baseSha !== target.baseSha || !snapshot.members.length || snapshot.prs.length !== snapshot.members.length)) throw new Rejected('candidate_mismatch');
  if (!snapshot.prs.length || snapshot.prs.length > 100 || new Set(snapshot.prs.map(p => p.number)).size !== snapshot.prs.length) throw new Rejected('candidate_members_invalid');
  const users = new Map<number, Identity>();
  const add = (u: Identity) => { identity(u); const old = users.get(u.id); if (old && old.login.toLowerCase() !== u.login.toLowerCase()) throw new Rejected('identity_changed'); users.set(u.id, u); };
  const email = async (value: string) => {
    const m = /^([1-9][0-9]*)\+([A-Za-z0-9][A-Za-z0-9-]*)@users\.noreply\.github\.com$/i.exec(value);
    if (!m) throw new Rejected('coauthor_unresolved');
    const u = identity(await port.resolveLogin(m[2]!));
    if (u.id !== Number(m[1]) || u.login.toLowerCase() !== m[2]!.toLowerCase()) throw new Rejected('coauthor_identity_mismatch');
    add(u);
  };
  for (const pr of snapshot.prs) {
    if (!validId(pr.number) || pr.state !== 'open' || !validSha(pr.headSha) || !validSha(pr.baseSha) || !Number.isSafeInteger(pr.expectedCommits) || pr.expectedCommits < 1 || pr.expectedCommits > 1000 ||
      pr.commits.length !== pr.expectedCommits || new Set(pr.commits.map(x => x.sha)).size !== pr.expectedCommits || !pr.commits.some(x => x.sha === pr.headSha)) throw new Rejected('commits_incomplete');
    if (target.kind === 'merge_group' && snapshot.members.filter(m => m.pr === pr.number && m.headSha === pr.headSha).length !== 1) throw new Rejected('candidate_members_invalid');
    add(pr.author);
    for (const commit of pr.commits) {
      if (!validSha(commit.sha)) throw new Rejected('commit_invalid');
      if (commit.author) add(commit.author); else await email(commit.email);
      for (const line of commit.message.split(/\r?\n/)) {
        if (/^\s*Co-authored-by\s*:/i.test(line)) {
          const m = /^\s*Co-authored-by:\s*[^<>\r\n]+<([^<>\r\n]+)>\s*$/i.exec(line);
          if (!m) throw new Rejected('coauthor_invalid');
          await email(m[1]!.trim());
        }
      }
    }
  }
  return {
    repository_id: c.repositoryId, kind: snapshot.kind, head_sha: snapshot.headSha, base_sha: snapshot.baseSha,
    ...(target.kind === 'pr' ? { pr: target.number } : {}),
    members: snapshot.members.map(m => ({ pr: m.pr, head_sha: m.headSha })).sort((a, b) => a.pr - b.pr),
    contributors: [...users.values()].sort((a, b) => a.id - b.id).map(u => ({ id: u.id, login: u.login.toLowerCase() })),
    policy_sha256: await policyDigest(c.policy),
  };
}
export const sameBinding = (a: Binding, b: Binding) => {
  const fields = (x: Binding) => ({repository_id:x.repository_id,head_sha:x.head_sha,base_sha:x.base_sha,kind:x.kind,
    ...(x.pr===undefined?{}:{pr:x.pr}),members:x.members,contributors:x.contributors,policy_sha256:x.policy_sha256});
  return canonical(fields(a)) === canonical(fields(b));
};
