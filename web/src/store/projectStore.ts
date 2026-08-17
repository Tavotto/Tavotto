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
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'

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
  useRenderStore.setState({ byFile: {} })
  // 3. 换成空白文档（旧文档属于旧项目；素材引用跨项目不可靠）
  await useDocumentStore.getState().switchDocument(
    { schema: 2, name: 'fig_layout', page: { w: 150, h: 100 }, objects: [], guides: [] },
    newId('d'),
  )
  // 4. 重载新项目素材
  await useAssetStore.getState().load()
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
