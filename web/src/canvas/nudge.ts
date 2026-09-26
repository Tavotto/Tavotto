/**
 * 方向键微调（ADR 0093）。
 *
 * **一段连续按键 = 一次移动**：按下第一下开一段，按住连发、连着点按都并进这一段；
 * 方向键全部松开且停顿 `NUDGE_QUIET_MS` 之后才收尾——一条撤销、图内元素一次权威
 * 渲染。收尾的出口还有：离散动作（`finishActiveGesture`，含撤销重做）、按下指针、
 * 选区变了、窗口失焦、按了别的键（`useKeyboard` 调 `finishNudge`）。
 *
 * 两类对象，两条既有的路径，都不另写移动规则：
 *   - 画布对象（面板 / 文字 / 形状）：与鼠标拖动同一套可移动判据（`draggableSelection`：
 *     锁定跳过、组内有锁定成员整组不动、隐藏的不动），一段 = 一个 documentStore 事务；
 *   - 图内元素（图内编辑态里选中的标题、图例、文字、箭头、子图……）：与鼠标拖动同一个
 *     `InFigureMove`（带随行元素、带形状里的内容、子图贴边钳位、净位移为零不写）。
 *     这一段里只动预览平面（SVG DOM），收尾才写 override——写回事务与渲染的次数按段算，
 *     不按键算。
 *
 * 步长是**页面 mm**（与画布对象的坐标、读数盒同一个单位）：普通 0.5、⇧ 5、⌥ 0.1。
 * 图内元素按面板在页面上的实际大小换成分数位移，所以「按一下挪 0.5 mm」在页面上量得到。
 *
 * 不吸附：步长固定的移动一吸就被拽回参考线上，离不开、也走不到想要的那一格。
 */
import { alignEntries, isElementHidden, panelFullRect } from '@/lib/elementGeom'
import { msg } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { registerGesture } from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { displayedExactManifest, useMountedSvgStore } from '@/store/mountedSvgStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { moveLabel, warnBlockedGroups } from '@/store/actions'
import { panelRotation, unrotateVec, type PanelObject } from '@/types/document'
import { draggableSelection, groupMove, inFigureMoveOf, type InFigureMove } from './interactions'

/** 步长（页面 mm）：普通 / ⇧ 大步 / ⌥ 细调 */
export const NUDGE_STEP_MM = { base: 0.5, large: 5, fine: 0.1 } as const

/** 方向键全部松开后停顿多久算这一段结束 */
export const NUDGE_QUIET_MS = 400

/**
 * 图内元素：上一段刚提交、权威渲染还没回来时，几何写操作不许做（ADR 0017）。
 * 这一段的按键先记着位移（读数盒照常跟），权威一到再开始移动；等这么久还没来就放弃。
 */
export const NUDGE_AUTHORITY_WAIT_MS = 10_000

const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
}

/** 这一下按键的步长（页面 mm）。⇧ 优先于 ⌥ */
export function nudgeStepMm(e: { shiftKey: boolean; altKey: boolean }): number {
  if (e.shiftKey) return NUDGE_STEP_MM.large
  if (e.altKey) return NUDGE_STEP_MM.fine
  return NUDGE_STEP_MM.base
}

interface BurstBase {
  /** 这一段推的是谁（`currentTargetKey`）；选区一变这一段就收 */
  key: string
  /** 这一段累计的位移（页面 mm，y 向下） */
  dx: number
  dy: number
  unregister: () => void
  cleanup: () => void
}

interface LayoutBurst extends BurstBase {
  kind: 'layout'
  origin: Map<string, { x: number; y: number }>
}

interface FigureBurst extends BurstBase {
  kind: 'figure'
  panelId: string
  gids: string[]
  /** null = 还在等几何权威（见 NUDGE_AUTHORITY_WAIT_MS） */
  mover: InFigureMove | null
  /** 页面 mm 位移 → 内容分数位移（按这一段开始时面板的大小与旋转） */
  toFrac: (dx: number, dy: number) => [number, number]
  unwatchAuthority: (() => void) | null
  waitTimer: ReturnType<typeof setTimeout> | null
  /** 等权威期间这一段已经停下来了：权威一到就直接提交 */
  settleOnAuthority: boolean
}

type Burst = LayoutBurst | FigureBurst

let burst: Burst | null = null
let quietTimer: ReturnType<typeof setTimeout> | null = null
/** 此刻按着的方向键：按住时系统连发的首延迟可能长于 NUDGE_QUIET_MS，不能靠计时器判「停了」 */
const held = new Set<string>()

const inFastEdit = () => useWorkspaceStore.getState().mode === 'fast_edit'
const interaction = () => useInteractionStore.getState()
const status = (key: string) =>
  useUiStore.getState().setStatus(msg(`status.${key}`, undefined, 'workspace'))

