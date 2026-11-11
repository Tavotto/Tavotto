import { create } from 'zustand'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { intakePastedImages, pastedAssetFiles } from '@/lib/canvasFileIntake'
import { newId } from '@/lib/id'
import { migrateFigureFrames } from '@/lib/figureFrameMigration'
import { figureTextToAnnotation } from '@/lib/figureTextCopy'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { exactPanelManifest, structuralPanelManifest, useRenderStore } from '@/store/renderStore'
import { deferUserEntry } from '@/store/documentRestore'
import { runDiscreteAction } from '@/store/gestureCoordinator'
import { useSelectionStore } from '@/store/selectionStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useUiStore } from '@/store/uiStore'
import type { CanvasObject, LayoutGroup, PanelObject } from '@/types/document'
import { isRuntimePanel, objectLabel } from '@/types/document'
import { modKey } from '@/lib/utils'
import { DUPLICATE_OFFSET_MM } from '@/lib/units'

/**
 * 对象剪贴板：⌘C/⌘V，走系统剪贴板（JSON + 魔数）。
 *
 * - 系统剪贴板意味着**跨标签页 / 跨布局文档**天然可用；
 * - 只有画布上有选中对象时 ⌘C 才写入剪贴板，绝不劫持普通文本复制；
 *   ⌘V 读到的不是本工具的负载时按无事发生处理（不吞别人的粘贴）；
 * - 粘贴保留相对位置、层级次序、成组关系与图内 overrides；
 * - 目标环境缺素材（跨图库 / 素材被删）时弹重新链接对话框，
 *   绝不静默生成空面板。
 */

import { CLIPBOARD_FORMAT } from '@/lib/brand'
import { clearAdoption } from '@/lib/artifactValidation'

/**
 * 只认一个魔数。改名到 Tavotto 时旧魔数（`magplot/objects@1` 与更早的
 * `magic-matplot/objects@1`）一并去掉了——见 `lib/brand.ts` 的干净断裂说明。
 * 数组这个形状留着：它是「读取端可以比写出端宽」的接口，将来真要认第二种
 * 负载时不必再改调用方。
 */
const MAGIC = CLIPBOARD_FORMAT
const MAGICS: string[] = [CLIPBOARD_FORMAT]

interface ClipPayload {
  magic: string
  sourceDocId: string
  /** 复制发生时的画布；旧负载无此字段（单画布时代 doc 即 canvas） */
  sourceCanvasId?: string
  /** 数组序 = 原文档 z 序（底 → 顶） */
  objects: CanvasObject[]
  /** 完整包含在选区里的布局组约束 */
  layoutGroups: LayoutGroup[]
}

/** 当前选区的剪贴板负载；没有可复制的对象返回 null */
function buildClipPayload(): ClipPayload | null {
  const ids = useSelectionStore.getState().ids
  if (!ids.length) return null
  const { doc, documentId, activeCanvasId } = useDocumentStore.getState()
  const objects = doc.objects.filter((o) => ids.includes(o.id))
  if (!objects.length) return null
  const carriedGroups = (doc.layoutGroups ?? []).filter((g) => {
    const members = doc.objects.filter((o) => o.groupId === g.id)
    return members.length >= 2 && members.every((o) => ids.includes(o.id))
  })
  return {
    magic: MAGIC,
    sourceDocId: documentId,
    sourceCanvasId: activeCanvasId,
    objects: structuredClone(objects),
    layoutGroups: structuredClone(carriedGroups),
  }
}

/**
 * 图内编辑里选中的元素 → 剪贴板负载（ADR 0163「图内文字复制为标注」）。负载里放的是**已经换算好的文字标注**
 * （与原文字在页面上重合，见 `lib/figureTextCopy`），所以跨标签页 / 跨排版 / 菜单与 ⌘V 走的是同一条对象粘贴路径，
 * 没有第二种负载。
 *
 * `payload` 为 null 时 `refusedName` 说是哪个元素「还不能复制」（曲线 / 色块 / 刻度组……，或权威 manifest 此刻还没就位——
 * 读 bbox 之后要写文档，旧清单不作数，ADR 0017）；两者都是 null = 选中的东西已经不在清单里，什么都不做。
 */
