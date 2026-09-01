/**
 * 全局 Style / Spec profile 的**前端持有者**（Session 10，ADR 0029）。
 *
 * 它是 React 与 `/api/profiles/*` 之间唯一的一层：组件里不许出现 fetch、
 * 更不许出现磁盘格式的知识。磁盘那一侧的全部细节在
 * `src/tavotto/engine/profilestore.py`（用户数据目录、原子写、乐观并发、
 * 损坏回退）。
 *
 * **不缓存翻译后的名字。** 内置条目带 `name_key`，句子由组件在渲染那一刻
 * 按当前语言查出来；用户自己起的名字原样存、不翻译。
 *
 * **不持有"当前编辑到哪一条"。** 那是设置页自己的临时状态，进不了这里——
 * 一个 store 同时管数据和光标位置，两个窗口就会互相抢光标。
 *
 * 没有后端时（浏览器演练场 / MCP 内嵌画布）`load()` 会失败，`specs` 退回
 * **内置清单**：规范是产品的一部分，没有它连预检都说不出话；而样式清单
 * 空着就是空着——用户没建过就是没建过，不该凭空变出一条。
 */
import { create } from 'zustand'
import {
  backendErrorText,
  createProfile,
  deleteProfile,
  duplicateProfile,
  exportProfile,
  fetchProfiles,
  importProfile,
  resetProfile,
  updateProfile,
  type ProfileKind,
  type ProfileRecord,
} from '@/lib/api'
import { builtinCatalog, type SpecCatalogEntry } from '@/lib/specBinding'

/** 后端不在时的规范清单：内置那两条，`revision` 固定 1（它们本来就不可改）。 */
function builtinSpecRecords(): ProfileRecord[] {
  return builtinCatalog().map((e) => ({
    id: e.id,
    kind: 'spec' as const,
    schema_version: 1,
    revision: 1,
    display_name: e.display_name,
    name_key: e.name_key ?? '',
    version: e.version,
    created_at: 0,
    updated_at: 0,
    built_in: true,
    read_only: true,
    is_default: false,
    derived_from: '',
    warnings: [],
    data: e.data,
  }))
}

interface ProfileState {
  styles: ProfileRecord[]
  specs: ProfileRecord[]
  loaded: boolean
  loading: boolean
  /** 最近一次失败的原因（结构化 code + 后端原话），成功时清空 */
  error: { code: string; message: string } | null
  /** 乐观并发撞车时磁盘上的那一版，供界面展示「已被改过」 */
  conflict: ProfileRecord | null

  load: () => Promise<void>
  list: (kind: ProfileKind) => ProfileRecord[]
  get: (kind: ProfileKind, id: string) => ProfileRecord | null
  catalog: () => SpecCatalogEntry[]

  create: (kind: ProfileKind, name: string, data: Record<string, unknown>) => Promise<ProfileRecord | null>
  duplicate: (kind: ProfileKind, id: string, name?: string) => Promise<ProfileRecord | null>
  rename: (kind: ProfileKind, id: string, name: string) => Promise<ProfileRecord | null>
  save: (kind: ProfileKind, id: string, data: Record<string, unknown>) => Promise<ProfileRecord | null>
  remove: (kind: ProfileKind, id: string) => Promise<boolean>
  restoreDefaults: (kind: ProfileKind, id: string) => Promise<ProfileRecord | null>
  exportOne: (kind: ProfileKind, id: string) => Promise<string | null>
  importOne: (kind: ProfileKind, payload: string) => Promise<ProfileRecord | null>
  clearError: () => void
}

interface ApiFailure {
  status?: number
  body?: Record<string, unknown>
  message: string
}

function asFailure(err: unknown): ApiFailure {
  const e = err as { status?: number; body?: Record<string, unknown>; message?: string }
  return { status: e?.status, body: e?.body, message: e?.message ?? String(err) }
}

/** 请求序号：慢响应不许覆盖新的（与 assetStore / readiness 同一条纪律）。 */
let seq = 0
let applied = 0

/**
 * 清单记录 → 规范目录。**纯函数**：组件里 `useMemo([specs])` 直接用它，
 * 而不是订阅 `catalog()`——后者每次调用都新建一个数组，拿它当 zustand
 * 选择器的返回值等于"每一帧都变了"，React 会一直重渲染到报错为止。
 */
export function toCatalog(specs: ProfileRecord[]): SpecCatalogEntry[] {
  return specs.map((r) => ({
    id: r.id,
    display_name: r.display_name,
    name_key: r.name_key || undefined,
    version: r.version,
    built_in: r.built_in,
    data: r.data,
  }))
}

