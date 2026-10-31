/**
 * override 的目标身份（ADR 0083）。
 *
 * gid 是位置式的（`axes_i.lines_j`）：脚本重排 / 插入 / 删除曲线或子图之后，同一个
 * gid 指向了另一个对象。manifest 给有显式 label 的元素发 `identity`；一条编辑**写下的
 * 那一刻**把它抄进 patch，引擎重放时对不上就不应用、报 warning（写回一条即阻断）。
 *
 * 这里只有两件事，都不认识 store：
 *   - `stampOverrideIdentities`：文档提交之后，给**这一次新写或改了值**的 override
 *     抄上写它时那一版 manifest 的身份（元素没有身份就去掉——不许把旧身份留在新编辑上）。
 *     只管**用户编辑**的提交：从存储原样读回来的（布局版本 / 写回历史 / 写回基线）由调用方
 *     显式标 `commit(…, { overrides: 'restored' })`，整次提交根本不进这里——「恢复」从数据
 *     里推断不出来（同一身份、值不同的恢复与原地改值长得一模一样，#602 评审 P1）；
 *   - `isStaleIdentityOverride`：这条 override 的身份与此刻 manifest 对不上 = 失效修改。
 *
 * 判据与引擎 `overrides.apply` 同一条：带了认识的方案前缀、gid 此刻还在、身份不等。
 * 不认识的前缀不核对（引擎同样不核对）。
 */
import type { Manifest } from '@/lib/api'
import type { FigureDocument, PanelObject, PanelOverride } from '@/types/document'

/** 引擎 `overrides.IDENTITY_SCHEME`：目前唯一认识的方案（label 摘要）。 */
export const IDENTITY_SCHEME = 'l1:'

const keyOf = (o: { gid: string; prop: string }) => `${o.gid}\u0000${o.prop}`

/** 值是否变了：override 的值都是 JSON 值，按序列化比（与渲染键同一把尺子）。 */
const sameValue = (a: unknown, b: unknown) => a === b || JSON.stringify(a) === JSON.stringify(b)

/**
 * 在 immer 草稿上给新写 / 改了值的 override 抄身份。
 *
 * `base` 是提交之前的文档、`next` 是提交之后的（都是冻结的普通对象）：结构共享
 * 保证没动过的面板 `overrides` 数组引用不变，所以逐面板先比引用，只有真改了的
 * 那几块才逐条看——拖动的每一帧都经过这里，不能每帧把所有面板扫一遍。
 * `manifestFor(panel)` 给出**提交之前**用户看着的那一版 manifest（显示用的就行：
 * 身份是脚本结构的事实，与 overrides 无关）；拿不到就不动（没见过的对象无从抄起）。
 */
export function stampOverrideIdentities(
  draft: FigureDocument,
  base: FigureDocument,
  next: FigureDocument,
  manifestFor: (panel: PanelObject) => Manifest | null | undefined,
): void {
  const baseById = new Map(base.objects.map((o) => [o.id, o]))
  next.objects.forEach((obj, i) => {
    if (obj.type !== 'panel') return
    const before = baseById.get(obj.id)
    if (before?.type !== 'panel' || before.overrides === obj.overrides) return
    const manifest = manifestFor(before)
    if (!manifest) return
    const identities = new Map<string, string | undefined>()
    for (const el of manifest.elements) identities.set(el.gid, el.identity)
    const old = new Map(before.overrides.map((o) => [keyOf(o), o]))
    obj.overrides.forEach((o, j) => {
      const prev = old.get(keyOf(o))
      const unchanged = !!prev && sameValue(prev.value, o.value)
      const target = () => (draft.objects[i] as PanelObject).overrides[j]
      if (o.identity !== undefined) {
        // 自带身份的：只有「原地改值、身份是从旧条目原样继承来的」（upsert 的展开写法）
        // 才按此刻重抄。带着别的身份来的是有来历的，不重抄（防线；恢复本身不靠这一条
        // 认——它们整次提交不进这里，见文件头）。
        if (!prev || prev.identity !== o.identity || unchanged) return
      } else if (unchanged) {
        // 值没变、身份却丢了：是哪条写法把条目重建成了 {gid, prop, value}
        // （filter + push 同值）。还是原来那条编辑，身份照旧
        if (prev.identity !== undefined) target().identity = prev.identity
        return
      }
      if (!identities.has(o.gid)) return
      const want = identities.get(o.gid)
      const t = target()
      if (want) {
        if (t.identity !== want) t.identity = want
      } else if ('identity' in t) {
        delete t.identity
      }
    })
  })
}

