import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { perfCount } from '@/perf/core'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { Images } from '@/components/ui/icons'
import { EmptyState } from '@/components/ui/EmptyState'
import { runTutorialEntry } from '@/lib/onboarding/tutorial'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { addPanelToCanvas, openFastEdit, useWorkspaceStore } from '@/store/workspace'
import { clientToMm, mmToPx, mmToViewX, mmToViewY, mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyStateAnchor } from '@/lib/emptyStateAnchor'
import { shouldFitOnDoubleClick } from '@/lib/fitGuard'
import { applyStageFit, fitStage, stageFitFrame } from '@/store/zoomToSelection'
import { normalizeWheel } from '@/lib/wheel'
import { ObjectView } from './ObjectView'
import { WorkspaceContextBar } from './WorkspaceContextBar'
import { OverlaySvg } from './OverlaySvg'
import { MeasureChip } from './MeasureChip'
import { PageSheet } from './PageSheet'
import { ContextBar } from './context-bar/ContextBar'
import { QuickEdit } from './QuickEdit'
import { SyncOverridesHost } from '@/components/inspector/SyncOverridesDialog'
import { PageOutsideMask } from './PageOutsideMask'
import { Rulers, RULER_SIZE } from './Rulers'
import { CanvasContextMenu } from './CanvasContextMenu'
import { placePanelInPage } from '@/lib/panelPlacement'
import { startDraw, startMarquee, startPan } from './interactions'

