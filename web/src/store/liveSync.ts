/**
 * 外部修改 → 界面的那条闭环（Prompt 06）。
 *
 * ```text
 * 外部编辑器改文件
 *   → 项目 watcher（后端，ADR 0026）
 *   → 统一刷新（后端，ADR 0025）
 *   → SSE：registry.changed / assets.changed / panel.file_changed
 *   → 【本模块】合并后的素材刷新
 *   → PanelObject 派生元数据原地同步（panelSourceSync）
 *   → 受影响面板重建 / 降级面板清缓存 / 一条合并后的提示
 * ```
 *
 * **三个入口共用这一条路径**：SSE 事件、素材面板的「刷新项目」按钮、SSE
 * 重连恢复。三条各写一遍的话，「刷新之后要不要同步文档」这件事就有三份
 * 判据，而第三份总会漏掉——本仓库已经在别处付过这个学费。
 */
import { refreshProject } from '@/lib/api'
import { msg } from '@/i18n'
import { useAssetStore } from './assetStore'
import { syncPanelSourceMetadata, type PanelSyncResult } from './panelSourceSync'
import { useProjectReadinessStore } from './projectReadinessStore'
import { useProjectStore } from './projectStore'
import { useRenderStore } from './renderStore'
import { useRuntimeAssetStore } from './runtimeAssetStore'
import { previewSession, resetPreview } from './svgPreviewStore'
import { useUiStore } from './uiStore'
import { enterElementEdit } from './actions'
import { useWorkspaceStore } from './workspace'

/**
 * 同步结果 → 渲染层 / 编辑态 / 提示。
 *
 * **画布选择一个字都不动**：`selectionStore` 在这里既不读也不写。用户选中的
 * 是画布上的对象，一次外部文件变化不改变"他选中了什么"。
 */
function applyPanelSync(result: PanelSyncResult): void {
  const render = useRenderStore.getState()

  // 升级 / 派生字段变了：转入引擎跟踪并作废旧变体。判据里已经排除了降级的
  // 那些——没有脚本的面板重建它没有意义。
  if (result.staleFileIds.length) {
    render.markStale(result.staleFileIds)
    useRuntimeAssetStore.getState().invalidate(result.staleFileIds)
  }

  // 降级：**渲染缓存整份丢掉**。只把 `script` 抹成 null 是不够的——留着的
  // manifest 会让元素树、命中几何、检查器继续按"这张图可参数化"办事，
  // 界面上看起来一切正常，直到用户点下去才发现后面没有东西。
  for (const fileId of result.droppedFileIds) render.reset(fileId)

  const ui = useUiStore.getState()
  const editing = ui.elementPanelId
  const editingLost = !!editing && result.downgraded.includes(editing)
  if (editingLost) {
    // 正在编辑的这张图失去了源脚本：退回画布。overrides **一条都不删**
    // ——那是用户的编辑，源关系恢复之后它们还要用。
    if (previewSession()?.panelId === editing) resetPreview()
    ui.setElementPanel(null) // 顺带清 selectedGids 与 cropTarget
  }

  // 升级方向（issue #267）。降级那一侧一直有人管（上面：源脚本没了就退出
  // 图内编辑），**升级这一侧从来没有**——`upgraded` 只换来一句提示。于是
  // 「用户双击时还没关联上」这条路上，他要的图内编辑再也不会到来。
  //
  // 只补 `openFastEdit` 明确记下的那一个待办，而且要求**用户还停在那张图上**
  // （`activePanelId`）、**此刻不在图内编辑**：迟到的关联不许把界面从已经走开
  // 的用户手里抢走。
  const wsStore = useWorkspaceStore.getState()
  const pending = wsStore.pendingElementEdit
  if (pending && result.upgraded.includes(pending)) {
    wsStore.setPendingElementEdit(null)
    // 读**此刻**的 uiStore，不是函数开头那份快照：上面的降级分支可能刚改过它
    if (wsStore.activePanelId === pending && useUiStore.getState().elementPanelId == null) {
      enterElementEdit(pending)
    }
  }

  if (editingLost) {
    ui.setStatus(msg('status.sourceLostEditing', undefined, 'workspace'), 'error')
  } else if (result.downgraded.length) {
    ui.setStatus(msg('status.sourceLost', { count: result.downgraded.length }, 'workspace'))
  } else if (result.upgraded.length) {
    ui.setStatus(msg('status.sourceLinked', { count: result.upgraded.length }, 'workspace'))
  }
}

/**
 * 合并后的素材刷新 + 派生元数据同步。
 *
 * 「合并」有两层，缺一层都不够：
 *  1. `assetStore.load()` 把同一批事件里的多次调用并成**一个** /api/panels 请求；
 *  2. `syncPanelSourceMetadata()` 无差异时零改动——所以并不成一个请求的那些
 *     （比如第二条事件晚到一步）也不会重复置 dirty、重复弹提示。
 *
 * 返回 `null` = 这一轮没有权威数据可用（请求失败、或响应属于已经切走的项目）。
 * 那时**什么都不做**：旧素材、旧文档原样留着，下一条事件会再来一次。
 */
