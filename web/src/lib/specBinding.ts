/**
 * 「这个项目按哪套规范检查」的**唯一判据**（ADR 0029）。
 *
 * 三层边界里这一层只回答 Spec：
 *
 *     Style   图长什么样      —— lib/stylePresets.ts（应用 = 文档修改，可撤销）
 *     Spec    图要满足什么    —— 本文件 + lib/preflight.ts（只检查，不改图）
 *     Export  文件怎么生成    —— lib/exportDefaults.ts + exportPayload.ts
 *
 * 核心决定：**项目结果稳定优先于规范自动升级**。文档里存的不只是一个 id，
 * 还有绑定那一刻的**规则全文快照**；全局那份后来改了，旧项目的结论一个字
 * 都不变，界面提示「有新版可同步」，由用户明确确认。
 *
 * 「有没有新版」的判据是**内容不等**，不是版本号——版本号是人写的，谁都
 * 可能忘了改；而用户在意的从来是「判据变没变」。
 *
 * 与后端 `engine/profilestore.resolve_spec()` 的一处**有意的不同**：那边
 * 不认识的 id 直接抛错（MCP 调用方拿着不存在的规范时，"按默认规范放行了"
 * 是最坏的答案），这边退回默认规范并把 `globalMissing` 报出来（旧文档里
 * 可能存着一个已经删掉的 id，导出对话框不能整个崩掉）。两边取舍不同是
 * 写在 ADR 里的，不是漂移。
 */
import doc from '@profiles'
import { JOURNAL_BASE_PROFILE_ID, type JournalFieldSource } from './journals'
import {
  DEFAULT_PROFILE_ID,
  loadProfile,
  mergeJournalInto,
  type JournalOverride,
  type PublicationProfile,
} from './profile'

/** 文档里那条绑定（`CanvasData.profile`）。类型见 types/document.ts。 */
export interface SpecBinding {
  id: string
  journal?: Record<string, unknown>
  snapshot?: Record<string, unknown>
  snapshotVersion?: string
  follow?: boolean
  /** 选期刊那一刻的出处（ADR 0155 §4）：库后来更新也不改已定稿项目的结论；缺省 = 没按期刊选过 */
  journalRef?: JournalRef
  /** 生效字段出处的精简版（value / quote / link / retrieved / 两种 scope），出处卡离线也读得到它 */
  journalSources?: Record<string, JournalFieldSource>
  /** 选期刊之前的规范绑定与页宽：「不按期刊」据此退回；没有 = 退回默认规范 */
  journalPrev?: JournalPrev
}

/** 选期刊之前项目用的规范（`profile` 为 null = 那时没有绑定）与页宽（mm） */
export interface JournalPrev {
  profile: Omit<SpecBinding, 'journalPrev' | 'journalRef' | 'journalSources'> | null
  pageW: number
  /** 选期刊那一刻期刊把页宽设成了多少：「不按期刊」只在页宽仍是它时才退回 pageW（用户手改过就保留） */
  setW?: number
}

export interface JournalRef {
  id: string
  catalogRev: number
  retrieved: string
  /** 选那一刻的期刊名（出处卡与检查结论的主语；库后来改名也不动） */
  label: string
}

/** 全局清单里的一条规范（内置或用户自建）。形状与 `/api/profiles/spec` 一致。 */
export interface SpecCatalogEntry {
  id: string
  display_name: string
  name_key?: string
  version: string
  built_in: boolean
  data: Record<string, unknown>
}

/** 实际用来检查的那一份从哪来的。**三档是闭集**，界面按它说话。 */
export type SpecSource = 'snapshot' | 'global' | 'builtin'

