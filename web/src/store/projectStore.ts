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
  loadProjectDocument,
  readProjectDocument,
  rememberProjectDocument,
  type ProjectDocumentRef,
} from '@/lib/projectDocs'
import { isForeignDocument } from '@/lib/docOwnership'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import { pushPickerEntry } from '@/lib/pickerHistory'
import { cancelActivePointerGesture, finishActiveGesture } from '@/store/gestureCoordinator'
import { markMoment } from '@/lib/timelineCheckpoint'
import { useTimelineStore } from '@/store/timelineStore'
import { openRecentDocument } from '@/store/actions'
import { useAssetBrowseStore } from '@/store/assetBrowseStore'
import { flushAutosave, loadAutosavedDocument, pinDocumentOwner, useDocumentStore } from '@/store/documentStore'
import { useAiStore } from '@/store/aiStore'
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
import { useScriptInputStore } from '@/store/scriptInputStore'
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
   * 去 Project Picker（顶栏左上角的「回到项目列表」、设置「切换项目」、桌面菜单「打开项目」、
   * 教程收尾「打开自己的项目」）。离开前收尾连续编辑并冲刷自动保存，再在浏览器历史里
   * 占一格（`lib/pickerHistory.ts`），后退键回到编辑器。
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

/**
 * 离开当前文档（去 Picker / 换项目 / 项目失效）之前，把还开着的手势收掉，**再**冲刷。
 *
 *  - 指针手势（拖动 / 缩放 / 框选 / 绘制）按取消处理，与拖动中按 Esc 同一条出口：
 *    `trackPointer` 的监听挂在 window 上、工作台卸载后还活着，不收的话用户在 Picker 上
 *    松手才提交——那时冲刷已经做完、自动保存的订阅也摘了，这一笔只在内存里（Codex #661）。
 *  - 属性栏的连续编辑（安静计时器）按完成处理：卸载时它只注销不收尾，事务会悬着。
 */
function settleGesturesBeforeLeaving(): void {
  cancelActivePointerGesture()
  finishActiveGesture()
}

/**
 * 「离开这份排版」的顺序只有这一份（回主页 `showPicker`、编辑器开着时直接切项目 `adoptNow`
 * 共用）：**先把开着的手势收掉，再打「离开」点**——拍的是落定之后的内容，不是拖到一半 /
 * 连续编辑中间的样子（Codex #679）。打点在认领新项目之前、同步取走节点的项目与文档。
 */
function settleAndMarkLeaving(leaving: boolean): void {
  settleGesturesBeforeLeaving()
  if (leaving) void markMoment('close')
}