export function buildElementClipPayload(
  panelId: string,
  gids: readonly string[],
): { payload: ClipPayload | null; refusedName: string | null } {
  const { doc, documentId, activeCanvasId } = useDocumentStore.getState()
  const panel = doc.objects.find((o): o is PanelObject => o.id === panelId && o.type === 'panel')
  if (!panel) return { payload: null, refusedName: null }
  const render = useRenderStore.getState()
  const exact = exactPanelManifest(render, panel)
  const shown = exact ?? structuralPanelManifest(render, panel)
  const els = gids.flatMap((g) => shown?.elements.find((e) => e.gid === g) ?? [])
  if (!els.length) return { payload: null, refusedName: null }
  const objects = exact
    ? els.flatMap((el) => {
        const o = figureTextToAnnotation(panel, el)
        return o ? [o] : []
      })
    : []
  if (!objects.length) {
    const first = els.find((e) => !e.text_block) ?? els[0]
    // 只说元素类别（元素树里已有的类别词）；类别不认识时空串 = 「这个元素还不能复制」
    return { payload: null, refusedName: translate(`role.${first.role}`, { ns: 'inspector', defaultValue: '' }) }
  }
  return {
    payload: {
      magic: MAGIC,
      sourceDocId: documentId,
      sourceCanvasId: activeCanvasId,
      objects,
      layoutGroups: [],
    },
    refusedName: null,
  }
}

/** 现在是不是在图内编辑里选着元素：⌘C 复制的是元素（文字 → 标注），不是整张图 */
function selectedElements(): { panelId: string; gids: string[] } | null {
  const ui = useUiStore.getState()
  return ui.elementPanelId && ui.selectedGids.length
    ? { panelId: ui.elementPanelId, gids: [...ui.selectedGids] }
    : null
}

/**
 * 这个节点在一个**没有焦点**的输入控件 / 可编辑文本里（焦点不在它里面）。属性页的文字框在选中带文字的元素那一刻抢焦点并全选、
 * 随后焦点还给页面，框里留着一段残留的选区——这时按 ⌘C，浏览器把 copy 事件派给那个文字框（事件目标是选区所在的可编辑节点，
 * 不是焦点元素），ProseMirror 也会自己接走。那不是用户在复制的东西：图内编辑里选着元素时，复制的是元素。
 */
function inIdleEditor(node: Node | null): boolean {
  const at = node instanceof Element ? node : node?.parentElement
  const editor = at?.closest('[contenteditable], input, textarea')
  return !!editor && !editor.contains(document.activeElement)
}

/** 顶栏胶囊：这个元素还不能复制（只用状态胶囊，不挤页面） */
function announceNotCopyable(name: string | null): void {
  if (name == null) return
  useUiStore.getState().setStatus(name ? note('elementNotCopyable', { name }) : note('elementNotCopyableGeneric'), 'info')
}

/**
 * 右键弹层的「复制为标注」：复制 `gids` 里的文字元素（点击是用户手势，`writeText` 各浏览器都放行）。
 * 不能复制的元素只出胶囊、不动剪贴板。
 */
export async function copyElementsAsAnnotations(panelId: string, gids: readonly string[]): Promise<boolean> {
  const { payload, refusedName } = buildElementClipPayload(panelId, gids)
  if (!payload) {
    announceNotCopyable(refusedName)
    return false
  }
  try {
    await navigator.clipboard.writeText(JSON.stringify(payload))
  } catch {
    useUiStore.getState().setStatus(note('clipboardWriteDenied'), 'error')
    return false
  }
  announceCopied(payload)
  return true
}

/** 剪贴板的历史标签与提示都在 workspace 命名空间 */
const hist = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`history.${key}`, values, 'workspace')
const note = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`status.${key}`, values, 'workspace')

function announceCopied(payload: ClipPayload): void {
  useUiStore
    .getState()
    .setStatus(
      payload.objects.length === 1
        ? note('objectCopied', { name: objectLabel(payload.objects[0]) })
        : note('objectsCopied', { count: payload.objects.length }),
      'done',
    )
}

