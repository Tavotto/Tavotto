import { create } from 'zustand'
import { newId } from '@/lib/id'
import {
  armNoProjectRecovery,
  fetchOpenProjects,
  fetchProject,
  fetchRecentProjects,
  openProjectApi,
  removeRecentProject,
  setNoProjectHandler,
  type ProjectStatus,
  type RecentProject,
} from '@/lib/api'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import { flushAutosave, useDocumentStore } from '@/store/documentStore'
import { useAssetStore } from '@/store/assetStore'
import { clearVariantPngCache } from '@/hooks/useVariantPng'
import { useRenderStore } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
import { resetExportState } from '@/store/exportStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { resetPreview } from '@/store/svgPreviewStore'
import { clearDiagnosticTrace } from '@/diagnostics'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'

/**
 * 当前项目状态。'loading' 只出现在启动探测阶段；'none' = 后端没有打开的
 * 项目（或后端探测失败），工作台让位给 Project Picker。
 *
 * 项目绑在**标签页**上（lib/session.ts 的 sessionStorage），不是绑在后端的
 * 全局状态上：换一个标签页可以开另一个图库，互不影响。
 */
interface ProjectState {
  phase: 'loading' | 'open' | 'none'
  project: ProjectStatus | null
  recent: RecentProject[]
  /** 后端进程里打开着的全部项目（快速切换菜单用） */
  opened: ProjectStatus[]
  /** 启动时探测一次；SSE 断线重连后也可复查 */
  init: () => Promise<void>
  refreshRecent: () => Promise<void>
  /** 打开/切换项目：后端切换成功后冲刷并重置前端会话状态 */
  open: (path: string, create?: boolean) => Promise<ProjectStatus>
  remove: (path: string) => Promise<void>
  /** 后端不认本标签页的项目了（409 no_project）：退回 Project Picker */
  dropProject: () => void
}

