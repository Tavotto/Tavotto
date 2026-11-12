/**
 * 「接入 AI 编辑器」的**结果层**（用户 2026-10-27）。
 *
 * 安装器只有 `src/tavotto/engine/hostconnect.py` 那一份：设置里的按钮经壳命令 `host_integration` spawn
 * `tavotto-cli hosts <list|connect|disconnect> … --json`，这里只做三件事——解析那一行 JSON、把 `step`
 * 名翻成人话、把 `error_code` 翻成人话。写哪些文件、怎么备份一个字都不在这里（与 `codexInstall.ts`
 * 同一条纪律，看护 `tests/test_desktop_hosts_button.py`）。英文 code 不进界面，认不出就回退到 `other`。
 */
import { t } from '@/i18n'
import { codexStepLabel } from '@/lib/codexInstall'

/** 引擎每一步的结论（`hostconnect._step()` 逐字段对应；Codex 一项的步骤来自 codexinstall，同形） */
export interface HostStep {
  step: string
  ok: boolean
  skipped: boolean
  detail?: string
  error_code?: string
  /** 只有 `skill` 这一步带：written / updated / skipped / modified / removed / builtin / none / failed */
  skill?: string
}

/** 技能副本的状态（`hostskills.skill_state`）：current 是最新、stale 是旧版、modified 是用户改过、builtin 不需要复制 */
export type HostSkillState = 'current' | 'stale' | 'modified' | 'missing' | 'builtin'

export interface HostRow {
  id: string
  label: string
  installed: boolean
  connected: boolean
  skill: HostSkillState
  /** Codex：已装插件的版本；其余：技能记录里的版本；读不到就没有 */
  version?: string
  /** 只在 Codex 插件落后于这份 Tavotto 时出现 */
  update?: { latest: string; how: 'codex_plugin' }
}

/** `tavotto hosts list --json` */
export interface HostList {
  ok: boolean
  action: 'list'
  server: boolean
  hosts: HostRow[]
}

/** `tavotto hosts connect|disconnect <host> --json` */
export interface HostResult {
  ok: boolean
  host: string
  action: 'connect' | 'disconnect'
  state?: 'connected' | 'needs_new_session' | 'not_installed' | 'failed' | 'disconnected'
  steps: HostStep[]
  project_root?: string
  error_code?: string
}

const hc = (key: string, values?: Record<string, unknown>) =>
  t(`settings.hosts.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 壳侧失败（连那行 JSON 都没拿到）——带稳定 code，不带英文句子 */
export class HostShellError extends Error {
  readonly code: string

  constructor(code: string) {
    super(code)
    this.name = 'HostShellError'
    this.code = code
  }
}

function parseLine(raw: string): Record<string, unknown> {
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    throw new HostShellError('bad_output')
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new HostShellError('bad_output')
  return data as Record<string, unknown>
}

const SKILL_STATES: readonly HostSkillState[] = ['current', 'stale', 'modified', 'missing', 'builtin']

function parseSkillState(v: unknown): HostSkillState {
  return SKILL_STATES.find((x) => x === v) ?? 'missing'
}

function parseUpdate(v: unknown): { update?: HostRow['update'] } {
  const u = v as { latest?: unknown; how?: unknown } | null
  return u && typeof u.latest === 'string' && u.how === 'codex_plugin'
    ? { update: { latest: u.latest, how: 'codex_plugin' } }
    : {}
}

export function parseHostList(raw: string): HostList {
  const d = parseLine(raw)
  if (d.action !== 'list' || !Array.isArray(d.hosts)) throw new HostShellError('bad_output')
  const hosts = (d.hosts as unknown[])
    .filter((h): h is Record<string, unknown> => !!h && typeof h === 'object')
    .map((h) => ({
      id: String(h.id ?? ''),
      label: String(h.label ?? h.id ?? ''),
      installed: h.installed === true,
      connected: h.connected === true,
      skill: parseSkillState(h.skill),
      ...(typeof h.version === 'string' && h.version ? { version: h.version } : {}),
      ...parseUpdate(h.update),
    }))
    .filter((h) => h.id)
  return {
    ok: d.ok === true,
    action: 'list',
    server: d.server === true,
    hosts,
  }
}

export function parseHostResult(raw: string): HostResult {
  const d = parseLine(raw)
  if ((d.action !== 'connect' && d.action !== 'disconnect') || !Array.isArray(d.steps)) {
    throw new HostShellError('bad_output')
  }
  return d as unknown as HostResult
}

/** 宿主自己的步骤名优先，Codex 委托的那几步（marketplace / plugin / engine…）用 Codex 那一份的译名 */
export function hostStepLabel(step: string): string {
  const own = lookup(`step.${step}`)
  return own ?? codexStepLabel(step)
}

/** 技能那一步值得多说一句的情形（用户改过的副本没有覆盖 / 没有删）；其余不说 */
export function hostStepNote(s: HostStep): string | null {
  return s.step === 'skill' && s.skill === 'modified' ? hc('skillModified') : null
}

export function hostStepStateText(s: HostStep): string {
  return hc(!s.ok ? 'stepState.failed' : s.skipped ? 'stepState.skipped' : 'stepState.done')
}

function lookup(key: string, values?: Record<string, unknown>): string | null {
  const full = `settings.hosts.${key}`
  const text = t(full, { ns: 'dialogs', defaultValue: '', ...(values ?? {}) })
  return text && text !== full ? text : null
}

/** 失败的那一句：先找本层的译文，再找 Codex 安装器那一层的（Codex 一项的 code 来自它），都没有就 `other` */
export function hostErrorText(code: string | undefined, host?: string): string {
  const c = code || 'other'
  const own = lookup(`error.${c}`, { host: host ?? '' })
  if (own) return own
  const codex = t(`settings.agents.codexInstall.error.${c}`, { ns: 'dialogs', defaultValue: '' })
  if (codex && codex !== `settings.agents.codexInstall.error.${c}`) return codex
  return hc('error.other')
}
