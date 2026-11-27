/**
 * 修改方案「应用」的纯逻辑（ADR 0206 §四）：规范化、逐组勾选后的 override 列表、基线核对。
 *
 * **同源对**：`canonicalPatches` / `applyGroups` 与后端 `engine/patchspec.canonicalize`、`engine/changeplan.apply_groups` 是同一个定义，
 * 两侧各读 `tests/golden/change_plan_apply_vectors.json` 对拍（本侧 `changePlanApply.golden.test.ts`，后端 `tests/test_changeplan_golden.py`）。
 * 改任何一侧都要同步另一侧与向量。规则：形状不合规的条目剔除；同一 (gid, prop) 最后一条赢（整条为单位，identity 不继承）；按 (gid, prop) 的
 * Unicode 码点序排序（与 Python 的字符串比较一致，不用 UTF-16 码元序）。
 *
 * 只做算术与比较，不碰文档：写文档是 `store/changePlanStore` 经 `documentStore.commit` 的一次提交。
 */
import type { ChangePlanGroup, ChangePlanPatch } from '@/lib/api'
import type { PanelOverride } from '@/types/document'

type Json = null | boolean | number | string | Json[] | { [k: string]: Json }

function validValue(v: unknown, depth = 0): boolean {
  if (depth > 32) return false
  if (v === null || typeof v === 'boolean' || typeof v === 'string') return true
  if (typeof v === 'number') return Number.isFinite(v)
  if (Array.isArray(v)) return v.every((x) => validValue(x, depth + 1))
  if (typeof v === 'object') return Object.values(v as object).every((x) => validValue(x, depth + 1))
  return false
}

/** 按 Unicode 码点比较（Python `str` 的序）；`<` 比的是 UTF-16 码元，astral 字符会排错位 */
function cmp(a: string, b: string): number {
  const x = Array.from(a)
  const y = Array.from(b)
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0)! - y[i].codePointAt(0)!
    if (d !== 0) return d
  }
  return x.length - y.length
}

/** 规范化后的 override 列表（同后端 `patchspec.canonicalize`）；非列表 = 空 */
export function canonicalPatches(patches: unknown): ChangePlanPatch[] {
  if (!Array.isArray(patches)) return []
  const merged = new Map<string, ChangePlanPatch>()
  for (const e of patches) {
    if (e === null || typeof e !== 'object' || Array.isArray(e)) continue
    const r = e as Record<string, unknown>
    const { gid, prop } = r
    if (typeof gid !== 'string' || !gid || typeof prop !== 'string' || !prop) continue
    if (!('value' in r) || !validValue(r.value)) continue
    if ('identity' in r && (typeof r.identity !== 'string' || !r.identity)) continue
    const item: ChangePlanPatch = { gid, prop, value: r.value }
    if (typeof r.identity === 'string') item.identity = r.identity
    // JSON 数组当键：gid / prop 里出现任何字符都不会串键
    merged.set(JSON.stringify([gid, prop]), item)
  }
  return [...merged.values()].sort((a, b) => cmp(a.gid, b.gid) || cmp(a.prop, b.prop))
}

/** B0 ⊕ 勾选的那些组的 `patch`（规范化后的全量列表）；同后端 `changeplan.apply_groups` */
export function applyGroups(
  base: readonly unknown[],
  groups: readonly Pick<ChangePlanGroup, 'key' | 'changes'>[],
  keys: Iterable<string>,
): ChangePlanPatch[] {
  const chosen = new Set(keys)
  const out: unknown[] = [...base]
  for (const g of groups) if (chosen.has(g.key)) for (const c of g.changes) out.push(c.patch)
  return canonicalPatches(out)
}

