/**
 * 拖 / 粘贴文件进画布：每一样东西按类型接住（ADR 0176）。
 *
 * 图片（PNG / JPG / TIFF / PDF / SVG）的路径是「字节上传 → 后端按内容验证并复制进项目文件夹 → 成为素材 → 放到落点」，
 * 复用素材扫描（`assetStore.load`）与 `addPanelToCanvas`（恢复门的 `deferUserEntry` 因此自动生效），不另造对象模型。
 * 桌面壳与浏览器走同一条：`DataTransfer.files` 里有名字与字节（壳的原生拖放处理是关掉的，`disable_drag_drop_handler`）。
 *
 * 任何失败出口只落三种结局（产品负责人 2026-11-10）：静默做完并说一句；一句话 + 一个主按钮直接完成用户原来想做的事；
 * 真做不了就「发送问题反馈」。本模块不出现纯文字报错，也没有「请去某处做某事」。
 */
import { literal, msg, type UiMessage } from '@/i18n'
import { ApiError, backendCodeMsg, backendErrorMsg, importAsset, type ImportedAsset } from '@/lib/api'
import type { CanvasDropPlace } from '@/lib/canvasScriptDrop'
import { currentProjectId } from '@/lib/session'
import { foreignLanguageOf } from '@/lib/foreignScript'
import { isScriptName } from '@/lib/scriptImport'
import { useAssetStore } from '@/store/assetStore'
import { runDiscreteAction } from '@/store/gestureCoordinator'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore, type StatusAction } from '@/store/uiStore'
import { addPanelToCanvas } from '@/store/workspace'

/** 一个文件在画布上该怎么接 */
export type IntakeKind =
  /** 素材扫描认的图片（PNG / JPG / TIFF / PDF / SVG）：上传、成为素材、放上画布 */
  | 'asset'
  | 'script'
  /** R / MATLAB 脚本：打开所在项目并提议「生成可编辑版本」 */
  | 'foreign'
  /** 项目包（`.tavotto` / `.magplot` / zip）：走顶栏「导入项目包」同一条流程 */
  | 'package'
  /** 别的图片格式（WebP / GIF / BMP…）：素材扫描不认，如实说清 */
  | 'otherImage'
  | 'other'

/** 素材扫描认的图片后缀（与后端 `assetimport.EXTENSIONS` / `project_refresh` 同一份，`canvasFileIntake.test.ts` 钉住） */
export const ASSET_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.tif', '.tiff', '.pdf', '.svg'] as const
const ASSET_MIMES = new Set(['image/png', 'image/jpeg', 'image/tiff', 'image/svg+xml', 'application/pdf'])
const PACKAGE_EXTENSIONS = ['.tavotto', '.magplot', '.zip'] as const
const OTHER_IMAGE_EXTENSIONS = ['.webp', '.gif', '.bmp', '.heic', '.heif', '.avif', '.ico', '.jfif']

const extOf = (name: string) => {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot).toLowerCase() : ''
}

export function intakeKindOf(file: { name: string; type?: string }): IntakeKind {
  const ext = extOf(file.name)
  if ((ASSET_EXTENSIONS as readonly string[]).includes(ext)) return 'asset'
  if (isScriptName(file.name)) return 'script'
  if (foreignLanguageOf(file.name)) return 'foreign'
  if ((PACKAGE_EXTENSIONS as readonly string[]).includes(ext)) return 'package'
  if (OTHER_IMAGE_EXTENSIONS.includes(ext)) return 'otherImage'
  // 没有认得的后缀（剪贴板截图、网页拖下来的图）：看媒体类型
  const mime = (file.type ?? '').toLowerCase()
  if (ASSET_MIMES.has(mime)) return 'asset'
  if (mime.startsWith('image/')) return 'otherImage'
  return 'other'
}

export interface IntakePlan {
  assets: File[]
  scripts: File[]
  foreign: File[]
  packages: File[]
  /** 接不住的：别的图片格式 / 数据文件 / 笔记本 / 未知 */
  unsupported: { name: string; image: boolean }[]
}

