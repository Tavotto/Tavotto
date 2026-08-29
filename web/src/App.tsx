import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { CanvasStage } from '@/canvas/CanvasStage'
import { CanvasTabs } from '@/components/CanvasTabs'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ExportDialog } from '@/components/ExportDialog'
import { Inspector } from '@/components/inspector/Inspector'
import { LayoutDialog } from '@/components/LayoutDialog'
import { CommandPalette } from '@/components/CommandPalette'
import { FigurePickerDialog } from '@/components/FigurePickerDialog'
import { NativeConfirmDialog } from '@/components/NativeConfirmDialog'
import { NativeSessionCards } from '@/components/NativeSessionCards'
import { RegistryDialog } from '@/components/RegistryDialog'
import { RelinkDialog } from '@/components/RelinkDialog'
import { SettingsDialog } from '@/components/SettingsDialog'
import { TelemetryConsentDialog } from '@/components/TelemetryConsentDialog'
import { ShortcutHelp } from '@/components/ShortcutHelp'
import { StyleDialog } from '@/components/StyleDialog'
import { DocumentBanner } from '@/components/DocumentBanner'
import { VersionDrawer } from '@/components/VersionDialog'
import { LeftPanel } from '@/components/left/LeftPanel'
import { LeftRail } from '@/components/left/LeftRail'
import { CanvasHud, StatusToasts } from '@/components/StatusBar'
import { TopBar } from '@/components/TopBar'
import { UpdateBanner } from '@/components/UpdateBanner'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useEngineSync } from '@/hooks/useEngineSync'
import { useBuildVersion } from '@/hooks/useBuildVersion'
import { runUndoRedo, useKeyboard } from '@/hooks/useKeyboard'
import { useWorkspaceLayout } from '@/hooks/useWorkspaceLayout'
import { useServerEvents } from '@/hooks/useServerEvents'
import { subscribePruneSelection } from '@/hooks/usePruneSelection'
import { ProjectPicker } from '@/components/ProjectPicker'
import { useAiStore } from '@/store/aiStore'
import { useAssetStore } from '@/store/assetStore'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useProjectStore } from '@/store/projectStore'
import { useEnvStore } from '@/store/envStore'
import { useTelemetryStore } from '@/store/telemetryStore'
import { checkUpdateOnStartup } from '@/store/updateStore'
import { restoreSession, startAutosave, useDocumentStore } from '@/store/documentStore'
import { useViewportStore } from '@/store/viewportStore'
import { startLayoutAutoReflow } from '@/store/actions'
import { startVersionCheckpoints } from '@/hooks/useVersionCheckpoints'
import { installDiagnosticsWiring } from '@/diagnostics/wiring'
import { installDiagnosticsDevHook } from '@/diagnostics'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { onDesktopMenu, onDesktopOpen } from '@/lib/desktop'
import { DURATION, usePresence } from '@/lib/motion'
import { applyOpenRequest, readOpenRequestFromUrl, type OpenRequest } from '@/lib/openRequest'
import { msg } from '@/i18n'

export function App() {
  const phase = useProjectStore((s) => s.phase)

  useEffect(() => {
    void useProjectStore.getState().init()
    // 静默取一次版本状态（有新版本才在顶栏点圆点）。桌面与浏览器是两条
    // 互斥的升级通道，由 checkUpdateOnStartup 决定查哪一条
    checkUpdateOnStartup()
    // 渲染环境状态：缺 matplotlib 时属性栏与设置里都要能给出引导
    void useEnvStore.getState().refresh()
    // 匿名用量统计的同意态。**只是读一下**——没同意之前后端一个事件都不发，
    // 连 install_id 都不会生成；同意态还是 unset 时由 TelemetryConsentDialog
    // 问一次（问之前同样什么都没发）。
    void useTelemetryStore.getState().load()
  }, [])
  useDesktopMenu()
  useHandoff()

  // 启动探测中不闪 Picker；探测完没有项目 → Picker 接管整个界面
  if (phase === 'loading') return <div className="h-full bg-bg" />
  // 首启询问挂在 Picker 与 Workspace **共同的**根上：还没打开项目的用户
  // 一样该被问到，而不是等他开了项目才弹
  if (phase === 'none')
    return (
      <>
        <ProjectPicker />
        <TelemetryConsentDialog />
        {/* 还没打开项目也可能收到一条 `tavotto run` 交接：那个终端正阻塞着，
            确认屏不能等到用户先挑完项目才出现 */}
        <NativeConfirmDialog />
      </>
    )
  return <Workspace />
}