export function CanvasStage() {
  perfCount('render.CanvasStage')
  const outerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<HTMLDivElement>(null)
  const [outer, setOuter] = useState({ w: 0, h: 0 })

  // 快速编辑：这一屏只有那一张图。页面、网格、参考线、别的对象全部让开——
  // 它们是排版的语言，而这条工作流里用户还没有画布这个概念。
  // **只改这一屏怎么画，不改文档**：面板的 x/y/w/h 一个字节没动，
  // 切回排版模式看到的还是原来那张版。
  const { t } = useTranslation('workspace')
  const fastEdit = useWorkspaceStore((s) => s.mode === 'fast_edit')
  const activePanelId = useWorkspaceStore((s) => s.activePanelId)
  // 「这一次把图加进了文档」——只用来播报，可见那一行在 WorkspaceContextBar
  const addedForEdit = useWorkspaceStore(
    (s) => s.addedForEdit !== null && s.addedForEdit === s.activePanelId,
  )
  const showRulers = useUiStore((s) => s.showRulers) && !fastEdit
  const showGrid = useUiStore((s) => s.showGrid) && !fastEdit
  const gridSize = useUiStore((s) => s.gridSize)
  const showSafeArea = useUiStore((s) => s.showSafeArea)
  const tool = useUiStore((s) => s.tool)
  const cropTargetId = useUiStore((s) => s.cropTargetId)
  const spaceDown = useViewportStore((s) => s.spaceDown)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const tweening = useViewportStore((s) => s.tweening)
  const setViewRect = useViewportStore((s) => s.setViewRect)
  const page = useDocumentStore((s) => s.doc.page)
  const objects = useDocumentStore((s) => s.doc.objects)
  const dragging = useInteractionStore((s) => s.kind !== 'none')
  /** 空白画布上的右键菜单落点（client 坐标）；null = 没开 */
  const [canvasMenu, setCanvasMenu] = useState<{ x: number; y: number } | null>(null)
  // 快速编辑这一屏只有那一张图，而画布菜单全是版面级动作（粘贴进版面、全选版面、适应页面、标尺 / 网格）——
  // 与 useKeyboard 的 inFastEdit 同一条判据：快速编辑里不开。一处管两种来路：在快速编辑里右键（开了当场收掉、
  // 不会提交出来），与开着菜单切进快速编辑。是收起不是藏起：藏起的话回排版时它会在旧落点上重新冒出来
  if (fastEdit && canvasMenu) setCanvasMenu(null)
  /** 素材拖进来时的落点预览（mm，与松手后 `addPanelToCanvas` 落的是同一个框） */
  const ghost = useDropGhost(fastEdit)

  // 「适应」看的是哪一块。快速编辑下是那张图的包围盒（图比页面大是常态，
  // 按页面 fit 会把它切掉一半）；排版下是页面。
  // 取景框只有 `stageFitFrame` 一份（⌘1 / 缩放菜单 / 命令面板的 `fitStage` 读同一份）
  const frame = stageFitFrame(objects, page, fastEdit ? activePanelId : null)

  const pad = showRulers ? RULER_SIZE : 0

  // 视口尺寸 / 位置上报：面板折叠、窗口缩放都会触发
  useLayoutEffect(() => {
    const el = outerRef.current
    const view = viewRef.current
    if (!el || !view) return
    const sync = () => {
      const r = el.getBoundingClientRect()
      setOuter({ w: r.width, h: r.height })
      const v = view.getBoundingClientRect()
      setViewRect({ left: v.left, top: v.top, width: v.width, height: v.height })
    }
    sync()
    const ro = new ResizeObserver(sync)
    ro.observe(el)
    window.addEventListener('resize', sync)
    window.addEventListener('scroll', sync, true)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', sync)
      window.removeEventListener('scroll', sync, true)
    }
  }, [setViewRect, pad])

  // 首次拿到尺寸后自动适应（与 ⌘1 同一个框：快速编辑里那张图本身的矩形，排版里页面）。
  // frame 每次渲染都是新对象：依赖它的各个分量，不是它本身
  const { x: fx, y: fy, w: fw, h: fh, page: fpage } = frame
  const fittedRef = useRef(false)
  useEffect(() => {
    if (fittedRef.current) return
    const { viewW, viewH } = useViewportStore.getState()
    if (viewW && viewH) {
      applyStageFit({ x: fx, y: fy, w: fw, h: fh, page: fpage }, false)
      fittedRef.current = true
    }
  }, [outer.w, outer.h, fx, fy, fw, fh, fpage])

  // React 的 onWheel 是被动监听，缩放必须手动挂非被动监听器
  useEffect(() => {
    const el = viewRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const vp = useViewportStore.getState()
      const ax = e.clientX - vp.originX
      const ay = e.clientY - vp.originY
      // 先按 deltaMode 折算成像素：Firefox 默认发「行」，不归一化就慢几十倍
      // （像素模式恒等返回，Chrome/触控板捏合的手感逐位不变）
      const { deltaX, deltaY } = normalizeWheel(e, { w: vp.viewW, h: vp.viewH })
      if (e.ctrlKey || e.metaKey) {
        // 0.0022 让鼠标滚轮一格约 1.3×，触控板捏合（deltaY 很小）也保持连续
        vp.zoomAt(Math.exp(-deltaY * 0.0022), ax, ay)
      } else {
        vp.setPan(vp.panX - deltaX, vp.panY - deltaY)
      }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button === 1 || (e.button === 0 && spaceDown)) {
      e.preventDefault()
      startPan(e)
      return
    }
    if (e.button !== 0) return
    // 点空白处：退出文字编辑 / 裁剪模式
    if (useUiStore.getState().editingTextId) useUiStore.getState().setEditingText(null)
    if (cropTargetId) {
      useUiStore.getState().setCropTarget(null)
      return
    }
    if (tool !== 'select') {
      startDraw(e, tool)
      return
    }
    startMarquee(e)
  }

  // 光标 mm 坐标：按帧节流，避免状态栏拖慢拖动
  const rafRef = useRef(0)
  const onPointerMove = (e: React.PointerEvent) => {
    const { clientX, clientY } = e
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0
      useInteractionStore.getState().setCursor(clientToMm(clientX, clientY))
    })
  }

  // 双击画布外侧灰色工作区 → 适应当前画布（对象/页面内容/各种编辑态不误触）
  const onDoubleClick = (e: React.MouseEvent) => {
    const ui = useUiStore.getState()
    const ok = shouldFitOnDoubleClick({
      tool,
      spaceDown,
      editingText: !!ui.editingTextId,
      cropping: !!ui.cropTargetId,
      interacting: useInteractionStore.getState().kind !== 'none',
      onObject: !!(e.target as HTMLElement).closest('[data-object-id]'),
      point: clientToMm(e.clientX, e.clientY),
      frame: { x: frame.x, y: frame.y, w: frame.w, h: frame.h },
    })
    if (ok) fitStage()
  }

  const cursor = spaceDown
    ? dragging
      ? 'grabbing'
      : 'grab'
    : tool !== 'select'
      ? 'crosshair'
      : 'default'

  return (
    <div ref={outerRef} className="relative min-h-0 min-w-0 flex-1 overflow-hidden bg-canvas">
      {showRulers && outer.w > 0 && (
        <Rulers viewW={Math.max(outer.w - pad, 0)} viewH={Math.max(outer.h - pad, 0)} />
      )}
      <div
        ref={viewRef}
        // 端到端测试的落点：画布是纯图形区域，没有可靠的语义角色可以选中，
        // 与其套一个会误导读屏器的 role，不如留一个明确的测试钩子
        data-canvas-stage=""
        className="absolute overflow-hidden bg-canvas"
        style={{ left: pad, top: pad, right: 0, bottom: 0, cursor }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onDoubleClick={onDoubleClick}
        onPointerLeave={() => useInteractionStore.getState().setCursor(null)}
        onContextMenu={(e) => {
          // 对象自己的右键菜单在 ObjectView / PanelView 里，选中框的手柄 / 命中带在 OverlaySvg 里（它们都 stopPropagation）；
          // 冒到这里的是空白处。
          // 文字编辑 / 裁剪中落在对象上的右键也会冒上来——那时留给浏览器自己的菜单（复制 / 粘贴文字）
          if ((e.target as HTMLElement).closest('[data-object-id]')) return
          // 快速编辑里原生菜单照样拦下，画布菜单由上面 `fastEdit && canvasMenu` 那一句收掉
          e.preventDefault()
          setCanvasMenu({ x: e.clientX, y: e.clientY })
        }}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes('application/x-panel-id')) {
            e.preventDefault()
            e.dataTransfer.dropEffect = 'copy'
            ghost.over(e.clientX, e.clientY)
          }
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) ghost.clear()
        }}
        onDrop={(e) => {
          ghost.clear()
          const pid = e.dataTransfer.getData('application/x-panel-id')
          if (!pid) return
          e.preventDefault()
          if (fastEdit) {
            // 这条工作流里"拖进来"的意思是"改看这一张"，不是"再摆一张上去"
            openFastEdit(pid)
            return
          }
          const info = useAssetStore.getState().byId[pid]
          if (!info) return
          const p = clientToMm(e.clientX, e.clientY)
          addPanelToCanvas(info, p.x, p.y)
        }}
      >
        {/* 唯一的世界变换。`data-world-transform` 是稳定选择器：e2e 靠它量
            「切模式时画布有没有意外移动」（审计 T01），别改名。`data-view-tweening`
            在视口补间途中出现：e2e 等它消失再量几何，不用固定延时 */}
        <div
          data-world-transform
          data-view-tweening={tweening || undefined}
          className="absolute left-0 top-0 origin-top-left"
          style={{
            transform: `translate(${panX}px, ${panY}px) scale(${zoom})`,
            width: mmToWorld(page.w),
            height: mmToWorld(page.h),
            pointerEvents: spaceDown || tool !== 'select' ? 'none' : 'auto',
          }}
        >
          {!fastEdit && (
            <PageSheet
              w={page.w}
              h={page.h}
              zoom={zoom}
              showGrid={showGrid}
              gridSize={gridSize}
              bg={page.bg}
              transparent={page.transparent}
              margin={page.margin}
              showSafeArea={showSafeArea}
            />
          )}
          <CanvasLayers only={fastEdit ? activePanelId : null} />
        </div>

        {/* 页面外画淡、页面轮廓压在内容之上：在世界层之后、选框覆盖层之前 */}
        {!fastEdit && <PageOutsideMask />}

        <OverlaySvg />
        {ghost.box && <DropGhost box={ghost.box} />}
        {/* 贴着选区的尺寸芯片：与选框同一个视口坐标系（2026-10-07 设计审计 §10.1） */}
        <MeasureChip />

        {!fastEdit && objects.length === 0 && <EmptyHint />}
      </div>

      {/* 「这张图是刚为编辑加进文档的」这条提示的**读屏播报**（UI 审计 T06）。
          读屏播报的是活动区**内容的变化**，所以这块区必须比内容先在 DOM 里。
          它挂在 `CanvasStage` 而不是上下文条里：后者是进快速编辑那一刻才挂上的，
          活动区跟它一起插进来的话，插进来时就已经填好了字——那种「带着内容整个
          插入」的活动区各家 AT 行为不一致、很可能一声不吭，等于用一个 role 承诺
          了一件它并没有做的事。`CanvasStage` 两种模式下都常驻，区先在、内容后变，
          这条提示才真的会被读出来。可见的那一份是通知轨里带「撤销」的那条（`NotificationRail`，二审 D1）。 */}
      <div role="status" data-fast-edit-live className="sr-only">
        {addedForEdit ? t('fastEdit.addedForEditLive') : ''}
      </div>

      {/* 「我在改哪一层」只说一遍：快速编辑与图内编辑共用这一条（审计 T01） */}
      <WorkspaceContextBar />

      {/* 右键快捷编辑：自己 portal 到 body，不受世界变换影响 */}
      <QuickEdit />
      {/* 空白画布的右键菜单（2026-10-07 设计审计 §10.1） */}
      {canvasMenu && <CanvasContextMenu at={canvasMenu} close={() => setCanvasMenu(null)} />}
      {/* 「把这些修改用到同脚本的其他图…」的窗口：入口在上面那个右键菜单里 */}
      <SyncOverridesHost />

      {/* 单选时贴着选择框的上下文工具条（Quick Edit 的可发现入口） */}
      <ContextBar />
    </div>
  )
}