/** 右键菜单 / 属性页按钮的复制入口（点击是用户手势，writeText 各浏览器都放行） */
export async function copySelectedObjects(): Promise<boolean> {
  const payload = buildClipPayload()
  if (!payload) return false
  try {
    await navigator.clipboard.writeText(JSON.stringify(payload))
  } catch {
    useUiStore.getState().setStatus(note('clipboardWriteDenied'), 'error')
    return false
  }
  announceCopied(payload)
  return true
}

/**
 * ⌘C 的主路径：原生 copy 事件。`e.clipboardData.setData` 同步写、无权限门槛，
 * Safari / 桌面壳的 WKWebView / Firefox 都认——异步的 `writeText` 在 WebKit 里
 * 会被拒，正是「复制的素材无法跨标签页粘贴」的根源。返回 true = 消费了本次复制。
 */
export function handleCopyEvent(e: ClipboardEvent): boolean {
  // 页面上有真实文字选区（比如在报错 toast 里选了段文字）时让位给原生文本复制。
  // 例外：图内编辑里选着元素时，属性页的文字框会在选中那一刻抢焦点并全选、随后焦点还给页面——框里留着一段**没有焦点**的
  // 残留选区（选中标题后立刻按 ⌘C 就撞上它）。那不是用户在复制的东西：用户复制的是选中的元素
  const sel = document.getSelection?.()
  // （源码模式的文字框是 textarea：它里面的选区在 document 选区里只露出外层容器，所以事件目标也要看一眼）
  const idleEditor = selectedElements() !== null && (inIdleEditor(sel?.anchorNode ?? null) || inIdleEditor(e.target as Node | null))
  if (sel && !sel.isCollapsed && !idleEditor) return false
  const data = e.clipboardData
  if (!data) return false
  // 离散动作闸门（输入框 / 对话框里让位、不收手势）：不让位时先落定开着的连续编辑，
  // 复制到的是画面上看到的那一版（方向键微调那一段只动了预览、还没写进文档）
  const picked = selectedElements()
  return (
    // 残留在没有焦点的文字框里的选区不算「焦点在输入框里」：按页面本身走（见 `inIdleEditor`）
    runDiscreteAction('canvas', picked && inIdleEditor(e.target as Node | null) ? document.body : e.target, () => {
      // 图内编辑里选着元素：复制的是元素（文字 → 标注）。不能复制的（曲线、色块……）也要消费这次复制——
      // 放过去的话选区里那张整图会被复制走，与用户选中的东西不是一回事
      if (picked) {
        const { payload: fromElements, refusedName } = buildElementClipPayload(picked.panelId, picked.gids)
        e.preventDefault()
        if (fromElements) {
          // 文字框（ProseMirror）可能已经先把自己的 text/html 写进来了：清掉，只留我们的负载
          data.clearData?.()
          data.setData('text/plain', JSON.stringify(fromElements))
          announceCopied(fromElements)
        } else announceNotCopyable(refusedName)
        return true
      }
      const payload = buildClipPayload()
      if (!payload) return false
      e.preventDefault()
      data.setData('text/plain', JSON.stringify(payload))
      announceCopied(payload)
      return true
    }) ?? false
  )
}

/**
 * ⌘V 的主路径：原生 paste 事件，`e.clipboardData.getData` 同步读。
 * 不是本工具的负载就不拦（返回 false），普通文本粘贴照旧。
 */
export function handlePasteEvent(e: ClipboardEvent): boolean {
  const text = e.clipboardData?.getData('text/plain') ?? ''
  const payload = parsePayload(text)
  if (!payload) return pasteImageFiles(e, text)
  // 桌面壳「编辑 → 粘贴」与预置 ⌘V 加速键直达这里，不经 keydown：先过离散动作闸门——
  // 输入框 / 对话框里让位（不收手势），否则先落定开着的连续编辑，粘贴不并进方向键
  // 微调那条撤销（Codex #671）
  return (
    runDiscreteAction('canvas', e.target, () => {
      if (!consumePayload(payload)) return false
      e.preventDefault()
      return true
    }) ?? false
  )
}

