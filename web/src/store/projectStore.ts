import { create } from 'zustand'
import { emitActivity } from '@/lib/activity'
import { newId } from '@/lib/id'
import {
  armNoProjectRecovery,
  backendErrorMsg,
  fetchOpenProjects,
  fetchProject,
  fetchProjectLists,
  openProjectApi,
  postPinnedOp,
  removeRecentProject,
  setNoProjectHandler,
  type PinnedOp,
  type ProjectStatus,
  type RecentProject,
} from '@/lib/api'
import {
  documentHasContent,
  readProjectDocument,
  rememberProjectDocument,
  type ProjectDocumentRef,
} from '@/lib/projectDocs'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import { openRecentDocument } from '@/store/actions'
import { useAssetBrowseStore } from '@/store/assetBrowseStore'
import { flushAutosave, loadAutosavedDocument, useDocumentStore } from '@/store/documentStore'
import { useAssetStore } from '@/store/assetStore'
import { clearVariantPngCache } from '@/hooks/useVariantPng'
import { useRenderStore } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
import { resetExportState } from '@/store/exportStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useDepRepairStore } from '@/store/depRepairStore'
import { usePackageStore } from '@/store/packageStore'
import { useEnvStore } from '@/store/envStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { resetPreview } from '@/store/svgPreviewStore'
import { clearDiagnosticTrace } from '@/diagnostics'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { setCurrentProjectLabel } from '@/lib/projectLabel'
import { emptyDocument } from '@/types/document'
import { useWorkspaceStore } from '@/store/workspace'

/**
 * 当前项目状态。'loading' 只出现在启动探测阶段；'none' = 后端没有打开的
 * 项目（或后端探测失败），工作台让位给 Project Picker。
 *
 * 项目绑在**标签页**上（lib/session.ts 的 sessionStorage），不是绑在后端的
 * 全局状态上：换一个标签页可以开另一个图库，互不影响。
 */