function Workspace() {
  const { t } = useTranslation('workspace')
  useKeyboard()
  useServerEvents()
  useEngineSync()
  useSelectionRouting()
  const outdated = useBuildVersion()

  const leftOpen = useUiStore((s) => s.leftOpen)
  const rightOpen = useUiStore((s) => s.rightOpen)
  const overlay = useWorkspaceLayout() === 'narrow'
  // 收起时先把退场播完再卸载；退场时长与 --animate-drawer-out 同源
  const left = usePresence(leftOpen, DURATION.fast)
  const right = usePresence(rightOpen, DURATION.fast)
  const scrim = usePresence(overlay && (leftOpen || rightOpen), DURATION.fast)

  useEffect(() => {
    useAssetStore.getState().load()
    // 启动那次静默：探测失败不该在用户还没进设置页时弹东西
    void useAiStore.getState().loadCaps().catch(() => {})
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
    // 诊断（ADR 0016）：只读订阅 + 开发态调试入口。**纯内存、不落盘、不上传**，
    // 只有用户点「导出诊断包」时这些事件才会进一个 zip
    const stopDiagnostics = installDiagnosticsWiring()
    installDiagnosticsDevHook()
    const onAutosaveError = (ev: Event) => {
      // stale = 另一个窗口已经存过更新的版本，后端挡下了这次覆盖（见 documentStore）
      const stale = (ev as CustomEvent<{ reason?: string }>).detail?.reason === 'stale'
      useUiStore
        .getState()
        .setStatus(
          msg(
            stale ? 'autosave.staleOtherWindow' : 'autosave.diskFailed',
            undefined,
            'workspace',
          ),
          'error',
        )
    }
    const onDocConflict = () =>
      useUiStore
        .getState()
        .setStatus(msg('autosave.docConflict', undefined, 'workspace'), 'error')
    // `tavotto run` 的会话活在后端进程里，比这个窗口长命：重开界面、SSE
    // 断线重连之后都要对一次账，否则一条还停在屏障上的会话在界面上就不存在
    // 了——而那个终端还在等人点「继续运行脚本」。
    const syncNative = () => {
      const root = useProjectStore.getState().project?.figures_dir
      void useNativeSessionStore.getState().refresh(root)
    }
    syncNative()
    window.addEventListener('mm:sse-open', syncNative)
    window.addEventListener('tavotto:autosave-error', onAutosaveError)
    window.addEventListener('tavotto:doc-conflict', onDocConflict)
    return () => {
      stopAutosave()
      stopPrune()
      stopCheckpoints()
      stopReflow()
      stopDiagnostics()
      window.removeEventListener('mm:sse-open', syncNative)
      window.removeEventListener('tavotto:autosave-error', onAutosaveError)
      window.removeEventListener('tavotto:doc-conflict', onDocConflict)
    }
  }, [])

  return (
    <TooltipProvider>
      <div className="flex h-full flex-col overflow-hidden bg-bg text-ink">
        <TopBar />
        <CanvasTabs />
        {outdated && <UpdateBanner />}
        <DocumentBanner />
        <div className="relative flex min-h-0 flex-1">
          <LeftRail />
          {/* 窄屏时抽屉盖在画布上（绝对定位在轨道右侧），画布宽度不被侵占 */}
          {left.mounted && <LeftPanel overlay={overlay} state={left.state} />}
          <div className="relative flex min-w-0 flex-1 flex-col">
            <CanvasStage />
            <CanvasHud />
            <NativeSessionCards />
            <StatusToasts />
          </div>
          {right.mounted && <Inspector overlay={overlay} state={right.state} />}
          {scrim.mounted && (
            <button
              aria-label={t('scrim.collapse')}
              data-state={scrim.state}
              onClick={() => {
                const ui = useUiStore.getState()
                if (ui.leftOpen) ui.toggleLeft()
                if (ui.rightOpen) ui.toggleRight()
              }}
              className="absolute inset-0 z-20 cursor-default bg-ink/10 data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out"
            />
          )}
          <VersionDrawer />
        </div>
        <ExportDialog />
        <SettingsDialog />
        <LayoutDialog />
        <StyleDialog />
        <RegistryDialog />
        <FigurePickerDialog />
        <NativeConfirmDialog />
      <RelinkDialog />
        <TelemetryConsentDialog />
        <CommandPalette />
        <ShortcutHelp />
        <ConfirmDialog />
      </div>
    </TooltipProvider>
  )
}