export interface ResolvedSpec {
  /** 直接喂给 runPreflight 的那一份（journal 覆盖已经合并进去） */
  profile: PublicationProfile
  source: SpecSource
  /** 全局清单里有一份内容不同的同 id 规范（且用户没选「跟着全局走」） */
  updateAvailable: boolean
  /**
   * **实际在用的那份的 id**。没在文档里指定过任何规范时它是内置默认那一条的 id
   * ——回退也是一种「在用」，界面的「本项目在用」判据认它（全面打磨 D03）。此前
   * 界面直接拿 `binding.id` 当判据，于是回退到内置默认的那套在库里被标成「没在用」，
   * 同一页顶部却写着「当前项目使用 · 默认规范」。
   */
  profileId: string | null
  /** 绑的 id 在全局清单里找不到了（被删 / 换了台电脑）。**快照仍然管用** */
  globalMissing: boolean
  /** 全局那一版的展示用版本号；找不到就是 null（"不知道"是独立一档） */
  globalVersion: string | null
  snapshotVersion: string | null
}

/**
 * 确定性序列化（键序固定、递归生效）。**只用来比内容**，不落盘、不当 id。
 * 与 Python 的 `patchspec.canonical_json` 同形，但这里不需要跨语言一致
 * ——比较的两侧都在浏览器里。
 */