export interface ProjectState {
  phase: 'loading' | 'open' | 'none'
  project: ProjectStatus | null
  recent: RecentProject[]
  /** 收藏的项目，按用户排的顺序（左栏「工作区」抽屉）；与最近列表互相独立 */
  pinned: RecentProject[]
  /**
   * 正在切项目（`open` / `adoptOpenedProject` 在跑）。切换是串行的，界面据此把所有
   * 「打开项目」的入口置灰——并发的两次换代会让 A 的请求落进 B（Codex #550 P1）。
   */
  switching: boolean
  /** 后端进程里打开着的全部项目（快速切换菜单用） */
  opened: ProjectStatus[]
  /** 启动时探测一次；SSE 断线重连后也可复查 */
  init: () => Promise<void>
  refreshRecent: () => Promise<void>
  /** 打开/切换项目：后端切换成功后冲刷并重置前端会话状态 */
  open: (path: string, create?: boolean) => Promise<ProjectStatus>
  /**
   * 后端**已经**打开了一个项目（`/api/projects/open` 之外的入口，比如教程的
   * `/api/tutorial/open`）：认领它并做与 `open` 完全相同的前端换代。
   * 「打开项目之后前端要做什么」只有这一份，别的入口不许再抄一遍。
   */
  adoptOpenedProject: (
    status: ProjectStatus,
    opts?: {
      /**
       * 在「前端状态已换代、但工作台还没宣布打开」的那个空档里装文档。
       * 教程用它把教程画布换进来：工作台一挂载就会 `restoreSession()`，那时
       * `tavotto.currentDoc` 记的必须已经是教程文档，否则它会把空白文档再装回去。
       */
      prepareDocument?: () => Promise<void>
    },
  ) => Promise<ProjectStatus>
  /**
   * 收藏 / 取消收藏：**轮到执行时**按最新的 `pinned` 决定 add 还是 remove——前一次
   * 还没回来时连点第二下，界面还显示「没收藏」，点击那一刻定的话两下都是 add。
   * 后端按路径执行，重复执行无害。失败时列表不动、状态栏说一句。
   */
  togglePin: (path: string) => Promise<void>
  /**
   * 挪一条收藏：`{ delta }` 相对挪（上移 -1 / 下移 +1），`{ toPath }` 挪到那一条此刻
   * 的位置（拖动）。按**路径**描述、执行时才由后端查下标——排着队的两次挪动、前面
   * 排着的删除都不会让它移错项。
   */
  movePinned: (path: string, by: { delta: number } | { toPath: string }) => Promise<void>
  /**
   * 把「发请求 + 认领」整个当成**一次**切换排进切换队列（教程的 open / reset 用它）：
   * 请求在路上时 `switching` 就亮着，后点的别的项目排在它后面，按点击顺序落地（Codex #550：
   * 只把认领排队的话，教程请求还在路上时点的最近项目会先进队，随后教程的认领把它换掉）。
   * `fn` 拿到的 `adopt` 直接认领、**不再排队**——在事务里调 `adoptOpenedProject` 会排在
   * 自己后面，永远等不到。
   */
  switchTransaction: <T>(
    fn: (adopt: ProjectState['adoptOpenedProject']) => Promise<T>,
  ) => Promise<T>
  remove: (path: string) => Promise<void>
  /** 一次从最近列表移除多条（失效项分组的「全部移除」）；同样不删磁盘内容 */
  removeMany: (paths: string[]) => Promise<void>
  /**
   * 切回项目时记着「上次开的是这份」、却没能把它换回来（自动保存槽位读不到 /
   * 后端不可达）。**不静默开一份空白了事**：顶部横幅指名那份文档，给一个
   * 「打开上次文档」重试与一个「知道了」。
   */
  lastDocumentIssue: ProjectDocumentRef | null
  /** 横幅上的重试：再读一次自动保存槽位；成功就换过去并收起横幅 */
  openLastDocument: () => Promise<boolean>
  dismissLastDocumentIssue: () => void
  /** 后端不认本标签页的项目了（409 no_project）：退回 Project Picker */
  dropProject: () => void
  /**
   * 去 Project Picker（设置「切换项目」、桌面菜单「打开项目」、教程收尾「打开自己的项目」）。
   * **切换进行中什么都不做**：换代完成时 `adoptNow` 会把 phase 写回 open，用户这一下
   * 会被悄悄吞掉；而 Picker 里的入口在切换期间本来就全灰（Codex #550）。
   */
  showPicker: () => void
  /**
   * Picker 的「返回当前项目」。切换进行中**什么都不做**：`adoptNow` 已经把全局 pj 换成
   * 新项目、`project` 还没发布的那一刻回去，看到的是旧项目的界面、请求却发往新项目。
   */
  returnToCurrent: () => void
}

/**
 * 把这个项目上次开着的文档换回来。读的是它的自动保存槽位（磁盘优先、本机
 * 副本兜底——`readAutosaveDoc` 那套既有规则），换的是**同一个 documentId**，
 * 所以槽位不会分叉。任何一步失败都回 false，由调用方决定怎么说。
 */
