import { create } from 'zustand'
import { newId } from '@/lib/id'
import {
  fetchProject,
  fetchRecentProjects,
  openProjectApi,
  removeRecentProject,
  type ProjectStatus,
  type RecentProject,
} from '@/lib/api'
import { flushAutosave, useDocumentStore } from '@/store/documentStore'
import { useAssetStore } from '@/store/assetStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'

/**
 * 当前项目状态。'loading' 只出现在启动探测阶段；'none' = 后端没有打开的
 * 项目（或后端探测失败），工作台让位给 Project Picker。
 */
interface ProjectState {
  phase: 'loading' | 'open' | 'none'
  project: ProjectStatus | null
  recent: RecentProject[]
  /** 启动时探测一次；SSE 断线重连后也可复查 */
  init: () => Promise<void>
  refreshRecent: () => Promise<void>
  /** 打开/切换项目：后端切换成功后冲刷并重置前端会话状态 */
  open: (path: string, create?: boolean) => Promise<ProjectStatus>
  remove: (path: string) => Promise<void>
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  phase: 'loading',
  project: null,
  recent: [],

  init: async () => {
    try {
      const [project, recent] = await Promise.all([fetchProject(), fetchRecentProjects()])
      set({ project, recent, phase: project.open ? 'open' : 'none' })
    } catch {
      // 后端不可达时也进 Picker——它会在重试里继续探测
      set({ phase: 'none' })
    }
  },

  refreshRecent: async () => {
    try {
      set({ recent: await fetchRecentProjects() })
    } catch {
      /* 列表刷新失败不致命 */
    }
  },

  open: async (path, create = false) => {
    const status = await openProjectApi(path, create)
    // —— 前端切换协议（后端已停旧 watcher/worker/AI）——
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
    const store = useDocumentStore.getState()
    await store.switchDocument(
      { schema: 2, name: 'fig_layout', page: { w: 150, h: 100 }, objects: [], guides: [] },
      newId('d'),
    )
    // 4. 重载新项目素材与状态
    await useAssetStore.getState().load()
    set({ project: status, phase: 'open' })
    void get().refreshRecent()
    return status
  },

  remove: async (path) => {
    await removeRecentProject(path)
    await get().refreshRecent()
  },
}))