/** 键序固定的序列化：同一份内容 = 同一个串（嵌套对象键序不影响比较） */
function stable(v: Json | undefined): string {
  if (Array.isArray(v)) return `[${v.map(stable).join(',')}]`
  if (v !== null && typeof v === 'object') {
    return `{${Object.keys(v)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stable(v[k])}`)
      .join(',')}}`
  }
  return JSON.stringify(v ?? null)
}

/** 两份 override 列表规范化之后是不是同一件事（基线核对、撤销前核对用） */
export function samePatches(a: readonly unknown[], b: readonly unknown[]): boolean {
  return stable(canonicalPatches(a) as unknown as Json) === stable(canonicalPatches(b) as unknown as Json)
}

/** 方案的 patch → 文档里的 `PanelOverride`（形状相同，这里只是去掉多余字段、成为新对象） */
export function toOverrides(patches: readonly ChangePlanPatch[]): PanelOverride[] {
  return patches.map((p) => ({ gid: p.gid, prop: p.prop, value: p.value, ...(p.identity ? { identity: p.identity } : {}) }))
}

/** 图源身份：方案是为「这个文件 + 这份脚本（+ 磁盘修订）」算的；换素材 / 脚本改了，方案就不再适用 */
export interface PlanSource {
  fileId: string
  script: string | null
  /** 磁盘素材的修订（mtime）；两边都有才比（还没读到不算变了） */
  mtime?: number
}

export function sameSource(a: PlanSource, b: PlanSource): boolean {
  return a.fileId === b.fileId && (a.script ?? null) === (b.script ?? null) && (a.mtime === undefined || b.mtime === undefined || a.mtime === b.mtime)
}

/** 卡片 / 弹窗 / 应用判据共用的状态来源（`PlanEntry` 的结构子集，免得 lib 反过来依赖 store） */
export interface PlanStatusInput {
  status: 'generating' | 'ready' | 'applied' | 'discarded' | 'stale' | 'failed' | 'handoff'
  /** 'layout' = 一键排版的整版方案、'vector' = 改图助手改画布矢量对象的方案：不挂在某张图上，基线由应用那一刻的 handle 自己核 */
  kind?: 'figure' | 'layout' | 'vector'
  plan?: { base_patches: readonly unknown[]; base?: { target: string }; kind?: 'script' }
  appliedPatches?: readonly unknown[]
  /** 应用那一次 commit 的历史条目（一键排版卡用它从文档历史派生状态） */
  appliedEntry?: unknown
  source?: PlanSource
}

/**
 * 卡片此刻的状态：存着的状态 + 这张图**此刻**的 override（`current`，图不在了 = null）+ 图源身份（`source`，此刻的）。
 * 待确认 / 已应用的方案以文档为准——⌘Z 把应用撤回了就又是「待确认」，重做回来又是「已应用」，
 * 既不是方案的改前也不是我们写的那份就是「图已改过」（对不上了）。**换了素材 / 脚本**（图源身份对不上，或后端记的 `base.target` 不是这个文件）
 * 同样是「图已改过」——换素材后 overrides 可能恰好也是空的，只比 override 会把为原图算的 patch 写进新图。其余状态照存着的。
 */
export function effectiveStatus(
  e: PlanStatusInput,
  current: readonly unknown[] | null,
  source?: PlanSource | null,
  /** 一键排版卡按文档历史派生状态用（调用方从 `documentStore` 取；别的卡不看） */
  history?: { past: readonly unknown[]; future: readonly unknown[] },
): PlanStatusInput['status'] {
  if ((e.status !== 'ready' && e.status !== 'applied') || !e.plan) return e.status
  // 矢量编辑方案按对象状态判（`changePlanModel.effectiveStatus`，历史条目会被之后的回写换掉）：这里原样
  if (e.kind === 'vector') return e.status
  if (e.kind === 'layout') {
    // 与审阅卡一样以文档为准：应用那次 commit 还在历史里 = 已应用；⌘Z 撤回了（在重做栈里）= 又是待确认，可再查看 / 应用；
    // 两处都不在（撤回后又改了别的）= 图已改过。从没应用过的待确认卡由 `layout.apply` 应用那一刻自己核基线
    if (!history || !e.appliedEntry) return e.status
    if (history.past.includes(e.appliedEntry)) return 'applied'
    return history.future.includes(e.appliedEntry) ? 'ready' : 'stale'
  }
  // 脚本修改方案（ADR 0206 PR 6）：应用 / 撤销都是后端写脚本，不在文档历史里——已应用就是已应用（⌘Z 不撤脚本）；
  // 待确认时脚本是否还是生成时那一版由后端在应用那一刻锁内核（对不上 409 → 卡片转「图已改过」），图源修订这里不看
  // （撤销之后脚本回到改前，同一份方案可以再应用）
  if (e.plan.kind === 'script') {
    if (e.status === 'applied') return 'applied'
    if (!current) return 'stale'
    if (source && e.plan.base && e.plan.base.target !== source.fileId) return 'stale'
    return samePatches(current, e.plan.base_patches) ? 'ready' : 'stale'
  }
  if (!current) return 'stale'
  if (source) {
    if (e.source && !sameSource(e.source, source)) return 'stale'
    if (e.plan.base && e.plan.base.target !== source.fileId) return 'stale'
  }
  if (e.status === 'applied' && e.appliedPatches && samePatches(current, e.appliedPatches)) return 'applied'
  return samePatches(current, e.plan.base_patches) ? 'ready' : 'stale'
}