export function planIntake(files: readonly File[]): IntakePlan {
  const plan: IntakePlan = { assets: [], scripts: [], foreign: [], packages: [], unsupported: [] }
  for (const f of files) {
    switch (intakeKindOf(f)) {
      case 'asset':
        plan.assets.push(f)
        break
      case 'script':
        plan.scripts.push(f)
        break
      case 'foreign':
        plan.foreign.push(f)
        break
      case 'package':
        plan.packages.push(f)
        break
      case 'otherImage':
        plan.unsupported.push({ name: f.name, image: true })
        break
      default:
        plan.unsupported.push({ name: f.name, image: false })
    }
  }
  return plan
}

/* ----------------------------- 说话的两个出口 ----------------------------- */

/**
 * 「发送问题反馈」作为 toast 的主按钮：本机备包的对话框（`diagSendStore`），不发送。
 * 引擎说此入口关着（`capability === null`，用户 / 部署关掉了）时不给按钮——那是用户自己关的，不是我们漏了。
 */
export function feedbackAction(): StatusAction | undefined {
  if (useDiagSendStore.getState().capability === null) return undefined
  return {
    label: msg('settings.diagnostics.send.cardButton', undefined, 'dialogs'),
    run: () => useDiagSendStore.getState().setOpen(true),
  }
}

export const sayDrop = (key: string, values: Record<string, unknown>, tone: 'done' | 'error', action?: StatusAction) =>
  useUiStore.getState().setStatus(msg(`canvasDrop.${key}`, values, 'workspace'), tone, action ? { action } : undefined)

/** 接不住的：一句话 + 「发送问题反馈」 */
export interface IntakeProblem {
  name: string
  /** 后端 / 网络说的原因（已是描述符）；没有 = 类型本身接不住 */
  reason?: UiMessage
  image?: boolean
}

/** 把本批所有「没成」的汇成**一句**说出去（带主按钮）；没有问题什么都不做，返回 false */
export function reportProblems(problems: readonly IntakeProblem[]): boolean {
  if (!problems.length) return false
  const action = feedbackAction()
  if (problems.length > 1) {
    sayDrop('importFailedMany', { count: problems.length }, 'error', action)
    return true
  }
  const [p] = problems
  if (p.reason) sayDrop('importFailed', { name: p.name, reason: p.reason }, 'error', action)
  else sayDrop(p.image ? 'unsupportedImage' : 'unsupportedType', { name: p.name }, 'error', action)
  return true
}

/* ------------------------------ 图片上传并放置 ------------------------------ */

export interface PlaceResult {
  /** 放上画布（或排进恢复门队列）的素材名 */
  placed: string[]
  problems: IntakeProblem[]
  /** 中途换了项目：这一批作废，什么都不说（A 项目的响应绝不落进 B） */
  aborted: boolean
}

const reasonOf = (e: unknown): UiMessage =>
  e instanceof ApiError || (e instanceof Error && e.message) ? backendErrorMsg(e) : literal(String(e))

/**
 * 逐个上传 → 重扫一次素材清单 → 依次放上画布。
 * 第一张放在落点（按页面毫米坐标，仍在发起时的那张画布上才用），其余交给常规摆放（避让已有对象，自然错开）。
 * 落点的画布变了（上传期间切了标签）就不带点：按旧画布的页面算的坐标放到别的画布上会落在页外。
 */
