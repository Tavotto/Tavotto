/**
 * 画布级色键表（ADR 0210 §四）：「哪几条系列是同一组 → 这一组占哪个槽」，换方案只换「槽 → 颜色」。
 *
 * - 键的证据：高 = 显式 label 的身份相同（ADR 0083 的 `identity`，同名跨子图同摘要），自动成组；
 *   中 = 归一化后同名（全半角、大小写、空格）但身份不同，只给「并入」的建议，用户确认后才合；
 *   低 = 没有显式 label（manifest 没有 `identity`），列为未处理，不按位置、不按相邻去猜。
 * - 文档里只存身份摘要，不存 label 原文；组名每次从 manifest 的 `label` 字段现读。
 * - 槽一旦分配就不动：新键取最小的空槽，删键 / 重排图例 / 换图顺序都不换色。
 *
 * 全是纯函数：输入当前表与各面板的 manifest，输出下一张表与给界面看的分组。
 */
import type { Manifest, ManifestElement } from '@/lib/api'
import { PALETTE_PROP } from '@/lib/stylePresets'
import type { ColorKey, ColorKeyTable, ColorSlot, PanelObject } from '@/types/document'

export type { ColorKey, ColorKeyOwned, ColorKeyTable, ColorSlot } from '@/types/document'

/** 一条参与配色的系列（某个面板里的一个元素） */
export interface SeriesRef {
  panelId: string
  gid: string
  role: string
  /** 承接颜色的那条属性（`line.color` / `scatter.facecolor` / `bar_series.facecolor`） */
  prop: string
  /** 缺席 = 脚本没给它起显式名字（低证据） */
  identity?: string
  /** manifest 里 `label` 字段此刻的文字（只拿来显示和比对，不进文档） */
  name: string
}

/** 给界面看的一组 */
export interface ColorGroup {
  key: string
  slot: ColorSlot
  name: string
  series: SeriesRef[]
  /** 名字像对照组：建议用中性灰（只建议，不自动生效，ADR 0210 §四） */
  suggestNeutral: boolean
}

/** 中等证据：归一化后同名的两组，建议把 `from` 并入 `into` */
export interface MergeSuggestion {
  into: string
  from: string
  name: string
}

export interface Reconciled {
  table: ColorKeyTable
  groups: ColorGroup[]
  merges: MergeSuggestion[]
  /** 没有显式 label 的系列：不参与统一 */
  unlabeled: SeriesRef[]
}

function labelOf(el: ManifestElement): string {
  const v = el.editable.find((f) => f.prop === 'label')?.value
  return typeof v === 'string' ? v : ''
}

/** 按面板顺序、面板内按 manifest 顺序，列出所有参与配色的系列 */
export function collectSeries(
  panels: readonly PanelObject[],
  manifestOf: (panel: PanelObject) => Manifest | null | undefined,
): SeriesRef[] {
  const out: SeriesRef[] = []
  for (const panel of panels) {
    const manifest = manifestOf(panel)
    if (!manifest) continue
    for (const el of manifest.elements) {
      const prop = PALETTE_PROP[el.role]
      if (!prop || !el.editable.some((f) => f.prop === prop)) continue
      out.push({
        panelId: panel.id,
        gid: el.gid,
        role: el.role,
        prop,
        ...(el.identity ? { identity: el.identity } : {}),
        name: labelOf(el),
      })
    }
  }
  return out
}

/** 归一化组名：全半角（NFKC）、大小写、连续空白 */
export function normalizeName(name: string): string {
  return name.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim()
}

const NEUTRAL_NAME =
  /^(control|ctrl|ctl|vehicle|veh|wt|wild[ -]?type|sham|mock|untreated|placebo|dmso|pbs|saline|blank|对照|空白|空白对照|阴性对照|溶剂对照|野生型|假手术)( ?(group|组))?$/

/** 名字像对照组吗（只用来**建议**中性灰） */
export function looksNeutral(name: string): boolean {
  return NEUTRAL_NAME.test(normalizeName(name))
}

function usedSlots(keys: readonly ColorKey[], except?: string): Set<number> {
  const s = new Set<number>()
  for (const k of keys) if (k.key !== except && typeof k.slot === 'number') s.add(k.slot)
  return s
}

function freeSlot(keys: readonly ColorKey[], except?: string): number {
  const used = usedSlots(keys, except)
  let i = 0
  while (used.has(i)) i += 1
  return i
}

function nextKeyId(keys: readonly ColorKey[]): string {
  let n = 0
  for (const k of keys) {
    const m = /^k(\d+)$/.exec(k.key)
    if (m) n = Math.max(n, Number(m[1]))
  }
  return `k${n + 1}`
}

export function emptyTable(scheme: string): ColorKeyTable {
  return { version: 1, scheme, keys: [], owned: [] }
}