async function restoreProjectDocument(ref: ProjectDocumentRef): Promise<boolean> {
  try {
    return (await loadAutosavedDocument(ref.id)).loaded
  } catch {
    return false
  }
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
  // 素材库的搜索词与筛选说的是旧项目的目录与素材，跟着清
  useAssetBrowseStore.getState().clear()
  // 素材清单本身也属于旧项目：面板、「无法使用」清单与由它们派生的来源目录。不清的话，
  // 新项目的 /api/panels 挂起或失败时 B 下面显示的是 A 的卡片和 A 的文件名（#577）；
  // 换代同时作废 A 还在飞的那次请求
  useAssetStore.getState().clear()
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
  // 项目环境 / 工作目录模式是项目级的（ADR 0018 / 0045）：清掉旧项目的，按新
  // 项目重取——否则开关与错误块的建议说的是上一个项目的模式
  useEnvStore.getState().resetProject()
  // 包管理换代：清单与「在 PyPI 查找」的结果都属于旧项目那个受管环境。查找结果
  // 带着 A 环境里的 `installed` 版本与 A 的索引源，而这一页的安装按钮作用在
  // **当前**项目上；在途的那次查找回来时同样按代际作废（ADR 0038）。
  usePackageStore.getState().clear()
  // 依赖修复同一条纪律（issue #590）：计划 / 绑定 / 错误 / 钉住的解释器说的都是旧项目的环境，
  // 在途请求按代际作废；**装包作业不取消**（后端的 close_project 不碰它，结果按计划自己的项目记账），
  // 进度按所属项目分格——B 上不显示 A 的进度条，切回 A 接得上
  useDepRepairStore.getState().clear()
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
  await useDocumentStore.getState().switchDocument(emptyDocument(), newId('d'))
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

/**
 * 一条串行队列：`run(fn)` 等前面排着的都结束（成功或失败）再跑 fn。
 *
 * 切项目与改收藏各用一条：
 *  - 切项目：`adoptOpenedProject` 先改全局 pj 再 await 换代，两次交错就会让 A 的
 *    换代请求带着 B 的 pj 发出去、最后一次完成的把 `project` 写回 A（Codex #550 P1）；
 *  - 改收藏：每次都是整张替换，payload 必须在**轮到自己时**从最新列表算，否则两次
 *    快速收藏都从同一份旧列表出发，后到的那张把先到的盖掉（Codex #550 P2）。
 */
function serialQueue() {
  let tail: Promise<unknown> = Promise.resolve()
  return <T>(fn: () => Promise<T>): Promise<T> => {
    const run = tail.then(fn)
    tail = run.catch(() => {})
    return run
  }
}
const switchQueue = serialQueue()
const pinQueue = serialQueue()

/**
 * 收藏列表的修订号：每次 PUT 成功 +1。`pinned` 还有两个写入方（`init` 与
 * `refreshRecent`，读的是 GET 的快照）不在收藏队列里——它们发请求前记下修订号，
 * 回来时若已经变了，说明期间有更新的 PUT 回包，**这份快照比界面旧**，只更新最近
 * 列表、不碰收藏（Codex #550：切换后的刷新把刚收藏的项目盖回旧快照）。
 */
let pinnedRev = 0

/**
 * 两份列表（最近 / 收藏 / 已打开）的请求序号：`init` 与 `refreshRecent` 每发一次 +1，
 * 回来时只认**最新那一次**、且发请求那一刻的 pj 仍是此刻的 pj。条目里的 `current` /
 * `id` / `opened` 是按发请求时的项目算的：连切 A → B 时 A 那次刷新晚到的话，会把
 * A 标成「当前」、真正的当前 B 反而能点（Codex #550）。与 assetStore 等同一条纪律：
 * 请求序号挡旧响应、发请求那一刻的 pj 挡串项目。
 */
let listSeq = 0

export const useProjectStore = create<ProjectState>((set, get) => {
  /** 切项目的前端换代本体；对外的两个入口都经 `switchQueue` 串行地调它 */
  const adoptNow: ProjectState['adoptOpenedProject'] = async (status, opts) => {
    // 先认领项目，再做任何会发请求的事：素材/渲染都必须落到新项目上
    if (status.id) setCurrentProjectId(status.id)
    // 「最近文档」要在条目上标出所属项目（审计 T04）；名字的权威在这里，
    // documentStore 只读那份投影（否则两个 store 互相 import 成环）
    setCurrentProjectLabel(status.name)
    // 手里又有项目了：这一个再失效时仍要能把用户送回选择器
    armNoProjectRecovery()
    // 「这个项目上次开着哪份」要在换代**之前**读：换代会先换上一份空白文档，
    // 而那一档记的是「最近一份有内容的文档」，空白不会盖掉它——但读在前面
    // 才不依赖这条细节。
    const last = status.id ? readProjectDocument(status.id) : null
    await resetForNewProject()
    // 空白文档已经就位、`currentDoc` 已经指向它；要换成别的文档就在这里换，
    // 必须赶在 `phase: 'open'` 之前（见接口注释）
    let issue: ProjectDocumentRef | null = null
    if (opts?.prepareDocument) await opts.prepareDocument()
    else if (last && !(await restoreProjectDocument(last))) issue = last
    // 文档就位了就按它的页面适配视口。从 Project Picker 进来时舞台还没挂载
    // （量不到视口），`fit` 会把这次适配记成待办、舞台一量到尺寸就应用——
    // 改造前新项目沿用上一个项目留下的 175%（审计 T03）。
    {
      const page = useDocumentStore.getState().doc.page
      useViewportStore.getState().fit(page.w, page.h)
    }
    set({ project: status, phase: 'open', lastDocumentIssue: issue })
    void get().refreshRecent()
    emitActivity({ kind: 'project.opened', tutorial: status.tutorial === true })
    return status
  }

  /** 排进切换队列；排队 + 执行期间 `switching` 一直亮着 */
  let inFlight = 0
  const runSwitch = <T,>(fn: () => Promise<T>): Promise<T> => {
    inFlight += 1
    set({ switching: true })
    return switchQueue(fn).finally(() => {
      inFlight -= 1
      if (inFlight === 0) set({ switching: false })
    })
  }

  /**
   * 改收藏：排进收藏队列，一次一个操作，界面以回包为准。队列保证回包按发出顺序落地
   * （不会有旧回包盖新回包）；操作本身按路径描述，所以与别的标签页交错也不会互相盖。
   */
  const applyPinned = (op: PinnedOp | (() => PinnedOp)): Promise<void> => {
    // 入队那一刻的 pj：轮到执行时它变了（前一个操作撞上 409 no_project、pj 被清掉；或
    // 换了项目），这个操作就作废、一个请求都不发——不然 pj 为空的请求会落到后端的默认
    // 项目上，把一个来自失效会话的操作写进配置（Codex #550）
    const pj = currentProjectId()
    return pinQueue(async () => {
      if (currentProjectId() !== pj) return
      try {
        // 函数形式 = 轮到自己时才定操作（收藏开关：连点两下是开了又关，不是两次「开」）
        const pinned = await postPinnedOp(typeof op === 'function' ? op() : op)
        pinnedRev += 1
        set({ pinned })
      } catch (e) {
        useUiStore.getState().setStatus(backendErrorMsg(e), 'error')
      }
    })
  }

  return {
  phase: 'loading',
  project: null,
  recent: [],
  pinned: [],
  switching: false,
  opened: [],
  lastDocumentIssue: null,

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
      setCurrentProjectLabel(project.open ? project.name : null)
      const rev = pinnedRev
      const seq = ++listSeq
      const pj = currentProjectId()
      const [{ recent, pinned }, opened] = await Promise.all([
        fetchProjectLists(),
        fetchOpenProjects().catch(() => []),
      ])
      // 列表过期（期间又发过一次、或换了项目）时项目与阶段照常认，只是不写旧列表
      const fresh = seq === listSeq && pj === currentProjectId()
      set({
        project,
        phase: project.open ? 'open' : 'none',
        ...(fresh ? { recent, opened } : {}),
        ...(fresh && rev === pinnedRev ? { pinned } : {}),
      })
    } catch {
      // 后端不可达时也进 Picker——它会在重试里继续探测
      set({ phase: 'none' })
    }
  },

  refreshRecent: async () => {
    try {
      const rev = pinnedRev
      const seq = ++listSeq
      const pj = currentProjectId()
      const [{ recent, pinned }, opened] = await Promise.all([
        fetchProjectLists(),
        fetchOpenProjects().catch(() => []),
      ])
      if (seq !== listSeq || pj !== currentProjectId()) return
      set({ recent, opened, ...(rev === pinnedRev ? { pinned } : {}) })
    } catch {
      /* 列表刷新失败不致命 */
    }
  },

  open: (path, create = false) =>
    runSwitch(async () => adoptNow(await openProjectApi(path, create))),

  adoptOpenedProject: (status, opts) => runSwitch(() => adoptNow(status, opts)),

  switchTransaction: (fn) => runSwitch(() => fn(adoptNow)),

  togglePin: (path) =>
    applyPinned(() => ({
      op: get().pinned.some((p) => p.path === path) ? 'remove' : 'add',
      path,
    })),

  movePinned: (path, by) =>
    applyPinned(
      'delta' in by
        ? { op: 'move', path, delta: by.delta }
        : { op: 'move', path, to_path: by.toPath },
    ),

  remove: async (path) => {
    await removeRecentProject(path)
    await get().refreshRecent()
  },

  removeMany: async (paths) => {
    // 后端一次只移一条；失败的那几条留在列表里，下一次刷新如实显示
    await Promise.allSettled(paths.map((p) => removeRecentProject(p)))
    await get().refreshRecent()
  },

  openLastDocument: async () => {
    const ref = get().lastDocumentIssue
    if (!ref) return false
    // 走「最近文档」同一条路（读槽位 → 换文档 → 适配视口 → 说一句话）；
    // 它失败时自己会报「本机副本已不存在」那句
    await openRecentDocument(ref.id)
    const ok = useDocumentStore.getState().documentId === ref.id
    if (ok) set({ lastDocumentIssue: null })
    return ok
  },

  dismissLastDocumentIssue: () => set({ lastDocumentIssue: null }),

  showPicker: () => {
    if (get().switching) return
    set({ phase: 'none' })
  },

  returnToCurrent: () => {
    if (get().switching || get().project?.open !== true) return
    set({ phase: 'open' })
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
    set({ project: null, phase: 'none', lastDocumentIssue: null })
    // 选择器要用「最近 / 已打开」两份列表；这两个端点与项目无关，不会再 409
    void get().refreshRecent()
  },
  }
})