/** 换项目时把属于旧项目的前端会话状态全部丢掉。 */
async function resetForNewProject() {
  // 1. 冲刷当前文档的自动保存（切走的文档可从「最近文档」取回）；手势先收掉，
  //    否则松手那一笔会落进换上来的空白文档
  settleGesturesBeforeLeaving()
  flushAutosave()
  // 2. 清选择 / 图内编辑态 / 渲染缓存
  useSelectionStore.getState().set([])
  const ui = useUiStore.getState()
  ui.setElementPanel(null)
  ui.setEditingText(null)
  ui.setCropTarget(null)
  useRenderStore.getState().clear()
  useRuntimeAssetStore.getState().clear()
  // 时间线的预览属于旧项目的排版（ADR 0101）
  useTimelineStore.getState().clear()
  // 素材库的搜索词与筛选说的是旧项目的目录与素材，跟着清
  useAssetBrowseStore.getState().clear()
  // 素材清单本身也属于旧项目：面板、「无法使用」清单与由它们派生的来源目录。不清的话，
  // 新项目的 /api/panels 挂起或失败时 B 下面显示的是 A 的卡片和 A 的文件名（#577）；
  // 换代同时作废 A 还在飞的那次请求
  useAssetStore.getState().clear()
  // 改图助手的对话属于旧项目：会话列表丢掉并换代，在途的发起 / 撤销 / 中止回来不落地（#589）。
  // 后端任务不取消——它照样改完、记进 A 的历史，切回 A 在历史里看得到
  useAiStore.getState().clear()
  // 版本缩略图按 (项目, 素材版本, 变体) 缓存 blob：换项目时整表释放，
  // 既是回收 blob，也是防止旧项目的图被当成新项目某个版本的预览
  clearVariantPngCache()
  // 脚本运行状态机换代（在途 probe 响应作废，绝不落进新项目）+ 脚本清单清空
  useScriptRunStore.getState().clear()
  useScriptLibraryStore.getState().clear()
  // 脚本 input() 的问答与记住的答案都属于旧项目（ADR 0099）：换代清空。**后端那一问不取消**——
  // A 的脚本照样在等，切回 A 时 `loadAnswers()` 从 pending 把对话框接回来
  useScriptInputStore.getState().clear()
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
    // 排版时间线（ADR 0101）：从一个开着的项目**直接**切到另一个，是在关掉前一个。
    // 必须在认领新项目之前打：节点的项目、文档、缩略图图源都在这一刻同步取走；
    // 手势先收掉再打（`settleAndMarkLeaving`，与回主页同一份顺序）
    settleAndMarkLeaving(
      get().phase === 'open' && !!get().project?.id && get().project?.id !== status.id,
    )
    // 先认领项目，再做任何会发请求的事：素材/渲染都必须落到新项目上。
    // 从认领到换代完成这段时间里内存里还是**上一个项目**的文档，而下面要 await 一次后端
    // （`loadProjectDocument`）：这期间用户改一笔 / 派生更新落地，「记上次开着哪份」的订阅会
    // 按新 pj 把旧项目的文档记到新项目名下（#719 Codex P1）。这段时间让它停记
    // 停到换代完成为止（`resume`），之后恢复出来的那份照常记
    let held = true
    rememberSuspended += 1
    const resume = () => {
      if (!held) return
      held = false
      rememberSuspended -= 1
    }
    try {
      return await adoptSteps(status, opts, resume)
    } finally {
      resume()
    }
  }

  const adoptSteps = async (
    status: ProjectStatus,
    opts: Parameters<ProjectState['adoptOpenedProject']>[1],
    resume: () => void,
  ): Promise<ProjectStatus> => {
    // 认领新项目之前把内存里这份排版的归属钉在旧项目上：下面换代时那次冲刷写的是旧项目的排版
    pinDocumentOwner()
    if (status.id) setCurrentProjectId(status.id)
    // 「最近文档」要在条目上标出所属项目（审计 T04）；名字的权威在这里，
    // documentStore 只读那份投影（否则两个 store 互相 import 成环）
    setCurrentProjectLabel(status.name)
    // 手里又有项目了：这一个再失效时仍要能把用户送回选择器
    armNoProjectRecovery()
    // 「这个项目上次开着哪份」要在换代**之前**读：换代会先换上一份空白文档，
    // 而那一档记的是「最近一份有内容的文档」，空白不会盖掉它——但读在前面
    // 才不依赖这条细节。
    // 读的是**后端**的记录（#715 PR-B）：桌面版换了端口就是换了 origin，本机那份缓存是空的；
    // 后端没有这组端点（404）时 `loadProjectDocument` 退回本机缓存，即改造前的行为。
    const last = status.id ? await loadProjectDocument(status.id) : null
    await resetForNewProject()
    resume()
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
    // 排版时间线的关键时刻（ADR 0101 §3）：只管「编辑器开着时直接切到另一个项目」
    // ——Workspace 不重挂、时间线一直在跑。从 Picker 打开 / 启动恢复时 Workspace 还没
    // 挂上，这一下是空的，那两条路由 Workspace 在文档恢复完之后调 `markWorkspaceOpened()`
    void markMoment('open')
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
    // 去 Picker = 工作台整个卸载：自动保存的防抖计时器被取消、beforeunload 兜底被摘掉，
    // 开着的手势（拖动、改字号的安静计时器）不会自己收尾。所以离开之前先把它们收干净、
    // 再立刻冲刷一次——防抖窗口里的最后一下改动，不能等到用户在 Picker 上关掉窗口才发现没了。
    // 与切项目（`resetForNewProject`）、`dropProject` 是同一句 `flushAutosave()`。
    // 排版时间线的关键时刻（ADR 0101）：回主页 = 离开这份排版（先收手势、再打点）
    settleAndMarkLeaving(get().phase === 'open')
    flushAutosave()
    set({ phase: 'none' })
    pushPickerEntry()
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
    settleGesturesBeforeLeaving()
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
 * `rememberProjectDocument` 同时推给后端（#715 PR-B，后端为准），所以「同值不写」也挡住了
 * 每帧一个 PUT。
 */
/** 大于 0 = 正在切项目、内存里还是上一个项目的文档：下面的订阅不记（见 `adoptNow`） */
let rememberSuspended = 0

useDocumentStore.subscribe((s, prev) => {
  if (rememberSuspended > 0) return
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
  // 确知属于别的项目的排版不记到这个项目名下（#715 验收 P1，判据唯一出处 `lib/docOwnership`）。
  // 排在「同值不写」之后：拖动的每一帧走不到这里。从「最近文档」里显式打开别的项目的排版并改过
  // 之后，自动保存按这个项目重新记它的归属（本机索引与后端 owners 都是），那之后的下一次变化照常记
  if (isForeignDocument(s.documentId, pj)) return
  rememberProjectDocument(pj, { id: s.documentId, name })
})