/**
 * 剪贴板里不是本工具的对象负载、而是图片文件（截图、从文件管理器复制的 PNG / PDF / SVG…）：放进画布（ADR 0176）。
 * 与拖入同一条导入路径（`canvasFileIntake`：复制进项目文件夹成为素材 → `addPanelToCanvas`）。
 * **有文字就不拦**：电子表格 / 网页的复制常带一张预览图，那是在粘文字，外部文字粘贴不在这里管；
 * 输入框 / 对话框里让位（与对象粘贴同一个 `runDiscreteAction` 判据），浏览器照常处理。
 */
function pasteImageFiles(e: ClipboardEvent, text: string): boolean {
  if (text.trim()) return false
  const files = pastedAssetFiles(e.clipboardData)
  if (!files.length) return false
  return (
    runDiscreteAction('canvas', e.target, () => {
      e.preventDefault()
      void intakePastedImages(files)
      return true
    }) ?? false
  )
}

export function parsePayload(text: string): ClipPayload | null {
  if (!MAGICS.some((m) => text.includes(m))) return null
  try {
    const data = JSON.parse(text)
    if (!MAGICS.includes(data?.magic) || !Array.isArray(data.objects)) return null
    return data as ClipPayload
  } catch {
    return null
  }
}

/* --------------------------- 缺失素材的重新链接 ----------------------------- */

export interface MissingAsset {
  fileId: string
  name: string
  /** 引用它的剪贴板面板数量 */
  count: number
  /** 用户选择：重新链接到哪个现有素材；undefined = 跳过 */
  relinkTo?: string
}

interface ClipboardState {
  /**
   * 等待用户处理缺失素材：
   * - paste：粘贴流程，确认后把剪贴板对象落进文档
   * - relink：项目包导入后就地重链接当前文档里的缺失面板
   */
  pending:
    | { mode: 'paste'; payload: ClipPayload; missing: MissingAsset[] }
    /**
     * `relocate`：导出时发现图挪了位置、来重新找它（「重新找到这张图」）——是**同一张图换了地址**，
     * 面板上的图内修改原样保留；不带 = 项目包导入后的素材替换，修改换成新素材自己的基线（原行为）。
     */
    | { mode: 'relink'; missing: MissingAsset[]; relocate?: boolean }
    | null
  setPending: (p: ClipboardState['pending']) => void
}

export const useClipboardStore = create<ClipboardState>((set) => ({
  pending: null,
  setPending: (pending) => set({ pending }),
}))

/**
 * 面板引用的素材在当前项目里找得到吗。静态素材看 assetStore；运行时图（`runtime:` fileId）不在
 * assetStore 里，身份在 runtimeAssetStore 的清单（后端注册表）。三态：清单里有 = 在；清单已取回
 * 而没有 = 缺失；清单为 null（未知）按缺失处理，保守走重链接，同时后台取一次清单（不等它），
 * 下一次判据就准了。这里绝不异步等：等待期间用户可能切了项目 / 文档或开始了别的手势。
 */
function panelSourceKnown(p: PanelObject): boolean {
  if (useAssetStore.getState().byId[p.fileId]) return true
  if (!isRuntimePanel(p)) return false
  const store = useRuntimeAssetStore.getState()
  if (store.assets === null) {
    void store.loadAssets()
    return false
  }
  return store.assets.some((a) => a.id === p.fileId)
}

/** 项目包导入后：把当前文档里缺失素材的面板交给重链接对话框处理 */
export function requestRelinkMissing(): number {
  const { doc } = useDocumentStore.getState()
  const missingMap = new Map<string, MissingAsset>()
  for (const o of doc.objects) {
    if (o.type !== 'panel' || panelSourceKnown(o)) continue
    const cur = missingMap.get(o.fileId)
    if (cur) cur.count += 1
    else {
      missingMap.set(o.fileId, {
        fileId: o.fileId,
        name: o.name ?? o.fileId.split('/').pop() ?? o.fileId,
        count: 1,
      })
    }
  }
  if (missingMap.size) {
    useClipboardStore.getState().setPending({ mode: 'relink', missing: [...missingMap.values()] })
  }
  return missingMap.size
}