export async function importAndPlace(
  files: readonly File[],
  opts: { place?: CanvasDropPlace | null; nameOf?: (f: File, i: number) => string } = {},
): Promise<PlaceResult> {
  const pj = currentProjectId()
  const problems: IntakeProblem[] = []
  // 没有项目就没有项目文件夹可放（浏览器 playground / MCP 画布里没有这个后端）：每个文件各说一句原因，不白发请求
  if (pj === null)
    return { placed: [], problems: files.map((f) => ({ name: f.name, reason: msg('canvasDrop.noProjectFolder', undefined, 'workspace') })), aborted: false }
  const imported: { file: File; asset: ImportedAsset }[] = []
  for (const [i, file] of files.entries()) {
    const name = opts.nameOf?.(file, i) ?? file.name
    try {
      imported.push({ file, asset: await importAsset(file, name, pj) })
    } catch (e) {
      if (currentProjectId() !== pj) return { placed: [], problems: [], aborted: true }
      problems.push({ name: file.name, reason: reasonOf(e) })
    }
    if (currentProjectId() !== pj) return { placed: [], problems: [], aborted: true }
  }
  if (!imported.length) return { placed: [], problems, aborted: false }

  // `force`：在途的那份清单可能在这次导入之前就读完了目录
  await useAssetStore.getState().load({ force: true })
  if (currentProjectId() !== pj) return { placed: [], problems: [], aborted: true }

  const sameCanvas = opts.place ? useDocumentStore.getState().activeCanvasId === opts.place.canvasId : false
  const at = sameCanvas ? opts.place?.at : undefined
  const placed: string[] = []
  // 上传 / 重扫是异步的，回来时用户可能正在别的手势里：放置是离散动作，先收手势再放（`runDiscreteAction`，
  // 不看焦点的 `app` 档），否则 `commit` 会并进那次事务——取消拖动连图一起回滚、一次撤销撤掉两件事
  runDiscreteAction('app', null, () => {
    for (const [i, { file, asset }] of imported.entries()) {
      const info = useAssetStore.getState().byId[asset.id]
      if (!info) {
        // 后端落盘后核过它在素材清单里；这里仍对不上（清单取失败 / 被别处删了）：如实算没成，别静默
        problems.push({ name: file.name, reason: backendCodeMsg('asset_import_not_visible', {}, '') })
        continue
      }
      addPanelToCanvas(info, i === 0 ? at?.x : undefined, i === 0 ? at?.y : undefined)
      placed.push(asset.name)
    }
  })
  return { placed, problems, aborted: false }
}

/** 放成了的那一句（只在没有问题时说；问题带主按钮，说问题） */
export function reportPlaced(placed: readonly string[]): void {
  if (placed.length === 1) sayDrop('placedOne', { name: placed[0] }, 'done')
  else if (placed.length > 1) sayDrop('placedMany', { count: placed.length }, 'done')
}

/** 一批图的结局统一说一次：有问题说问题（带按钮），否则说放进了几张 */
export function reportImageResult(res: PlaceResult, extra: readonly IntakeProblem[] = []): void {
  if (res.aborted) return
  if (reportProblems([...res.problems, ...extra])) return
  reportPlaced(res.placed)
}

/* ------------------------------- 剪贴板图片 ------------------------------- */

const pad = (n: number) => String(n).padStart(2, '0')

/** 剪贴板里图片的文件名：`pasted-20261110-213455.png`（扩展名按媒体类型，认不出就 .png） */
export function pastedImageName(file: { name: string; type?: string }, now: Date = new Date(), index = 0): string {
  // 真有名字的（从文件管理器复制的文件）沿用；截图工具给的 `image.png` / 空名字是占位，自己起
  if (file.name && !/^image(\.\w+)?$/i.test(file.name)) return file.name
  const ext = { 'image/jpeg': '.jpg', 'image/tiff': '.tif', 'image/svg+xml': '.svg', 'application/pdf': '.pdf' }[
    (file.type ?? '').toLowerCase() as string
  ] ?? '.png'
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  return `pasted-${stamp}${index ? `-${index + 1}` : ''}${ext}`
}

/** 剪贴板里**能放上画布的**文件（图片 / PDF / SVG）；别的文件、文字不归这里 */
export function pastedAssetFiles(data: Pick<DataTransfer, 'files' | 'items'> | null | undefined): File[] {
  if (!data) return []
  const out: File[] = []
  for (const f of Array.from(data.files ?? [])) if (intakeKindOf(f) === 'asset') out.push(f)
  if (out.length) return out
  // 个别宿主只在 items 里给（`getAsFile`）
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind !== 'file') continue
    const f = item.getAsFile()
    if (f && intakeKindOf(f) === 'asset') out.push(f)
  }
  return out
}

/** 粘贴：同一条导入路径，落点交给常规摆放（对象粘贴也没有鼠标落点：原位错开 / 避让） */
export async function intakePastedImages(files: readonly File[]): Promise<void> {
  const now = new Date()
  const res = await importAndPlace(files, { nameOf: (f, i) => pastedImageName(f, now, i) })
  reportImageResult(res)
}