/**
 * 素材拖进画布时的落点预览（2026-10-07 设计审计 §10.1：此前拖进来什么都不显示，松手才知道落在哪、多大）。
 *
 * 框与松手后 `addPanelToCanvas → addPanel → placePanelInPage` 落的是**同一个计算**（按原图尺寸软上限缩放、
 * 以指针为中心、夹在页面里）。拖动中浏览器不让读 dataTransfer 的内容，所以在 dragstart 冒泡到 document 时
 * 记下被拖的素材 id（那一刻是可读的）；dragend / drop 清掉。快速编辑里拖进来是「改看这一张」，不预览。
 */
let draggedAssetId: string | null = null
function useDropGhost(fastEdit: boolean) {
  const [box, setBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  useEffect(() => {
    const start = (e: DragEvent) => {
      draggedAssetId = e.dataTransfer?.getData('application/x-panel-id') || null
    }
    const end = () => {
      draggedAssetId = null
      setBox(null)
    }
    document.addEventListener('dragstart', start)
    document.addEventListener('dragend', end)
    document.addEventListener('drop', end)
    return () => {
      document.removeEventListener('dragstart', start)
      document.removeEventListener('dragend', end)
      document.removeEventListener('drop', end)
    }
  }, [])
  return {
    box: fastEdit ? null : box,
    over: (clientX: number, clientY: number) => {
      if (fastEdit || !draggedAssetId) return
      const info = useAssetStore.getState().byId[draggedAssetId]
      if (!info) return
      const at = clientToMm(clientX, clientY)
      const next = placePanelInPage(info.native_w_mm, info.native_h_mm, useDocumentStore.getState().doc.page, at)
      setBox((b) => (b && b.x === next.x && b.y === next.y && b.w === next.w && b.h === next.h ? b : next))
    },
    clear: () => setBox(null),
  }
}

/** 落点预览框：暂定的东西用唯一那种虚线（`--sel-dash` 的 CSS 等价：dashed 边） */
function DropGhost({ box }: { box: { x: number; y: number; w: number; h: number } }) {
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const t = { zoom, panX, panY, originX: 0, originY: 0 }
  return (
    <div
      data-drop-ghost
      aria-hidden
      className="pointer-events-none absolute rounded-xs border border-dashed border-sel bg-sel/5"
      style={{ left: mmToViewX(box.x, t), top: mmToViewY(box.y, t), width: mmToPx(box.w, t), height: mmToPx(box.h, t) }}
    />
  )
}

/**
 * 每个打开的画布标签一个常驻图层：激活画布渲染活跃 doc，其余渲染快照并
 * display:none。切换标签只是 CSS 显隐——docToCanvas/canvasToDoc 共享同一个
 * objects 数组引用，配合 ObjectView 的 memo，标签来回切换时 DOM 与已解码的
 * 图片全部原地保留，不再整树重建、闪白、卡顿。
 * 隐藏图层 display:none：无命中、无绘制成本；快照对象的 id 不在活跃 doc 里，
 * 任何按 id 回写（文字自适应高度这类）都会安静落空，不会误写。
 */
function CanvasLayers({ only }: { only?: string | null }) {
  const objects = useDocumentStore((s) => s.doc.objects)
  const openTabs = useDocumentStore((s) => s.openTabs)
  const activeId = useDocumentStore((s) => s.activeCanvasId)
  const canvases = useDocumentStore((s) => s.canvases)

  return (
    <>
      {openTabs.map((id) => {
        const active = id === activeId
        // 快速编辑只画那一个对象：其余标签整层不挂（它们是别的画布的排版）
        if (only && !active) return null
        const all = active ? objects : canvases.find((c) => c.id === id)?.objects
        const objs = only ? all?.filter((o) => o.id === only) : all
        if (!objs) return null
        return (
          <div
            key={id}
            aria-hidden={active ? undefined : true}
            className="absolute left-0 top-0"
            style={active ? undefined : { display: 'none' }}
          >
            {objs.map((o) => (
              <ObjectView key={o.id} obj={o} />
            ))}
          </div>
        )
      })}
    </>
  )
}

/** 画布层的文案在 workspace:stage.* 下 */
const sg = (key: string, values?: Record<string, unknown>) =>
  translate(`stage.${key}`, { ns: 'workspace', ...(values ?? {}) })

/**
 * 空画布的起步提示：**一个**主要行动「添加图」（打开素材库），旁边一条
 * 「试用示例」（走教程的统一入口）。项目里一张图都没有时，说明改成告诉用户
 * 把什么文件放进项目目录——那是唯一能让「添加图」有东西可添的路（审计 T03）。
 * 教程项目自己不再提供「试用示例」。
 */
function EmptyHint() {
  useTranslation('workspace')
  const setLeftTab = useUiStore((s) => s.setLeftTab)
  const selectionEmpty = useSelectionStore((s) => s.ids.length === 0)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const viewW = useViewportStore((s) => s.viewW)
  const viewH = useViewportStore((s) => s.viewH)
  const page = useDocumentStore((s) => s.doc.page)
  const assetsLoaded = useAssetStore((s) => s.loaded)
  const hasAssets = useAssetStore((s) => s.panels.length > 0)
  const inTutorial = useProjectStore((s) => s.project?.tutorial === true)
  if (!selectionEmpty) return null
  // 锚在纸面**可见部分**的中心：侧栏一开、画布被挤到一边时提示跟着纸面走，
  // 而不是飘在灰色工作区中央；纸面比视野大时（放大 / 首次适配还没算对）落在
  // 看得到的那一块上，永远不出屏（审计 B01）。落点算法在 `lib/emptyStateAnchor`
  const { x: cx, y: cy } = emptyStateAnchor({
    viewW,
    viewH,
    paper: { x: panX, y: panY, w: mmToWorld(page.w) * zoom, h: mmToWorld(page.h) * zoom },
  })
  return (
    // `w-max`：绝对定位盒子的 shrink-to-fit 只看 left 右侧剩下的空间，纸面中心靠近
    // 视口右缘时提示会被折成十几个字一行的一根细柱（2026-09-11 走查截图）；
    // 按内容定宽，宽度上限由 EmptyState 自己的 max-w 决定
    <div
      data-canvas-empty-hint
      className="pointer-events-none absolute w-max -translate-x-1/2 -translate-y-1/2"
      style={{ left: cx, top: cy }}
    >
      <div className="pointer-events-auto">
        <EmptyState
          icon={Images}
          title={sg('emptyTitle')}
          // 素材清单还没回来时先按「有」说：那句「放文件进目录」是对空项目说的
          hint={sg(assetsLoaded && !hasAssets ? 'emptyHintNoAssets' : 'emptyHint')}
          action={{ label: sg('addFigure'), onClick: () => setLeftTab('assets') }}
          secondary={
            inTutorial
              ? undefined
              : { label: sg('tryTutorial'), onClick: () => void runTutorialEntry('canvas') }
          }
        />
      </div>
    </div>
  )
}