/** 此刻方向键该推的是谁：图内编辑态只推图内选中的元素，否则推画布选区 */
function currentTargetKey(): string | null {
  const ui = useUiStore.getState()
  if (ui.elementPanelId) {
    return ui.selectedGids.length ? `figure:${ui.elementPanelId}:${ui.selectedGids.join('|')}` : null
  }
  // 快速编辑这一屏上只有这张图、没有版面：推面板在版上的 x/y 用户既看不见它动，
  // 也不知道自己动了它——退出之后才发现图挪了位置
  if (inFastEdit()) return null
  const ids = useSelectionStore.getState().ids
  return ids.length ? `layout:${ids.join('|')}` : null
}

/**
 * 方向键按下（`useKeyboard` 已经排除了输入框、对话框、自己处理方向键的控件）。
 * 回 true = 这一下归微调（调用方 preventDefault，免得界面滚动）。
 */
export function nudgeKeyDown(e: KeyboardEvent): boolean {
  const dir = ARROWS[e.key]
  if (!dir) return false
  // 指针拖动进行中：那一段有自己的事务与预览，键盘不插手
  if (interaction().kind !== 'none') return true
  const ui = useUiStore.getState()
  const key = currentTargetKey()
  if (!key) {
    // 图内编辑态 / 快速编辑里没选中图内元素：吃掉这个键（不让它冒出去滚界面），
    // 什么都不改——面板在版上的 x/y 不归这里推
    finishNudge()
    return inFastEdit() || !!ui.elementPanelId
  }
  if (burst && burst.key !== key) finishNudge()
  if (!burst && !beginBurst(key)) return true

  held.add(e.key)
  const step = nudgeStepMm(e)
  const b = burst!
  b.dx += dir[0] * step
  b.dy += dir[1] * step
  apply(b)
  armQuietTimer()
  return true
}

/** 方向键松开：全部松开后开始数停顿 */
export function nudgeKeyUp(e: KeyboardEvent): void {
  if (!held.delete(e.key)) return
  if (burst && !held.size) armQuietTimer()
}

/** 这一段在不在（测试与调试用） */
export const nudgeActive = (): boolean => burst != null

function armQuietTimer(): void {
  if (quietTimer) clearTimeout(quietTimer)
  quietTimer = setTimeout(() => {
    quietTimer = null
    // 还按着：系统连发还没开始而已，这一段没完
    if (held.size) armQuietTimer()
    else settleBurst()
  }, NUDGE_QUIET_MS)
}

/** 停顿够了：正常收尾；图内这一段还在等几何权威时，改成权威一到就提交 */
function settleBurst(): void {
  const b = burst
  if (b?.kind === 'figure' && !b.mover) b.settleOnAuthority = true
  else finishNudge()
}

function beginBurst(key: string): boolean {
  const common = {
    key,
    dx: 0,
    dy: 0,
    unregister: () => {},
    cleanup: () => {},
  }
  let b: Burst
  if (key.startsWith('figure:')) {
    const ui = useUiStore.getState()
    const panel = findPanel(ui.elementPanelId!)
    if (!panel) return false
    const full = panelFullRect(panel)
    const rot = panelRotation(panel)
    b = {
      ...common,
      kind: 'figure',
      panelId: panel.id,
      gids: [...ui.selectedGids],
      mover: null,
      // 面板可能被旋转过：页面上的位移先转回内容坐标系，再按面板在页面上的实际大小折成分数
      toFrac: (dx, dy) => {
        const [cx, cy] = unrotateVec(dx, dy, rot)
        return [cx / full.w, cy / full.h]
      },
      unwatchAuthority: null,
      waitTimer: null,
      settleOnAuthority: false,
    }
    const made = startMover(b)
    if (made === 'unmovable') {
      status('nudgeNotMovable')
      return false
    }
  } else {
    // 与鼠标拖动同一套可移动判据：锁定跳过、组内有锁定成员整组不动、隐藏的不动
    const { targets, blockedGroups } = draggableSelection()
    warnBlockedGroups(blockedGroups, targets.length > 0)
    if (!targets.length) return false
    useDocumentStore.getState().beginTxn(moveLabel(targets.length))
    b = {
      ...common,
      kind: 'layout',
      origin: new Map(targets.map((o) => [o.id, { x: o.x, y: o.y }])),
    }
  }
  burst = b
  b.unregister = registerGesture(finishNudge)
  b.cleanup = watchExits()
  return true
}

/**
 * 给图内这一段配上移动规则。几何权威（且那一版已挂上画面）不在时先等着：
 * 回 null；选中的元素都不能动：回 'unmovable'。
 */
function startMover(b: FigureBurst): InFigureMove | null | 'unmovable' {
  const panel = findPanel(b.panelId)
  if (!panel) return 'unmovable'
  const manifest = displayedExactManifest(panel)
  if (!manifest) {
    watchAuthority(b)
    return null
  }
  const mover = moverFor(panel, manifest, b.gids)
  if (!mover) return 'unmovable'
  b.mover = mover
  return mover
}

/**
 * 与鼠标拖动同一套分派：两个以上可对齐的成员 = 整组平移（`groupMove`），否则按主选
 * （选区末位）那一个走 `inFigureMoveOf`。锁定的（命中层本来就点不中它们，元素树里
 * 仍选得到）与隐藏的不动。
 */
