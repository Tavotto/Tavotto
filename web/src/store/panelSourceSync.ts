/**
 * 画布对象的**外部派生元数据**同步（Prompt 06）。
 *
 * 用户在外部编辑器里给一张已经放在画布上的图补了脚本，Tavotto 必须让那张
 * 面板**原地**变成可编辑的——不是让用户删掉重加。反过来脚本被删掉时也必须
 * 原地降级，而不是让界面继续摆着一个双击进去什么都没有的编辑入口。
 *
 * ## 派生字段 vs 用户数据
 *
 * `PanelObject` 上的字段有两种来源，同步只碰第一种：
 *
 * | 来源 | 字段 | 谁说了算 |
 * | --- | --- | --- |
 * | 磁盘 / registry | `script` `cost` `fileKind` `pxW` | `/api/panels` |
 * | 用户 | `x` `y` `w` `h` `crop` `rotation` `overrides` `groupId` `locked` `hidden` `name` `opacity` `flipH` `flipV` `lockedGids` `aspectLocked` `layoutPinned` | 只有用户 |
 *
 * **`nativeW` / `nativeH` 刻意不在第一列**，尽管 `/api/panels` 也报图幅：
 * 它们是几何——改了会经 `useEngineSync` 那个 effect 把面板的 `h` 一起调走，
 * 那正是「绝不修改几何」要挡的事。图幅的权威是**这个变体自己**渲染回来的
 * manifest（`size_mm` 本身可以被 override），不是磁盘上那份文件的原始尺寸。
 *
 * **runtime 面板整个跳过**：它的 `fileId` 是 `runtime:` 前缀的不透明 id，
 * 磁盘上没有对应文件，也就不会出现在 `/api/panels` 里。不跳过的话每一次
 * 同步都会把它们判成「素材不见了」。
 *
 * ## 与保存 / 撤销的关系
 *
 * 走 `applyDerivedUpdate()`：不进撤销历史、不推 `saveState`，但**会**置
 * `dirty` 并排一次自动保存——`script` 是存进文档的字段，只改内存的话用户
 * 下次打开这份文档，面板又回到不可编辑。
 *
 * **无差异 = 零改动**：算不出差异就一个 `set()` 都不发（与后端统一刷新
 * 「无差异 = 零事件、零写盘」同一条纪律）。一批事件里的第二条、第三条
 * 因此天然不会重复置 dirty，也不会重复弹提示。
 */
import type { PanelInfo } from '@/lib/api'
import type { CanvasData, CanvasObject, PanelObject } from '@/types/document'
import { isRuntimePanel } from '@/types/document'
import { applyDerivedUpdate, useDocumentStore } from './documentStore'

export interface PanelSyncResult {
  /** 对象 id：不可编辑 → 可编辑（脚本关系建立） */
  upgraded: string[]
  /** 对象 id：可编辑 → 不可编辑（脚本被删 / 解绑 / 路径失效） */
  downgraded: string[]
  /** 对象 id：任意派生字段变了（`upgraded` 与 `downgraded` 都是它的子集） */
  changed: string[]
  /** 对象 id：文档引用了这个素材，而本次清单里没有它（缺失素材语义） */
  missing: string[]
  /**
   * 需要按新脚本重建的**素材 id**。渲染层按文件粒度工作（`renderStore` 的
   * `tracked` / `markStale` 都是文件级），所以这一维不能给对象 id。
   */
  staleFileIds: string[]
  /** 降级面板的素材 id：失效的 manifest / 渲染缓存要按它清 */
  droppedFileIds: string[]
}

const emptyResult = (): PanelSyncResult => ({
  upgraded: [],
  downgraded: [],
  changed: [],
  missing: [],
  staleFileIds: [],
  droppedFileIds: [],
})

/** 这次同步动了什么；`null` = 这个对象一个派生字段都没变 */
function derivedPatchOf(o: PanelObject, info: PanelInfo): Partial<PanelObject> | null {
  const patch: Partial<PanelObject> = {}
  // `script` 的两种"没有"（`null` 与字段缺席）在文档里是同一件事，
  // 比较前先归一——否则老文档里缺席的那些每一轮都会被判成"变了"。
  if ((o.script ?? null) !== (info.script ?? null)) patch.script = info.script ?? null
  if (o.cost !== info.cost) patch.cost = info.cost
  if (o.fileKind !== info.kind) patch.fileKind = info.kind
  if (o.pxW !== info.px_w) patch.pxW = info.px_w
  return Object.keys(patch).length ? patch : null
}

