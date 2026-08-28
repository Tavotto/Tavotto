/**
 * 交接请求：外部程序（Codex 插件、`tavotto open`、编辑器、别的 Agent）把一张
 * 刚画好的图送进来时，前端要做的三件事——切到那个项目、把面板放进画布、选中它。
 *
 * 两条入口共用这一份实现，**语义必须完全一样**：
 *   * 浏览器模式 / 桌面首启：地址栏 `?open=<stem>`（项目已由后端 `--figures`
 *     或 `?pj=` 认领），落地形状的唯一出处是
 *     `src/tavotto/engine/handoff.py` 的 `browser_url()`；
 *   * 桌面二次交接：Tauri 事件 `tavotto:open`（带 project + stem）——单实例插件
 *     把第二次启动的 argv 转发给已经在跑的窗口，后端一套进程不动。
 *
 * 三条纪律：
 *  1. **同一个项目绝不调 projectStore.open**：那条路会 switchDocument 成空白
 *     文档，用户正在排的版当场没了。同项目只重扫素材。
 *  2. **必须重扫素材**：交接的图是刚刚才写到磁盘上的，运行中的实例手里那份
 *     panels 是旧的，不重扫就是「打开了，但说找不到这张图」。
 *  3. **找不到就说找不到**：绝不退而求其次选中别的面板——用户要的是那一张。
 */
import { msg } from '@/i18n'
import { addPanel, addRuntimePanel } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useProjectStore } from '@/store/projectStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { backendErrorText } from '@/lib/api'
import type { PanelInfo, RuntimeAssetInfo } from '@/lib/api'

export interface OpenRequest {
  /** 图库目录绝对路径；桌面事件才有，URL 形态下项目由后端认领 */
  project?: string | null
  /** 产物文件名主干（Fig1_kinetics）——注册表与引擎认的就是它。
   *  可以没有：`tavotto open <目录>` 是「把这个图库打开」，不指定面板。 */
  stem?: string | null
  /** 多 Figure 交接：脚本的项目相对路径（`tavotto open` 的 `?pick=` /
   *  `--pick-script`）。不静默选第一张——打开 Figure 选择器让用户挑。 */
  pick?: string | null
  /** `tavotto run` 的一次性交接 ID（ADR 0021 §4）。**不透明串，不是凭据**
   *  ——token 与端口都在那份 0600 的 descriptor 里，这边只拿得到 ID。
   *  与 stem / pick **不互斥**：那两个说"打开哪张图"，这个说"有一条 native
   *  会话在等你确认"。 */
  native?: string | null
}

export type OpenOutcome =
  | 'placed'           // 面板已加入画布并选中
  | 'selected'         // 画布上本来就有，只是选中
  | 'picker'           // 多 Figure：已打开 Figure 选择器
  | 'runtime-uncached' // 运行时图已登记但还没有预览，引导去素材库运行
  | 'project-only'     // 只交接了项目，没指定面板
  | 'native-pending'   // `tavotto run` 的确认已排队，没有别的面板要落
  | 'missing'          // 项目里找不到这个 stem
  | 'no-project'       // 没有项目可落
  | 'failed'

/** 素材 id 是图库相对路径（可能带子目录）；stem = 去目录去扩展名。 */
export function stemOf(fileId: string): string {
  const base = fileId.split(/[/\\]/).pop() ?? fileId
  const dot = base.lastIndexOf('.')
  return dot > 0 ? base.slice(0, dot) : base
}

function findByStem(panels: PanelInfo[], stem: string): PanelInfo | undefined {
  // 同 stem 的 PDF 与 PNG 可能同时在（写回会两个载体一起更新）：矢量优先，
  // 它才是能进图内编辑、能导出真矢量的那一份。
  const hits = panels.filter((p) => stemOf(p.id) === stem)
  return hits.find((p) => p.kind === 'pdf') ?? hits[0]
}

const norm = (p: string) => p.replace(/[/\\]+$/, '')

/**
 * 地址栏里的 `?open=<stem>`。认下之后立刻抹掉——项目与面板归属都在应用状态里，
 * 地址栏留着它只会在用户之后自己换项目时变成一个撒谎的 URL（与 lib/session.ts
 * 处理 `?pj=` 的理由完全一样）。
 */
export function readOpenRequestFromUrl(): OpenRequest | null {
  try {
    const params = new URLSearchParams(window.location.search)
    const stem = params.get('open')
    const pick = params.get('pick')
    // `?native=` 是 `tavotto run` 首启这一条路（壳的 `landing_query`）。
    // 二次交接走 `tavotto:open` 事件——两条都必须带它，漏掉哪一条，那一条
    // 上的 CLI 就一直挂在「Waiting for Tavotto desktop…」上直到超时。
    const native = params.get('native')
    if (!stem && !pick && !native) return null
    const url = new URL(window.location.href)
    url.searchParams.delete('open')
    url.searchParams.delete('pick')
    url.searchParams.delete('native')
    window.history.replaceState(null, '', url.pathname + url.search + url.hash)
    // stem 定得下来一张就不需要选择器（后端生产侧本来就互斥，这里同语义）；
    // native 与它们不互斥，单独带上
    return { ...(stem ? { stem } : pick ? { pick } : {}), ...(native ? { native } : {}) }
  } catch {
    return null
  }
}