/** 换项目时把属于旧项目的前端会话状态全部丢掉。 */
async function resetForNewProject() {
  // 1. 冲刷当前文档的自动保存（切走的文档可从「最近文档」取回）
  flushAutosave()
  // 2. 清选择 / 图内编辑态 / 渲染缓存
  useSelectionStore.getState().set([])
  const ui = useUiStore.getState()
  ui.setElementPanel(null)
  ui.setEditingText(null)
  ui.setCropTarget(null)
  useRenderStore.getState().clear()
  useRuntimeAssetStore.getState().clear()
  // 版本缩略图按 (项目, 素材版本, 变体) 缓存 blob：换项目时整表释放，
  // 既是回收 blob，也是防止旧项目的图被当成新项目某个版本的预览
  clearVariantPngCache()
  // 脚本运行状态机换代（在途 probe 响应作废，绝不落进新项目）+ 脚本清单清空
  useScriptRunStore.getState().clear()
  useScriptLibraryStore.getState().clear()
  // 多 Figure 选择器（交接的 pick）属于旧项目，跟着关掉
  useFigurePickerStore.getState().close()
  // native 会话换代：卡片与在途响应都属于旧项目。**用户的脚本一个都不动**
  // ——那些进程是他自己在终端里起的，切个项目不该杀掉它们（ADR 0021 §14）。
  // 切回去时 refresh() 会把它们重新对上账。
  useNativeSessionStore.getState().clear()
  // 预览平面挂在「面板 + 那一版 SVG」上，旧项目的面板整批消失后那些账本
  // 指向的都是野节点，跟着一起清（DOM 由 React 自己收）
  resetPreview()
  // 诊断轨迹同样属于旧项目：不清的话，在新项目里导出的诊断包会带着上一个
  // 项目的匿名操作序列，让这份 trace 同时描述两份互不相干的文档——既误导
  // 排障，也把「用户以为只导出了当前这份工作」这句话变成假的。
  // seq 刻意**不重置**（见 diagnostics/store.ts）：编号缺口是「这里被清过」
  // 的唯一线索。
  clearDiagnosticTrace()
  // 接入就绪度整份丢掉：报告、错误、聚焦目标、横幅关闭记录都属于旧项目。
  // 关闭记录本身按项目 id 存在本机，切回去时仍然作数——清的只是内存里
  // 「当前项目关过哪一版」这个投影。
  useProjectReadinessStore.getState().clear()
  // 导出作业的**前端状态**跟着丢：结果里的 `/exports/<name>` 是裸路径，
  // 渲染时由 `apiUrl()` 补上**当前**项目的 pj——不清的话，切完项目再打开
  // 导出面板会看到旧项目的结果，而那些链接指向的是新项目的导出目录（不是
  // 404 就是下到同名的另一张图）。轮询也会一直问一个属于旧项目的作业。
  //
  // **只清前端状态，不取消后端那个作业**：用户切个项目不是在说"我不要那次
  // 导出了"，文件该照常写完（与 native 会话同一条纪律，ADR 0021 §14）。
  resetExportState()
  // 3. 换成空白文档（旧文档属于旧项目；素材引用跨项目不可靠）
  await useDocumentStore.getState().switchDocument(
    { schema: 2, name: 'fig_layout', page: { w: 150, h: 100 }, objects: [], guides: [] },
    newId('d'),
  )
  // 工作区模式指着旧文档里的一个对象 id，跟着换代（本机那一档按 documentId
  // 存，切回去仍然作数——清的是内存里"现在停在哪张图上"）。
  //
  // **必须排在 `switchDocument` 之后。** 排在前面的话，
  // `startWorkspacePersistence` 的那个订阅此刻认的还是**旧**文档 id：它会把
  // `{mode:'layout'}` 写进 `tavotto.workspace.<旧 id>`，把用户在那份文档里停
  // 的那张图抹掉——上面这句"切回去仍然作数"就成了一句假话。派生状态不许覆盖
  // 用户偏好，切项目这件事更不是用户在表达"我不要快速编辑了"。
  useWorkspaceStore.getState().clear()
  // 4. 重载新项目素材 + 它的接入就绪度（两份是同一次后端计算的两个投影）
  await useAssetStore.getState().load()
  void useProjectReadinessStore.getState().load()
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  phase: 'loading',
  project: null,
  recent: [],
  opened: [],

  init: async () => {
    try {
      let project: ProjectStatus
      try {
        project = await fetchProject()
      } catch {
        // 本标签页记着的项目在后端已不存在（进程重启/项目已关闭）：
        // 忘掉它退回默认项目，绝不继续拿一个失效 id 去请求
        if (!currentProjectId()) throw new Error('unreachable')
        setCurrentProjectId(null)
        project = await fetchProject()
      }
      if (project.open && project.id) {
        setCurrentProjectId(project.id)
        armNoProjectRecovery()
      }
      const [recent, opened] = await Promise.all([
        fetchRecentProjects(),
        fetchOpenProjects().catch(() => []),
      ])
      set({ project, recent, opened, phase: project.open ? 'open' : 'none' })
    } catch {
      // 后端不可达时也进 Picker——它会在重试里继续探测
      set({ phase: 'none' })
    }
  },

  refreshRecent: async () => {
    try {
      const [recent, opened] = await Promise.all([
        fetchRecentProjects(),
        fetchOpenProjects().catch(() => []),
      ])
      set({ recent, opened })
    } catch {
      /* 列表刷新失败不致命 */
    }
  },

  open: async (path, create = false) => {
    const status = await openProjectApi(path, create)
    // 先认领项目，再做任何会发请求的事：素材/渲染都必须落到新项目上
    if (status.id) setCurrentProjectId(status.id)
    // 手里又有项目了：这一个再失效时仍要能把用户送回选择器
    armNoProjectRecovery()
    await resetForNewProject()
    set({ project: status, phase: 'open' })
    void get().refreshRecent()
    return status
  },

  remove: async (path) => {
    await removeRecentProject(path)
    await get().refreshRecent()
  },

  /**
   * 后端不认本标签页记着的 pj 了（进程重启 / 项目被别处关掉）：忘掉这个 id，
   * 退回 Project Picker 让用户自己选。**不自动挑一个别的项目落进去**——那会
   * 让标签页对着另一个图库继续编辑，与 init() 的容错、后端 _request_ctx 对
   * 失效 pj 的态度同源。
   */
  dropProject: () => {
    // 幂等：api.ts 已经节流过一次，这里再兜一层（已经在选择器上就什么都不做）
    if (get().phase === 'none' && !get().project && !currentProjectId()) return
    // 编辑中的文档先落本机兜底副本。此刻磁盘那一份必然写不进去（同样 409），
    // 但 flushAutosave 绝不会因为写盘失败去清本机副本，改动不会丢。
    flushAutosave()
    // 先冲刷再忘掉 pj：反过来的话这份自动保存会落到后端的默认项目里去。
    setCurrentProjectId(null)
    set({ project: null, phase: 'none' })
    // 选择器要用「最近 / 已打开」两份列表；这两个端点与项目无关，不会再 409
    void get().refreshRecent()
  },
}))

// 任何一个请求撞上 409 no_project 都会走到这里（检测在 lib/api.ts 的请求出口）
setNoProjectHandler(() => useProjectStore.getState().dropProject())