/** 这条 override 带着的身份与此刻 manifest 里那个 gid 的对不上（引擎不会应用它）。 */
export function isStaleIdentityOverride(o: PanelOverride, manifest: Manifest): boolean {
  if (typeof o.identity !== 'string' || !o.identity.startsWith(IDENTITY_SCHEME)) return false
  const el = manifest.elements.find((e) => e.gid === o.gid)
  if (!el) return false // gid 整个没了是另一条判据（孤儿 override）
  return el.identity !== o.identity
}

/**
 * 后端报的“改名”（`ai.done.task.renamed` / 撤销回应的 `renamed`，ADR 0083 §五）：AI 改图时某个元素的数据逐部件没动、
 * 只有显式 label 变了，身份（label 的摘要）因此从 `from_identity` 变成 `to_identity`。**只有身份摘要，没有 label 原文。**
 */
export interface IdentityRename {
  stem: string
  gid: string
  from_identity: string
  to_identity: string
  /** 提交后验证实际用的运行配置引用（`rc_…`，不透明 id）；缺席 = 无参数运行。运行时面板只在配置相同时才跟着改。 */
  run_config?: string
}

/** 方向：`forward` = 旧身份 → 新身份（AI 改完）；`back` = 新身份 → 旧身份（撤销这次修改）。 */
export type RebindDirection = 'forward' | 'back'

/**
 * 在 `objects`（immer 草稿）上把「写在被改名元素上的 override」的 `identity` 改写成改名后的身份，回改了几条。
 *
 * 为什么要改写：引擎重放时拿 override 的 `identity` 与 gid 此刻指向的对象的身份比，对不上就当“编辑的对象已找不到”拒绝
 * （ADR 0083 §一）。AI 改名之后，用户在画布上已有的编辑还写着旧名字的摘要——不改写的话，一次改图例就让这条线上的全部
 * 编辑静默失效。数据没动（后端用数据指纹确认过）所以它还是同一个元素。
 *
 * **幂等、只碰该碰的**：只改 `gid` 相同、且 `identity` **恰好等于**当前方向的起点身份的条目——没带身份的（按位置）、
 * 带着别的身份的、已经是目标身份的（重复调用 / 另一个窗口已经改过）一律不动。`stemMatches(panel, stem)` 决定哪些面板属于
 * 那张图（同一张图可以在画布上摆多份）。
 */
export function rebindOverrideIdentities(
  objects: FigureDocument['objects'],
  renames: readonly IdentityRename[],
  direction: RebindDirection,
  stemMatches: (panel: PanelObject, stem: string, rename: IdentityRename) => boolean,
  /** false = 只数不改（预检：没有要改的就不必开一条历史；`objects` 可以是冻结的） */
  apply = true,
): number {
  let n = 0
  for (const obj of objects) {
    if (obj.type !== 'panel') continue
    for (const r of renames) {
      if (!stemMatches(obj, r.stem, r)) continue
      const [from, to] = direction === 'forward' ? [r.from_identity, r.to_identity] : [r.to_identity, r.from_identity]
      for (const o of obj.overrides) {
        if (o.gid === r.gid && o.identity === from) {
          if (apply) o.identity = to
          n++
        }
      }
    }
  }
  return n
}

/** 把后端给的 `renamed` 收成闭集形状（脏数据 / 老后端没有这个键 → 空）。 */
export function parseRenames(raw: unknown): IdentityRename[] {
  if (!Array.isArray(raw)) return []
  const out: IdentityRename[] = []
  for (const r of raw) {
    if (!r || typeof r !== 'object') continue
    const { stem, gid, from_identity: from, to_identity: to } = r as Record<string, unknown>
    if ([stem, gid, from, to].every((v) => typeof v === 'string' && v !== '') && from !== to) {
      const rc = (r as Record<string, unknown>).run_config
      out.push({
        stem: stem as string,
        gid: gid as string,
        from_identity: from as string,
        to_identity: to as string,
        ...(typeof rc === 'string' && rc ? { run_config: rc } : {}),
      })
    }
  }
  return out
}