/** 画布上已有这个 fileId 的面板就只选中它（重复交接不叠面板）。 */
function selectExisting(fileId: string): boolean {
  const existing = useDocumentStore
    .getState()
    .doc.objects.find((o) => o.type === 'panel' && o.fileId === fileId)
  if (!existing) return false
  useSelectionStore.getState().set([existing.id])
  return true
}

/** 执行一次交接。返回值给测试与调用方看，用户看到的是选中的面板或一条 toast。 */
export async function applyOpenRequest(req: OpenRequest): Promise<OpenOutcome> {
  const stem = (req.stem ?? '').trim()
  const pick = (req.pick ?? '').trim()
  const native = (req.native ?? '').trim()
  if (!stem && !pick && !native && !req.project) return 'failed'
  const ui = useUiStore.getState()

  /**
   * `tavotto run` 的确认屏排队。
   *
   * 与「打开哪张图」互不相干，所以**每条出口都要排**——包括项目没能打开的
   * 那两条：确认屏自带项目路径 / 解释器 / 工作目录，attach 也不依赖界面此刻
   * 开着哪个项目。不排的表现是那个终端一直挂到 attach 超时，而界面上什么都
   * 没发生过。
   *
   * 顺序是硬的：**必须在 `proj.open()` 之后**——换项目会把 native 会话状态
   * 整个换代掉（projectStore 的 resetForNewProject），排在前面等于白排。
   */
  const queueNative = () => {
    if (native) void useNativeSessionStore.getState().receive(native)
  }

  try {
    const proj = useProjectStore.getState()
    const current = proj.project?.figures_dir
    if (req.project && (!current || norm(current) !== norm(req.project))) {
      // 换项目：projectStore.open 里已经先冲刷了当前文档的自动保存
      await proj.open(req.project)
    } else if (proj.phase !== 'open') {
      queueNative()
      ui.setStatus(msg('handoff.noProject', undefined, 'project'), 'error')
      return native ? 'native-pending' : 'no-project'
    } else {
      await useAssetStore.getState().load()
    }
  } catch (err) {
    queueNative()
    ui.setStatus(
      msg('handoff.openFailed', { error: backendErrorText(err) }, 'project'),
      'error',
    )
    return 'failed'
  }
  queueNative()

  // 多 Figure（`tavotto open script.py` 产出不止一张）：打开 Figure 选择器，
  // **绝不静默选第一张**——选择信息（脚本）由 CLI 带进来，挑哪张归用户。
  if (pick) {
    await useRuntimeAssetStore.getState().loadAssets()
    useFigurePickerStore.getState().open(pick)
    return 'picker'
  }

  // `tavotto run`：这次交接的全部内容就是那一屏确认，没有面板要落。
  // （图要等用户确认、脚本跑到第一个屏障之后才存在。）
  if (!stem && native) return 'native-pending'

  // `tavotto open <目录>`：只把图库换过来，不指定面板
  if (!stem) {
    ui.setStatus(
      msg('handoff.projectOpened', { name: useProjectStore.getState().project?.name ?? '' }, 'project'),
    )
    return 'project-only'
  }

  const info = findByStem(useAssetStore.getState().panels, stem)
  // runtime 清单永远查一次（只读端点，不触发执行）：pyplot 捕获的图
  // **从来没有原件**，磁盘上同名文件只是旧样本——那种碰撞下 runtime
  // 素材优先，否则交接打开的是陈旧文件（Codex 评审 P1）。
  await useRuntimeAssetStore.getState().loadAssets()
  const asset: RuntimeAssetInfo | undefined = (
    useRuntimeAssetStore.getState().assets ?? []
  ).find((a) => a.stem === stem)
  const preferRuntime = asset != null && asset.descriptor?.capture_source === 'pyplot'
  if (!info || preferRuntime) {
    // 磁盘上没有这张图 ≠ 没有这张图：`tavotto open script.py` 探测出的
    // show-only Figure 是 RuntimeFigureAsset（没有原件）。
    if (!asset) {
      ui.setStatus(msg('handoff.panelMissing', { stem }, 'project'), 'error')
      return 'missing'
    }
    if (selectExisting(asset.id)) {
      ui.setStatus(msg('handoff.located', { name: asset.stem }, 'project'))
      return 'selected'
    }
    if (!asset.descriptor) {
      // 已登记但从没跑出过预览（cache 物化失败/被清理）：不造假值，
      // 引导去素材库跑一次
      ui.setStatus(msg('handoff.runtimeNeedsRun', { stem }, 'project'), 'error')
      return 'runtime-uncached'
    }
    addRuntimePanel(asset.descriptor)
    ui.setStatus(msg('handoff.added', { name: asset.stem }, 'project'))
    return 'placed'
  }

  // 画布上已经有这张图了就只选中它，不再叠一份（重复交接同一张是常态：
  // 用户在 Codex 里改一版就交一次，每次都新增会堆出一摞同名面板）
  if (selectExisting(info.id)) {
    ui.setStatus(msg('handoff.located', { name: info.name }, 'project'))
    return 'selected'
  }

  addPanel(info)
  ui.setStatus(msg('handoff.added', { name: info.name }, 'project'))
  return 'placed'
}