/**
 * 外部交接（`tavotto open` / Codex 插件）→ 打开项目 + 定位面板。
 *
 * 两条入口、一个执行体（lib/openRequest.ts）：
 *   * 地址栏 `?open=<stem>` —— 浏览器模式与桌面**首启**都走它。只认一次，
 *     且必须等 `phase === 'open'`：素材是从项目里扫出来的，项目还没就位时
 *     去找面板必然「找不到」，用户得到的就是一条假错误。
 *   * Tauri 事件 `tavotto:open` —— 桌面**再次**交接（单实例转发 argv）。
 *     它自带项目路径，所以在 Project Picker 上也能直接落地。
 *
 * `tavotto run` 的交接 ID（`?native=` / 事件里的 `native`）也走这两条——
 * 它与「打开哪张图」不互斥，落地后交给 nativeSessionStore 的确认队列。
 */
function useHandoff() {
  const phase = useProjectStore((s) => s.phase)
  const fromUrl = useRef<OpenRequest | null | undefined>(undefined)
  if (fromUrl.current === undefined) fromUrl.current = readOpenRequestFromUrl()

  useEffect(() => {
    if (phase !== 'open' || !fromUrl.current) return
    const req = fromUrl.current
    fromUrl.current = null // 只认一次：换项目后不该再被重放
    void applyOpenRequest(req)
  }, [phase])

  useEffect(() => {
    let unlisten: (() => void) | undefined
    let disposed = false
    void onDesktopOpen((p) => {
      void applyOpenRequest({
        project: p.project,
        stem: p.stem,
        pick: p.pick,
        native: p.native,
      })
    }).then((u) => {
      if (disposed) u()
      else unlisten = u
    })
    return () => {
      disposed = true
      unlisten?.()
    }
  }, [])
}

/**
 * 系统菜单（Tauri 壳）→ 现有 store action 的转发。浏览器模式下是空订阅。
 * 撤销/重做按焦点分派：文本框里交还原生文本撤销，画布上走文档 undo 栈——
 * 菜单加速键（⌘Z 等）在桌面里会先于 keydown 被吃掉，这里是唯一入口。
 */
function useDesktopMenu() {
  useEffect(() => {
    let unlisten: (() => void) | undefined
    let disposed = false
    void onDesktopMenu((action) => {
      const ui = useUiStore.getState()
      const el = document.activeElement
      const inEditable =
        el instanceof HTMLElement &&
        (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))
      switch (action) {
        case 'menu-open-project':
          useProjectStore.setState({ phase: 'none' })
          break
        case 'menu-export':
          if (useProjectStore.getState().phase === 'open') ui.setExportOpen(true)
          break
        case 'menu-undo':
          // 菜单加速键在拖动进行中也会触发——必须走带 undoRedoBlocked 守卫的
          // 入口，否则会把进行中的事务当场结算掉，后续位移绕过历史（数据损坏）
          if (inEditable) document.execCommand('undo')
          else runUndoRedo(false)
          break
        case 'menu-redo':
          if (inEditable) document.execCommand('redo')
          else runUndoRedo(true)
          break
      }
    }).then((u) => {
      if (disposed) u()
      else unlisten = u
    })
    return () => {
      disposed = true
      unlisten?.()
    }
  }, [])
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