/**
 * 拿此刻的系列对一遍表：新出现的身份各成一个新键（取最小空槽），已有的键原样不动。
 * 不在任何面板里出现的键也留着（槽不让出去：脚本暂时删掉一组、回头又加回来，颜色还是原来那个）。
 */
export function reconcile(table: ColorKeyTable, series: readonly SeriesRef[]): Reconciled {
  const keys = table.keys.map((k) => ({ ...k, identities: [...k.identities] }))
  const keyOf = new Map<string, ColorKey>()
  for (const k of keys) for (const id of k.identities) keyOf.set(id, k)
  const unlabeled: SeriesRef[] = []
  const members = new Map<string, SeriesRef[]>()
  for (const s of series) {
    if (!s.identity) {
      unlabeled.push(s)
      continue
    }
    let k = keyOf.get(s.identity)
    if (!k) {
      k = { key: nextKeyId(keys), slot: freeSlot(keys), identities: [s.identity] }
      keys.push(k)
      keyOf.set(s.identity, k)
    }
    const list = members.get(k.key) ?? []
    list.push(s)
    members.set(k.key, list)
  }
  const groups: ColorGroup[] = []
  for (const k of keys) {
    const list = members.get(k.key)
    if (!list) continue
    const name = list.find((s) => s.name.trim())?.name ?? ''
    groups.push({
      key: k.key,
      slot: k.slot,
      name,
      series: list,
      suggestNeutral: k.slot !== 'neutral' && looksNeutral(name),
    })
  }
  const merges: MergeSuggestion[] = []
  const firstByName = new Map<string, ColorGroup>()
  for (const g of groups) {
    const n = normalizeName(g.name)
    if (!n) continue
    const first = firstByName.get(n)
    if (first) merges.push({ into: first.key, from: g.key, name: first.name })
    else firstByName.set(n, g)
  }
  return { table: { ...table, keys }, groups, merges, unlabeled }
}

/** 并入：`from` 的身份归到 `into`，`from` 这个键（和它的槽）不再存在 */
export function mergeKeys(table: ColorKeyTable, into: string, from: string): ColorKeyTable {
  const src = table.keys.find((k) => k.key === from)
  if (!src || into === from || !table.keys.some((k) => k.key === into)) return table
  return {
    ...table,
    keys: table.keys
      .filter((k) => k.key !== from)
      .map((k) =>
        k.key === into ? { ...k, identities: [...new Set([...k.identities, ...src.identities])] } : k,
      ),
  }
}

/** 设成 / 取消中性灰；取消时取最小空槽（原来那个槽可能已经给了别的组） */
export function setNeutral(table: ColorKeyTable, key: string, on: boolean): ColorKeyTable {
  return {
    ...table,
    keys: table.keys.map((k) => {
      if (k.key !== key) return k
      if (on) return { ...k, slot: 'neutral' }
      return k.slot === 'neutral' ? { ...k, slot: freeSlot(table.keys, key) } : k
    }),
  }
}

export function setScheme(table: ColorKeyTable, scheme: string): ColorKeyTable {
  return table.scheme === scheme ? table : { ...table, scheme }
}

/**
 * 从文档里读色键表：形状不对的整份当作没有（旧构建 / 手改过的文件），不认的条目逐条丢掉。
 * 只在读的一侧把关；写的一侧永远是本模块产出的表。
 */
export function readColorKeys(raw: unknown): ColorKeyTable | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  if (r.version !== 1 || typeof r.scheme !== 'string' || !Array.isArray(r.keys)) return null
  const seenKey = new Set<string>()
  const seenId = new Set<string>()
  const seenSlot = new Set<number>()
  const keys: ColorKey[] = []
  for (const k of r.keys as unknown[]) {
    if (!k || typeof k !== 'object') continue
    const { key, slot, identities } = k as Record<string, unknown>
    if (typeof key !== 'string' || seenKey.has(key) || !Array.isArray(identities)) continue
    const okSlot = slot === 'neutral' || (typeof slot === 'number' && Number.isInteger(slot) && slot >= 0 && !seenSlot.has(slot))
    if (!okSlot) continue
    const ids = identities.filter((x): x is string => typeof x === 'string' && !seenId.has(x))
    if (!ids.length) continue
    ids.forEach((x) => seenId.add(x))
    if (typeof slot === 'number') seenSlot.add(slot)
    seenKey.add(key)
    keys.push({ key, slot: slot as ColorSlot, identities: ids })
  }
  const owned = (Array.isArray(r.owned) ? r.owned : []).filter(
    (o): o is ColorKeyTable['owned'][number] =>
      !!o &&
      typeof o === 'object' &&
      ['panel', 'gid', 'prop', 'value'].every((f) => typeof (o as Record<string, unknown>)[f] === 'string'),
  )
  return { version: 1, scheme: r.scheme, keys, owned }
}