function canonical(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null'
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(',')}}`
}

/** 两份规则是不是同一套。**这就是「有没有新版」的判据**。 */
export function sameRules(a: unknown, b: unknown): boolean {
  return canonical(a) === canonical(b)
}

interface ProfileDoc {
  default_profile: string
  profiles: Record<string, PublicationProfile>
}

const entryOf = (p: PublicationProfile): SpecCatalogEntry => ({
  id: p.profile_id,
  display_name: p.label,
  name_key: `builtin.spec.${p.profile_id}`,
  version: p.version,
  built_in: true,
  data: p as unknown as Record<string, unknown>,
})

/** 内置规范的清单（没有后端时也拿得到——浏览器演练场 / MCP 内嵌画布都靠它）。
 *  `listed: false` 的底（如期刊底 journal-base-v1）不在清单里——与后端 `list_profiles` /
 *  `builtins` 同判据；它们按 id 仍可解析（`isHiddenBuiltin`），见 `resolveDocumentSpec`。 */
export function builtinCatalog(): SpecCatalogEntry[] {
  const d = doc as unknown as ProfileDoc
  return Object.values(d.profiles)
    .filter((p) => p.listed !== false)
    .map(entryOf)
}

/** 是不是「可解析但不可列出」的内置底。同源对：后端 `profiles.all_profile_ids()` 对
 *  `resolve_spec` 的分流。绑定到它不算「全局规范已被删除」。 */
export function isHiddenBuiltin(id: string | null | undefined): boolean {
  const d = doc as unknown as ProfileDoc
  return !!id && d.profiles[id]?.listed === false
}

/** 内置规范（含不可列出的底）按 id 取一条清单项；没有返回 undefined。选期刊绑定中性底时用。 */
export function builtinEntryById(id: string): SpecCatalogEntry | undefined {
  const p = (doc as unknown as ProfileDoc).profiles[id]
  return p ? entryOf(p) : undefined
}

function hiddenBuiltinEntry(id: string): SpecCatalogEntry {
  return entryOf((doc as unknown as ProfileDoc).profiles[id])
}

/** 快照 / 全局那份 + 期刊覆盖 → 可直接喂给预检的规范。
 *  合并规则**不在这里实现**：`lib/profile.mergeJournalInto` 是唯一那一份。 */
function withJournal(
  base: Record<string, unknown>,
  journal: Record<string, unknown> | undefined,
): PublicationProfile {
  return mergeJournalInto(
    base as unknown as PublicationProfile,
    journal as JournalOverride | undefined,
  )
}

/**
 * 解析一条绑定 → 实际生效的规范 + 用户该被告知的那几件事。
 *
 * 优先级（顺序是判据的一部分，别在调用点临时挑）：
 *
 * | 情形 | 用哪一份 | `source` |
 * | --- | --- | --- |
 * | 有快照，且用户没选「跟着全局走」 | 快照 | `snapshot` |
 * | 选了跟随，且全局还找得到 | 全局现值 | `global` |
 * | 没快照（老文档 / 从没绑过） | 全局现值 | `global` |
 * | 上面都不成立 | 默认内置规范 | `builtin` |
 */
export function resolveDocumentSpec(
  binding: SpecBinding | null | undefined,
  catalog: SpecCatalogEntry[],
): ResolvedSpec {
  const journal = binding?.journal as JournalOverride | undefined
  const global = binding?.id
    ? (catalog.find((e) => e.id === binding.id) ??
      (isHiddenBuiltin(binding.id) ? hiddenBuiltinEntry(binding.id) : undefined))
    : undefined
  const snapshot = binding?.snapshot
  const follow = binding?.follow === true
  const globalMissing = !!binding?.id && !global
  const snapshotVersion = binding?.snapshotVersion ?? null
  const globalVersion = global?.version ?? null

  if (snapshot && !follow) {
    return {
      profile: withJournal(snapshot, binding?.journal),
      source: 'snapshot',
      profileId: binding?.id ?? null,
      // 全局没了就没有"新版"可言——`globalMissing` 才是那时该说的话
      updateAvailable: !!global && !sameRules(global.data, snapshot),
      globalMissing,
      globalVersion,
      snapshotVersion,
    }
  }
  if (global) {
    return {
      profile: withJournal(global.data, binding?.journal),
      source: 'global',
      profileId: global.id,
      updateAvailable: false,
      globalMissing: false,
      globalVersion,
      snapshotVersion,
    }
  }
  if (snapshot) {
    // 选了跟随、但全局那份不在了：快照是唯一还活着的事实
    return {
      profile: withJournal(snapshot, binding?.journal),
      source: 'snapshot',
      profileId: binding?.id ?? null,
      updateAvailable: false,
      globalMissing: true,
      globalVersion: null,
      snapshotVersion,
    }
  }
  return {
    profile: loadProfile(binding?.id ?? DEFAULT_PROFILE_ID, journal),
    source: 'builtin',
    profileId: binding?.id ?? DEFAULT_PROFILE_ID,
    updateAvailable: false,
    globalMissing,
    globalVersion,
    snapshotVersion,
  }
}

/**
 * 组装一条要写进文档的绑定。**快照在这里生成，别处不许现造**。
 *
 * `follow` 只在用户明确表态时写进去（`true` 才落字段）——缺省不写等于
 * 「没选过」，与 `false` 在行为上同义，而多一个恒为 false 的字段只会让
 * 每份文档都变大、让 diff 里多一行没有信息的东西。
 */
export function bindingFor(
  entry: SpecCatalogEntry,
  options?: {
    journal?: Record<string, unknown>
    journalRef?: JournalRef
    journalSources?: Record<string, JournalFieldSource>
    journalPrev?: JournalPrev
    follow?: boolean
  },
): SpecBinding {
  const journal = options?.journal
  return {
    id: entry.id,
    ...(journal && Object.keys(journal).length ? { journal: structuredClone(journal) } : {}),
    ...(options?.journalRef ? { journalRef: structuredClone(options.journalRef) } : {}),
    ...(options?.journalRef && options.journalSources
      ? { journalSources: structuredClone(options.journalSources) }
      : {}),
    ...(options?.journalRef && options.journalPrev
      ? { journalPrev: structuredClone(options.journalPrev) }
      : {}),
    snapshot: structuredClone(entry.data),
    ...(entry.version ? { snapshotVersion: entry.version } : {}),
    ...(options?.follow ? { follow: true } : {}),
  }
}

/**
 * 换规范时带走的期刊字段。按期刊选的绑定（有 `journalRef`）是「中性底 + 该期刊的覆盖」：换成别的规范就
 * **整套放下**——否则期刊覆盖会叠在课题组规范上，期刊没写的项又被课题组的数补上（ADR 0155 §2 禁止的事）。
 * 没有 `journalRef` 的旧绑定里手写的 `journal` 覆盖照旧带过去。
 */
export function journalCarry(
  profile: SpecBinding | null | undefined,
  nextId: string,
): Pick<NonNullable<Parameters<typeof bindingFor>[1]>, 'journal' | 'journalRef' | 'journalSources' | 'journalPrev'> {
  if (!profile) return {}
  if (profile.journalRef && nextId !== JOURNAL_BASE_PROFILE_ID) return {}
  return {
    journal: profile.journal,
    journalRef: profile.journalRef,
    journalSources: profile.journalSources,
    journalPrev: profile.journalPrev,
  }
}
