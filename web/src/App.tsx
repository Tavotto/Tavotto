import { useEffect } from 'react'
import { CanvasStage } from '@/canvas/CanvasStage'
import { CanvasTabs } from '@/components/CanvasTabs'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ExportDialog } from '@/components/ExportDialog'
import { Inspector } from '@/components/inspector/Inspector'
import { LayoutDialog } from '@/components/LayoutDialog'
import { CommandPalette } from '@/components/CommandPalette'
import { RelinkDialog } from '@/components/RelinkDialog'
import { SettingsDialog } from '@/components/SettingsDialog'
import { ShortcutHelp } from '@/components/ShortcutHelp'
import { StyleDialog } from '@/components/StyleDialog'
import { VersionDrawer } from '@/components/VersionDialog'
import { LeftPanel } from '@/components/left/LeftPanel'
import { LeftRail } from '@/components/left/LeftRail'
import { CanvasHud, StatusToasts } from '@/components/StatusBar'
import { TopBar } from '@/components/TopBar'
import { UpdateBanner } from '@/components/UpdateBanner'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useEngineSync } from '@/hooks/useEngineSync'
import { useBuildVersion } from '@/hooks/useBuildVersion'
import { useKeyboard } from '@/hooks/useKeyboard'
import { useWorkspaceLayout } from '@/hooks/useWorkspaceLayout'
import { useServerEvents } from '@/hooks/useServerEvents'
import { subscribePruneSelection } from '@/hooks/usePruneSelection'
import { ProjectPicker } from '@/components/ProjectPicker'
import { useAiStore } from '@/store/aiStore'
import { useAssetStore } from '@/store/assetStore'
import { useProjectStore } from '@/store/projectStore'
import { useEnvStore } from '@/store/envStore'
import { useUpdateStore } from '@/store/updateStore'
import { restoreSession, startAutosave, useDocumentStore } from '@/store/documentStore'
import { useViewportStore } from '@/store/viewportStore'
import { startLayoutAutoReflow } from '@/store/actions'
import { startVersionCheckpoints } from '@/hooks/useVersionCheckpoints'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'

export function App() {
  const phase = useProjectStore((s) => s.phase)

  useEffect(() => {
    void useProjectStore.getState().init()
    // 静默取一次版本状态（后端 24h 节流 + 可关；有新版本才在顶栏点圆点）
    void useUpdateStore.getState().check(false)
    // 渲染环境状态：缺 matplotlib 时属性栏与设置里都要能给出引导
    void useEnvStore.getState().refresh()
  }, [])

  // 启动探测中不闪 Picker；探测完没有项目 → Picker 接管整个界面
  if (phase === 'loading') return <div className="h-full bg-bg" />
  if (phase === 'none') return <ProjectPicker />
  return <Workspace />
}

function Workspace() {
  useKeyboard()
  useServerEvents()
  useEngineSync()
  useSelectionRouting()
  const outdated = useBuildVersion()

  const leftOpen = useUiStore((s) => s.leftOpen)
  const rightOpen = useUiStore((s) => s.rightOpen)
  const overlay = useWorkspaceLayout() === 'narrow'

  useEffect(() => {
    useAssetStore.getState().load()
    void useAiStore.getState().loadCaps()
    // 磁盘恢复是异步的：恢复到文档后重新适配视口
    void restoreSession().then((restored) => {
      if (!restored) return
      const page = useDocumentStore.getState().doc.page
      useViewportStore.getState().fit(page.w, page.h)
    })
    const stopAutosave = startAutosave()
    const stopPrune = subscribePruneSelection()
    const stopCheckpoints = startVersionCheckpoints()
    const stopReflow = startLayoutAutoReflow()
    const onAutosaveError = () =>
      useUiStore
        .getState()
        .setStatus('自动保存写入磁盘失败：改动暂存在浏览器里，请检查磁盘空间后重试', 'error')
    window.addEventListener('magplot:autosave-error', onAutosaveError)
    return () => {
      stopAutosave()
      stopPrune()
      stopCheckpoints()
      stopReflow()
      window.removeEventListener('magplot:autosave-error', onAutosaveError)
    }
  }, [])

  return (
    <TooltipProvider>
      <div className="flex h-full flex-col overflow-hidden bg-bg text-ink">
        <TopBar />
        <CanvasTabs />
        {outdated && <UpdateBanner />}
        <div className="relative flex min-h-0 flex-1">
          <LeftRail />
          {/* 窄屏时抽屉盖在画布上（绝对定位在轨道右侧），画布宽度不被侵占 */}
          {leftOpen && <LeftPanel overlay={overlay} />}
          <div className="relative flex min-w-0 flex-1 flex-col">
            <CanvasStage />
            <CanvasHud />
            <StatusToasts />
          </div>
          {rightOpen && <Inspector overlay={overlay} />}
          {overlay && (leftOpen || rightOpen) && (
            <button
              aria-label="收起侧栏"
              onClick={() => {
                const ui = useUiStore.getState()
                if (ui.leftOpen) ui.toggleLeft()
                if (ui.rightOpen) ui.toggleRight()
              }}
              className="absolute inset-0 z-20 cursor-default bg-ink/10"
            />
          )}
          <VersionDrawer />
        </div>
        <ExportDialog />
        <SettingsDialog />
        <LayoutDialog />
        <StyleDialog />
        <RelinkDialog />
        <CommandPalette />
        <ShortcutHelp />
        <ConfirmDialog />
      </div>
    </TooltipProvider>
  )
}

/**
 * 选择驱动的面板路由：
 * - 出现选中（画布对象或进入图内编辑）→ 打开属性栏；素材抽屉未钉住则让位。
 *   停留在改图助手时只更新目标，不切换模式。
 * - 选择清空 → 未钉住的属性栏收起，不留「没有选中对象」的占位。
 */
function useSelectionRouting() {
  const hasSelection = useSelectionStore((s) => s.ids.length > 0)
  const inElement = useUiStore((s) => s.elementPanelId != null)
  const primaryId = useSelectionStore((s) => s.ids.at(-1) ?? null)
  const primaryGid = useUiStore((s) => s.selectedGids.at(-1) ?? null)
  const active = hasSelection || inElement

  useEffect(() => {
    const ui = useUiStore.getState()
    if (active) ui.autoShowProperties()
    else ui.autoHideProperties()
  }, [active])

  // 换选对象 / 图内元素也要把属性带到眼前（互斥断点下右栏可能正被抽屉挤掉）。
  // 例外：焦点还在左抽屉里（素材批量添加、树的 Shift 多选）时不抢走抽屉——
  // 那是用户正在进行的流程，点画布即可唤出属性。
  useEffect(() => {
    if (!primaryId && !primaryGid) return
    const ui = useUiStore.getState()
    const inDrawer = !!document.activeElement?.closest('[data-left-drawer]')
    if (inDrawer && ui.layout !== 'wide') return
    ui.autoShowProperties()
  }, [primaryId, primaryGid])
}