interface Acc {
  upgraded: string[]
  downgraded: string[]
  changed: string[]
  missing: string[]
  stale: Set<string>
  dropped: Set<string>
}

/**
 * 一批对象过一遍同步。返回新数组，**没有任何对象变化时返回 `null`**
 * ——引用不变，React 那边一次重渲染都不会发生。
 */
function syncObjects(
  objects: readonly CanvasObject[],
  panelsById: Record<string, PanelInfo>,
  affected: ReadonlySet<string> | null,
  acc: Acc,
): CanvasObject[] | null {
  let touched = false
  const next = objects.map((o) => {
    if (o.type !== 'panel' || isRuntimePanel(o)) return o
    if (affected && !affected.has(o.fileId)) return o
    const info = panelsById[o.fileId]
    if (!info) {
      // 素材不在清单里：**对象原样保留**（几何、overrides、成组一个不动）。
      // 也不把 `script` 抹掉——"文件暂时不见了"与"脚本关系失效了"是两件事，
      // 混成一件的话，网盘掉线一次就会让一批面板永久失去编辑入口。
      acc.missing.push(o.id)
      return o
    }
    const patch = derivedPatchOf(o, info)
    if (!patch) return o
    touched = true
    acc.changed.push(o.id)
    if ('script' in patch) {
      if (patch.script) {
        acc.upgraded.push(o.id)
      } else {
        acc.downgraded.push(o.id)
      }
    }
    // 素材粒度的处置有**三档**，不是两档：
    //   有脚本   → 重建（`markStale`，转入引擎跟踪）
    //   刚失去   → 清缓存（`renderStore.reset`，失效的 manifest 不能留）
    //   从来没有 → 两样都不做
    // 第三档单列出来的理由：一张普通位图的 `pxW` 变了也会走到这里，把它
    // 标成 tracked 等于告诉显示层"这张图要走引擎产物"，而它根本没有脚本可跑。
    const nextScript = 'script' in patch ? (patch.script ?? null) : (o.script ?? null)
    if (nextScript) acc.stale.add(o.fileId)
    else if ('script' in patch) acc.dropped.add(o.fileId)
    return { ...o, ...patch }
  })
  return touched ? next : null
}

/**
 * 把 `/api/panels` 的权威数据同步进当前文档的全部画布。
 *
 * `options.affectedIds` 给的是**素材 id**（`assets.changed` 的 `ids`）：给了
 * 就只看这些素材的面板。不给 = 全量比对，代价只是遍历几十个对象，没有请求。
 */
export function syncPanelSourceMetadata(
  panelsById: Record<string, PanelInfo>,
  options?: { affectedIds?: readonly string[] },
): PanelSyncResult {
  const acc: Acc = {
    upgraded: [],
    downgraded: [],
    changed: [],
    missing: [],
    stale: new Set(),
    dropped: new Set(),
  }
  const affected = options?.affectedIds ? new Set(options.affectedIds) : null
  if (affected && affected.size === 0) return emptyResult()

  const s = useDocumentStore.getState()
  const nextObjects = syncObjects(s.doc.objects, panelsById, affected, acc)

  // 非激活画布同样要同步：面板在哪张画布上不改变"这张图现在可不可编辑"。
  // **激活画布跳过**——`canvases[activeCanvasId]` 只是最后同步的快照，权威在
  // `doc`，两边都走一遍只会把同一个对象 id 报两次。
  let canvasesTouched = false
  const canvases: CanvasData[] = s.canvases.map((c) => {
    if (c.id === s.activeCanvasId) return c
    const objs = syncObjects(c.objects, panelsById, affected, acc)
    if (!objs) return c
    canvasesTouched = true
    return { ...c, objects: objs }
  })

  if (nextObjects || canvasesTouched) {
    applyDerivedUpdate({
      ...(nextObjects ? { doc: { ...s.doc, objects: nextObjects } } : {}),
      ...(canvasesTouched ? { canvases } : {}),
    })
  }

  return {
    upgraded: acc.upgraded,
    downgraded: acc.downgraded,
    changed: acc.changed,
    missing: acc.missing,
    staleFileIds: [...acc.stale],
    droppedFileIds: [...acc.dropped],
  }
}