function moverFor(panel: PanelObject, manifest: Manifest, gids: string[]): InFigureMove | null {
  const locked = new Set(panel.lockedGids ?? [])
  const els = gids
    .filter((g) => !locked.has(g))
    .map((g) => manifest.elements.find((el) => el.gid === g))
    .filter((el): el is NonNullable<typeof el> => !!el && el.gid !== 'figure' && !isElementHidden(el))
  if (els.length > 1) {
    const entries = alignEntries(panel, manifest, els.map((el) => el.gid))
    if (entries.length > 1) return groupMove(panel, entries)
  }
  for (const el of [...els].reverse()) {
    const mv = inFigureMoveOf(panel, manifest, el)
    if (mv) return mv
  }
  return null
}

function watchAuthority(b: FigureBurst): void {
  if (b.unwatchAuthority) return
  const retry = () => {
    if (burst !== b || b.mover) return
    const made = startMover(b)
    if (made == null) return
    stopWatching(b)
    if (made === 'unmovable') {
      status('nudgeNotMovable')
      dropBurst()
      return
    }
    apply(b)
    if (b.settleOnAuthority) finishNudge()
  }
  const offRender = useRenderStore.subscribe(retry)
  const offMounted = useMountedSvgStore.subscribe(retry)
  b.unwatchAuthority = () => {
    offRender()
    offMounted()
  }
  b.waitTimer = setTimeout(() => {
    if (burst === b && !b.mover) dropBurst()
  }, NUDGE_AUTHORITY_WAIT_MS)
}

function stopWatching(b: FigureBurst): void {
  b.unwatchAuthority?.()
  b.unwatchAuthority = null
  if (b.waitTimer) clearTimeout(b.waitTimer)
  b.waitTimer = null
}

/** 把这一段的累计位移落到画面上：画布对象改文档（事务内），图内元素只动预览平面 */
function apply(b: Burst): void {
  interaction().setNudge({ dx: b.dx, dy: b.dy })
  if (b.kind === 'layout') {
    useDocumentStore.getState().txnUpdate((d) => {
      for (const o of d.objects) {
        const start = b.origin.get(o.id)
        if (!start) continue
        o.x = start.x + b.dx
        o.y = start.y + b.dy
      }
    })
    return
  }
  if (!b.mover) return
  const [dfx, dfy] = b.toFrac(b.dx, b.dy)
  b.mover.preview(dfx, dfy, true)
}

/**
 * 现在就收掉这一段：画布对象结束事务（净位移为零时丢弃），图内元素以累计位移提交一次
 * （净位移为零时不写，`InFigureMove.commit`）。没有进行中的一段时什么都不做。
 */
export function finishNudge(): void {
  const b = burst
  if (!b) return
  if (b.kind === 'figure' && !b.mover) {
    // 还在等几何权威就被要求「现在收」（离散动作、换选区、按下指针）：没有权威写不了
    // 几何，这一段只能放弃——绝不能拿旧 manifest 去算要写进文档的值（ADR 0017）
    dropBurst()
    return
  }
  release(b)
  if (b.kind === 'layout') {
    useDocumentStore.getState().endTxn({ discard: b.dx === 0 && b.dy === 0 })
  } else {
    const [dfx, dfy] = b.toFrac(b.dx, b.dy)
    b.mover!.commit(dfx, dfy, true)
  }
}

/** 放弃这一段：画布对象回滚事务，图内元素还原预览 */
function dropBurst(): void {
  const b = burst
  if (!b) return
  release(b)
  if (b.kind === 'layout') useDocumentStore.getState().endTxn({ discard: true })
  else b.mover?.cancel(true)
}

function release(b: Burst): void {
  burst = null
  held.clear()
  if (quietTimer) clearTimeout(quietTimer)
  quietTimer = null
  if (b.kind === 'figure') stopWatching(b)
  b.unregister()
  b.cleanup()
  const ix = interaction()
  ix.setNudge(null)
  // 选中框跟随用的临时位移（`InFigureMove.preview` 写的）；指针拖动由 `end()` 清
  if (b.kind === 'figure') {
    ix.setGidDrag(null)
    ix.setElementPreview(null)
  }
}

/**
 * 这一段的其它出口：按下指针（开始点选 / 拖动之前先把这一段落定）、选区变了、
 * 窗口失焦（松键事件收不到了）。
 */
function watchExits(): () => void {
  const onPointer = () => finishNudge()
  const onBlur = () => {
    held.clear()
    finishNudge()
  }
  window.addEventListener('pointerdown', onPointer, true)
  window.addEventListener('blur', onBlur)
  const onSelection = () => {
    if (burst && currentTargetKey() !== burst.key) finishNudge()
  }
  const offSel = useSelectionStore.subscribe(onSelection)
  const offUi = useUiStore.subscribe(onSelection)
  return () => {
    window.removeEventListener('pointerdown', onPointer, true)
    window.removeEventListener('blur', onBlur)
    offSel()
    offUi()
  }
}

function findPanel(id: string): PanelObject | null {
  const o = useDocumentStore.getState().doc.objects.find((x) => x.id === id)
  return o?.type === 'panel' ? o : null
}

/** 测试隔离：丢掉进行中的一段，不提交 */
export function resetNudge(): void {
  dropBurst()
  held.clear()
}