// 任何一个请求撞上 409 no_project 都会走到这里（检测在 lib/api.ts 的请求出口）
setNoProjectHandler(() => useProjectStore.getState().dropProject())

/**
 * 记「这个项目现在开着哪份文档」（`lib/projectDocs.ts`）。
 *
 * 键取**本标签页此刻认领的项目**（`currentProjectId()`），不取 `project`
 * 字段：`adoptOpenedProject` 先 `setCurrentProjectId(新)` 再换代，而 `project`
 * 要到最后一步才更新——换代期间那份空白文档若按 `project` 记，会记到**旧**
 * 项目名下，把用户在旧项目里停的那份顶掉。
 *
 * 只记有内容的文档（理由见 `projectDocs.ts`）；已经记着同一份 (id, 名字) 就
 * 不再写——文档 store 每次拖动都会变，不能每帧写一次 localStorage。
 */
useDocumentStore.subscribe((s, prev) => {
  if (
    s.documentId === prev.documentId &&
    s.doc === prev.doc &&
    s.canvases === prev.canvases &&
    s.projectMeta.name === prev.projectMeta.name
  ) {
    return
  }
  const pj = currentProjectId()
  if (!pj || !documentHasContent(s)) return
  const name = s.projectMeta.name
  // 与**存着的**那份比，不与内存里的缓存比：缓存会在站点数据被清掉之后
  // 继续说「已经记过了」，而一次 getItem 比一帧拖动便宜得多
  const cur = readProjectDocument(pj)
  if (cur && cur.id === s.documentId && cur.name === name) return
  rememberProjectDocument(pj, { id: s.documentId, name })
})