export async function refreshAssetsAndSync(opts?: {
  force?: boolean
  affectedIds?: readonly string[]
}): Promise<PanelSyncResult | null> {
  // 就绪度报告与素材清单是同一份事实的两个投影（后端同一次 compute()）：
  // 素材要重取的每一个时机，报告也一样过期了。挂在这里而不是各个事件分支上
  // ——「前端的消费只有 liveSync 一份」是 Session 06 定下的不变式，报告
  // 另开一条消费路径的话，第三个触发点总会漏掉其中一条。
  //
  // **不 await**：横幅与接入中心晚一拍出现没有关系，而画布上的面板同步
  // 不该等一个诊断端点。合并由 store 自己的在途去重负责（一批事件一次请求），
  // `force` 与素材那一侧同义——用户刚按过刷新 / 刚写过盘时绝不复用一个
  // **在那之前**就发出的在途请求。
  void useProjectReadinessStore.getState().load({ force: opts?.force })
  const data = await useAssetStore.getState().load({ force: opts?.force })
  if (!data) return null
  const result = syncPanelSourceMetadata(
    useAssetStore.getState().byId,
    opts?.affectedIds ? { affectedIds: opts.affectedIds } : undefined,
  )
  applyPanelSync(result)
  return result
}

/**
 * 「刷新项目 / 检查新文件」按钮：调**统一刷新**，不是自己再扫一遍。
 *
 * 后端那一条会做完整的一轮（静态扫描 → 合并注册表 → 比对素材 → 发事件），
 * 事件回来时本模块的 SSE 分支会做同步。响应之后**再强制刷一次**是丢事件时的
 * 兜底（SSE 断着、或这一轮后端判定无差异因而一条事件都不发）。
 *
 * 这里刻意用 `force`，代价是事件正常到达时会多一个 `/api/panels`：合并会复用
 * 一个**可能在后端那一轮结束之前就发出**的在途请求，那份数据比按钮按下的那
 * 一刻还旧——用户按刷新，拿回一份比刷新更早的清单，是这个按钮最不该有的
 * 行为。一次目录列举换一个不会撒谎的按钮。
 *
 * 与 RegistryDialog 的「手工扫描」是两件事：那一条是给冲突裁决用的高级入口，
 * 这一条是普通用户的「我刚在外面改了东西，看一眼」。
 */
export async function refreshProjectNow(): Promise<void> {
  try {
    await refreshProject('manual')
  } finally {
    // 后端失败时也要刷一次素材：失败的可能只是静态扫描那一步，而磁盘上的
    // 素材清单照样读得到。刷不出来的话按钮点完界面毫无变化。
    await refreshAssetsAndSync({ force: true })
  }
}

/**
 * 文档刚装载完：拿**手里已有的**那份素材清单对一次账，**不发请求**。
 *
 * 项目打开也是 `load()` 的七个触发点之一，而它是唯一一个「文档比清单晚到」的
 * ——Tavotto 关着的时候用户完全可能在外面改脚本，而项目打开那一轮 watcher
 * 只建基线、一条事件都不发。不对这一次账的话，那些改动要等到下一次外部修改
 * 或用户手动刷新才生效，而用户看到的现象是"我明明加了脚本，它就是不认"。
 *
 * **这里不需要一个「清单取回来了没有」的守卫**：清单还没到时 `byId` 是空的，
 * 每个面板都会被判成"素材不在清单里"，而那一档本来就一个字节都不改（T-28）。
 * 加一个守卫只会多一条没有任何用例能分辨的分支——空门禁比没有门禁更坏。
 */
export function syncLoadedDocument(): void {
  applyPanelSync(syncPanelSourceMetadata(useAssetStore.getState().byId))
}

/* -------------------------------------------------------------------------- */
/*  SSE 重连恢复                                                               */
/* -------------------------------------------------------------------------- */

/** 重连恢复的节流窗口：一次网络抖动会在几秒内连着重连好几回 */
const RECONNECT_THROTTLE_MS = 3000
let lastRecovery = 0

/**
 * SSE 重新连上了：断线期间发生的事件全都没收到，补一次。
 *
 * **不调后端的静态刷新**——那会扫磁盘，而网络抖动一次就扫一遍是拿用户的
 * 磁盘给自己的连接质量买单。这里只重取素材清单（一个只读端点）并同步文档，
 * 足以恢复"哪张图现在可编辑"这件事；真正的文件变化由 watcher 在下一轮
 * 自己发现。
 */
export function recoverAfterReconnect(): void {
  if (useProjectStore.getState().phase !== 'open') return
  const now = Date.now()
  if (now - lastRecovery < RECONNECT_THROTTLE_MS) return
  lastRecovery = now
  void refreshAssetsAndSync()
}

/** 只给测试用：节流是模块级状态，活得比一个用例长 */
export function resetReconnectThrottle(): void {
  lastRecovery = 0
}
