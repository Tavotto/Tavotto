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

/** 内置规范的清单（没有后端时也拿得到——浏览器演练场 / MCP 内嵌画布都靠它）。 */
export function builtinCatalog(): SpecCatalogEntry[] {
  const d = doc as unknown as ProfileDoc
  return Object.values(d.profiles).map((p) => ({
    id: p.profile_id,
    display_name: p.label,
    name_key: `builtin.spec.${p.profile_id}`,
    version: p.version,
    built_in: true,
    data: p as unknown as Record<string, unknown>,
  }))
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
  const global = binding?.id ? catalog.find((e) => e.id === binding.id) : undefined
  const snapshot = binding?.snapshot
  const follow = binding?.follow === true
  const globalMissing = !!binding?.id && !global
  const snapshotVersion = binding?.snapshotVersion ?? null
  const globalVersion = global?.version ?? null

  if (snapshot && !follow) {
    return {
      profile: withJournal(snapshot, binding?.journal),
      source: 'snapshot',
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
      updateAvailable: false,
      globalMissing: true,
      globalVersion: null,
      snapshotVersion,
    }
  }
  return {
    profile: loadProfile(binding?.id ?? DEFAULT_PROFILE_ID, journal),
    source: 'builtin',
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
  options?: { journal?: Record<string, unknown>; follow?: boolean },
): SpecBinding {
  const journal = options?.journal
  return {
    id: entry.id,
    ...(journal && Object.keys(journal).length ? { journal: structuredClone(journal) } : {}),
    snapshot: structuredClone(entry.data),
    ...(entry.version ? { snapshotVersion: entry.version } : {}),
    ...(options?.follow ? { follow: true } : {}),
  }
}