export const useProfileStore = create<ProfileState>((set, get) => ({
  styles: [],
  specs: builtinSpecRecords(),
  loaded: false,
  loading: false,
  error: null,
  conflict: null,

  load: async () => {
    const mine = ++seq
    set({ loading: true })
    try {
      const [styles, specs] = await Promise.all([fetchProfiles('style'), fetchProfiles('spec')])
      // **形状不对 = 没拿到，不是拿到了空的。** 一个 200 但没有 `profiles`
      // 的响应（代理、离线页、别的服务占了端口）会把内置规范一起抹掉，而
      // 界面上看起来只是"这台机器上没有规范"——最坏的那种静默。
      if (!Array.isArray(styles) || !Array.isArray(specs)) throw new Error('bad_shape')
      if (mine < applied) return
      applied = mine
      set({ styles, specs, loaded: true, loading: false, error: null })
    } catch (err) {
      if (mine < applied) return
      applied = mine
      // 后端不在：规范退回内置清单，样式保持空。**不是错误状态**——
      // 演练场里本来就没有后端，把它标红只会教用户忽略红色。
      const f = asFailure(err)
      set({
        loading: false,
        loaded: true,
        specs: builtinSpecRecords(),
        error: f.status ? errorOf(err) : null,
      })
    }
  },

  list: (kind) => (kind === 'style' ? get().styles : get().specs),

  get: (kind, id) => get().list(kind).find((r) => r.id === id) ?? null,

  catalog: () => toCatalog(get().specs),

  create: (kind, name, data) => run(set, get, kind, () => createProfile(kind, name, data)),
  duplicate: (kind, id, name) => run(set, get, kind, () => duplicateProfile(kind, id, name)),
  rename: (kind, id, name) =>
    run(set, get, kind, () => {
      const cur = get().get(kind, id)
      if (!cur) throw new Error('not_found')
      return updateProfile(kind, id, { display_name: name }, cur.revision)
    }),
  save: (kind, id, data) =>
    run(set, get, kind, () => {
      const cur = get().get(kind, id)
      if (!cur) throw new Error('not_found')
      return updateProfile(kind, id, { data }, cur.revision)
    }),
  restoreDefaults: (kind, id) => run(set, get, kind, () => resetProfile(kind, id)),
  importOne: (kind, payload) => run(set, get, kind, () => importProfile(kind, payload)),

  remove: async (kind, id) => {
    try {
      await deleteProfile(kind, id)
      set({
        ...replace(kind, get().list(kind).filter((r) => r.id !== id)),
        error: null,
        conflict: null,
      })
      return true
    } catch (err) {
      set({ error: errorOf(err), conflict: null })
      return false
    }
  },

  exportOne: async (kind, id) => {
    try {
      const payload = await exportProfile(kind, id)
      set({ error: null })
      return JSON.stringify(payload, null, 1)
    } catch (err) {
      set({ error: errorOf(err) })
      return null
    }
  },

  clearError: () => set({ error: null, conflict: null }),
}))

/**
 * 后端错误 → 结构化 + **按当前界面语言渲染**的一句话。
 * 直接用后端的 `error` 原文等于在英文界面里泄漏中文——`backendErrorText`
 * 认得 code 就查 `errors:backend.*`，认不得才原文透出。
 */
function errorOf(err: unknown): { code: string; message: string } {
  const f = asFailure(err)
  return { code: String(f.body?.code ?? 'unknown'), message: backendErrorText(err) }
}

function replace(kind: ProfileKind, next: ProfileRecord[]): Partial<ProfileState> {
  return kind === 'style' ? { styles: next } : { specs: next }
}

/**
 * 一次写操作：成功就把返回的那一条并回清单；失败记 error；乐观并发撞车时
 * 额外记下磁盘现值（`conflict`），界面据此说「这条已经被改过」而不是
 * 泛泛地说「保存失败」。
 */
async function run(
  set: (fn: Partial<ProfileState>) => void,
  get: () => ProfileState,
  kind: ProfileKind,
  op: () => Promise<ProfileRecord>,
): Promise<ProfileRecord | null> {
  try {
    const rec = await op()
    const list = get().list(kind)
    const idx = list.findIndex((r) => r.id === rec.id)
    const next = idx < 0 ? [...list, rec] : list.map((r) => (r.id === rec.id ? rec : r))
    set({ ...replace(kind, next), error: null, conflict: null })
    return rec
  } catch (err) {
    const f = asFailure(err)
    const current = f.body?.current as ProfileRecord | undefined
    set({ error: errorOf(err), conflict: current ?? null })
    return null
  }
}