/**
 * 导出时发现某张图不在了：只把引用这一张的面板交给重链接对话框（同名素材自动配上）。
 * 文档里没有面板引用它 = false，不弹框。
 */
export function requestRelinkSource(fileId: string): boolean {
  const { doc } = useDocumentStore.getState()
  let missing: MissingAsset | null = null
  for (const o of doc.objects) {
    if (o.type !== 'panel' || o.fileId !== fileId) continue
    if (missing) missing.count += 1
    else missing = { fileId, name: o.name ?? fileId.split(/[\\/]/).pop() ?? fileId, count: 1 }
  }
  if (!missing) return false
  useClipboardStore.getState().setPending({ mode: 'relink', missing: [missing], relocate: true })
  return true
}

/**
 * 就地重链接：把文档里引用缺失素材的面板换到所选素材（一条历史）。
 * `keepEdits`：同一张图换了地址（导出时「重新找到这张图」）——面板上用户的图内修改不动，
 * 不拿新素材的基线盖掉，否则紧接着的自动重新导出会把没写回的修改悄悄丢掉。
 */
export function materializeRelink(resolved: MissingAsset[], { keepEdits = false }: { keepEdits?: boolean } = {}): void {
  const assets = useAssetStore.getState().byId
  const relink = resolved.filter((m) => m.relinkTo && assets[m.relinkTo])
  if (!relink.length) {
    useUiStore.getState().setStatus(note('relinkNone'), 'info')
    return
  }
  const byFileId = new Map(relink.map((m) => [m.fileId, assets[m.relinkTo!]]))
  useDocumentStore.getState().commit(hist('relinkAssets', { count: relink.length }), (d) => {
    for (const o of d.objects) {
      if (o.type !== 'panel') continue
      const info = byFileId.get(o.fileId)
      if (!info) continue
      clearAdoption(o)
      o.fileId = info.id
      o.fileKind = info.kind
      o.nativeW = info.native_w_mm
      o.nativeH = info.native_h_mm
      o.pxW = info.px_w
      o.pxH = info.px_h
      o.script = info.script ?? null
      o.cost = info.cost
      o.name = info.name
      if (!keepEdits) o.overrides = info.baked_overrides ? structuredClone(info.baked_overrides) : []
    }
    // 新素材的写回基线原样放进来（身份不按旧素材的 manifest 重抄，ADR 0083）
  }, { overrides: 'restored' })
  useUiStore.getState().setStatus(note('relinked', { count: relink.length, undo: modKey('Z') }), 'done')
}

/**
 * 菜单里的「粘贴」能不能真的读到剪贴板（Codex #833）。菜单项没有原生 paste 事件可接，只能走
 * `pasteObjects()` 的异步 `readText`；两类引擎上它走不通，菜单就**不提供**这一项（⌘V 的原生事件照常可用）：
 *   - 根本没有 `readText`（Firefox 默认）；
 *   - WebKit（Safari、macOS / Linux 桌面壳的 WKWebView / WebKitGTK、iOS 上的一切浏览器）：非编辑区的
 *     异步读不放行（`docs/rules/frontend/canvas-objects-and-workspace.md`「剪贴板」）。按 UA 认：
 *     带 AppleWebKit、却不是 Chromium 一族（Chrome / Edge / WebView2 / Android WebView 都自报 `Chrome/`）。
 */
export function canPasteFromMenu(nav: Navigator | undefined = typeof navigator !== 'undefined' ? navigator : undefined): boolean {
  if (typeof nav?.clipboard?.readText !== 'function') return false
  const ua = nav.userAgent ?? ''
  const webkit = /AppleWebKit\//.test(ua) && !/(Chrome|Chromium|Edg)\//.test(ua)
  return !webkit
}

