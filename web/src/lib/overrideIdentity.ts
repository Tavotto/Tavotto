/**
 * override 的目标身份（ADR 0083）。
 *
 * gid 是位置式的（`axes_i.lines_j`）：脚本重排 / 插入 / 删除曲线或子图之后，同一个
 * gid 指向了另一个对象。manifest 给有显式 label 的元素发 `identity`；一条编辑**写下的
 * 那一刻**把它抄进 patch，引擎重放时对不上就不应用、报 warning（写回一条即阻断）。
 *
 * 这里只有两件事，都不认识 store：
 *   - `stampOverrideIdentities`：文档提交之后，给**这一次新写或改了值**的 override
 *     抄上写它时那一版 manifest 的身份（元素没有身份就去掉——不许把旧身份留在新编辑上）；
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
        // 才按此刻重抄。别的都是有来历的身份——历史版本 / 布局版本恢复回来的条目带着
        // 它们写下时的身份，按此刻的 manifest 重抄等于把旧编辑按位置重新绑到新对象上，
        // 正是这道核对要堵的那条路。
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