/**
 * 剪贴板里现在有没有本工具的对象——**只在不会弹权限窗时**回答（已授予读权限）；
 * 不知道（没有权限接口 / 还没授予 / 读不了）= null，调用方当作可点。右键弹层的「粘贴为标注」据此置灰。
 */
export async function peekClipboardHasObjects(): Promise<boolean | null> {
  try {
    if (!canPasteFromMenu()) return null
    const perm = await navigator.permissions?.query({ name: 'clipboard-read' as PermissionName })
    if (perm?.state !== 'granted') return null
    return parsePayload(await navigator.clipboard.readText()) !== null
  } catch {
    return null
  }
}

/**
 * 「浏览器根本不给读剪贴板」每会话只提示一次：粘贴键是高频操作，
 * 每按一次弹一条等于噪音，而这个结论一次就够用户知道了。
 * 会话级语义 → 模块级变量，不进文档态。
 */
let readUnsupportedNotified = false

/** ⌘V 入口。返回 true = 消费了这次粘贴（是本工具的对象负载）。 */
export async function pasteObjects(): Promise<boolean> {
  // Firefox 默认不暴露 readText：对象粘贴在这类浏览器上永远走不通，
  // 静默失败会让用户以为是复制没成功，反复重试。
  if (typeof navigator.clipboard?.readText !== 'function') {
    if (!readUnsupportedNotified) {
      readUnsupportedNotified = true
      useUiStore.getState().setStatus(note('clipboardReadUnsupported'), 'error')
    }
    return false
  }
  let text = ''
  try {
    text = await navigator.clipboard.readText()
  } catch (err) {
    // 窗口失焦时浏览器抛的也是 NotAllowedError（「Document is not focused」），
    // 那种多半根本不是冲我们来的粘贴；只有当前确实有焦点才算真的被拒。
    const denied =
      (err as { name?: string } | null)?.name === 'NotAllowedError' && document.hasFocus?.()
    if (denied) {
      useUiStore.getState().setStatus(note('clipboardReadDenied'), 'error')
    }
    return false // 其余情况（失焦、瞬态异常）保持安静，不打扰
  }
  const payload = parsePayload(text)
  if (!payload) return false
  return consumePayload(payload)
}

/**
 * 负载落地：缺素材先走重新链接对话框，否则直接粘贴。
 *
 * ⌘V（原生 paste 事件）、两份右键菜单的「粘贴」（`pasteObjects`）都到这里。单图编辑里也能粘（2026-10-21）：
 * 粘出一张图 = 第二张图，模式订阅切到多图排版；粘标注就留在单图编辑。图内文字编辑器 / 输入框里的粘贴在
 * `handlePasteEvent` 的离散动作闸门那一步就让位给浏览器了，到不了这里。
 */
function consumePayload(payload: ClipPayload): boolean {
  // 上次的排版还在恢复：这次粘贴排队到文档就绪再落（一次）；需要用户先处理恢复问题时拒掉并说一句。都算消费了这次粘贴
  if (deferUserEntry(() => void consumePayload(payload)) !== 'run') return true
  const missingMap = new Map<string, MissingAsset>()
  for (const o of payload.objects) {
    if (o.type !== 'panel') continue
    const p = o as PanelObject
    if (panelSourceKnown(p)) continue
    const cur = missingMap.get(p.fileId)
    if (cur) cur.count += 1
    else {
      missingMap.set(p.fileId, {
        fileId: p.fileId,
        name: p.name ?? p.fileId.split('/').pop() ?? p.fileId,
        count: 1,
      })
    }
  }

  if (missingMap.size) {
    useClipboardStore.getState().setPending({
      mode: 'paste',
      payload,
      missing: [...missingMap.values()],
    })
    return true
  }
  materializePaste(payload, [])
  return true
}

/** 真正落盘：一条历史记录。resolved 为缺失素材的处置结果。 */
export function materializePaste(payload: ClipPayload, resolved: MissingAsset[]): void {
  const { doc, documentId, activeCanvasId, commit } = useDocumentStore.getState()
  const assets = useAssetStore.getState().byId
  // 「原位 +4mm 错开」只对**同一张画布**成立；跨画布粘贴保持原坐标。
  // 旧负载没有 sourceCanvasId（单画布时代），同文档即同画布。
  const sameDoc =
    payload.sourceDocId === documentId &&
    (payload.sourceCanvasId == null || payload.sourceCanvasId === activeCanvasId)
  const relink = new Map(resolved.map((m) => [m.fileId, m.relinkTo]))

  const idMap = new Map<string, string>()
  const groupMap = new Map<string, string>()
  const clones: CanvasObject[] = []
  let skipped = 0

  // 负载可能出自升级前的标签页 / 版本（面板没有 `figureFrame` 记号）：与读档同一道迁移（ADR 0098
  // §三），否则它先按新图幅出图、存盘重开时才补上 figsize 那条——版面在重开时变样
  for (const src of migrateFigureFrames(payload.objects)) {
    if (src.type === 'panel') {
      const missing = relink.has(src.fileId) ? relink.get(src.fileId) : undefined
      if (relink.has(src.fileId) && !missing) {
        skipped += 1
        continue // 用户选择跳过：不生成空面板
      }
    }
    const copy = structuredClone(src)
    const nid = newId(src.type[0])
    idMap.set(src.id, nid)
    copy.id = nid
    if (copy.groupId) {
      const next = groupMap.get(copy.groupId) ?? newId('g')
      groupMap.set(copy.groupId, next)
      copy.groupId = next
    }
    if (copy.type === 'panel') {
      const target = relink.get(copy.fileId)
      if (target) {
        const info = assets[target]
        if (info) {
          clearAdoption(copy)
          copy.fileId = info.id
          copy.fileKind = info.kind
          copy.nativeW = info.native_w_mm
          copy.nativeH = info.native_h_mm
          copy.pxW = info.px_w
          copy.pxH = info.px_h
          copy.script = info.script ?? null
          copy.cost = info.cost
          copy.name = info.name
          // overrides 绑定在原脚本元素上，跨素材不可靠 → 换链接即清空
          copy.overrides = info.baked_overrides ? structuredClone(info.baked_overrides) : []
        }
      }
    }
    if (sameDoc) {
      copy.x += DUPLICATE_OFFSET_MM
      copy.y += DUPLICATE_OFFSET_MM
    } else {
      // 跨文档：保持原坐标，越界的钳回页面内
      copy.x = Math.min(Math.max(copy.x, -copy.w * 0.9), doc.page.w - copy.w * 0.1)
      copy.y = Math.min(Math.max(copy.y, -copy.h * 0.9), doc.page.h - copy.h * 0.1)
    }
    clones.push(copy)
  }

  if (!clones.length) {
    useUiStore.getState().setStatus(note('pasteNothing'), 'error')
    return
  }

  const carriedGroups = payload.layoutGroups
    .filter((g) => groupMap.has(g.id))
    .map((g) => ({
      ...structuredClone(g),
      id: groupMap.get(g.id)!,
      order: g.order.map((oid) => idMap.get(oid)).filter((x): x is string => !!x),
    }))

  commit(hist('pasteObjects', { count: clones.length }), (d) => {
    d.objects.push(...clones)
    if (carriedGroups.length) {
      d.layoutGroups = [...(d.layoutGroups ?? []), ...carriedGroups]
    }
  })
  // 在图内编辑里粘贴：新对象成了选区，回到画布层工作（与点中别的对象同一件事，`ObjectView.onPointerDown`）——
  // 编辑态不退的话属性页仍指着图内元素，看不到刚粘出来的标注
  if (useUiStore.getState().elementPanelId) useUiStore.getState().setElementPanel(null)
  useSelectionStore.getState().set(clones.map((o) => o.id))
  useUiStore
    .getState()
    .setStatus(
      skipped
        ? note('pastedWithSkips', { count: clones.length, skipped, undo: modKey('Z') })
        : note('pasted', { count: clones.length, undo: modKey('Z') }),
      // 全部贴上是成功（✓）；有跳过的是部分结果，只陈述（ⓘ）
      skipped ? 'info' : 'done',
    )
}
