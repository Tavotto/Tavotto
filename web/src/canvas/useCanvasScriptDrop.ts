/**
 * 系统文件拖到画布上：每一样东西按类型接住（ADR 0176；其中 `.py` 的那条是 2026-10-27 用户拍板的「打开并运行」）。
 *
 * * 图片（PNG / JPG / TIFF / PDF / SVG）：上传到项目文件夹成为素材，放在落点（`lib/canvasFileIntake`）。
 * * `.py`：运行它，图放进**这张**画布、落在放下的位置；运行要 AI 修复时，修好后的验证重跑沿用同一个意图
 *   （`PrepEntry.placeIn`）。路径：macOS / Windows 壳旁听系统拖放交来真实路径（ADR 0092），与画布的 DOM `drop` 配对
 *   （`createCanvasDropPairer`）；拿不到路径的宿主退回系统选择器，说清是哪个文件。
 * * 项目外的 `.py` / R / MATLAB 脚本：一句话 + 主按钮「打开它所在的项目」（与主页同一条打开并运行的路径）。
 * * Jupyter 笔记本（`.ipynb`，ADR 0179）：在项目里就抽成它旁边的同名 `.py` 并运行（图放进这张画布、落在放下的位置）；项目外的
 *   同样给「打开它所在的项目」，打开后接着抽取并运行；拿不到路径（浏览器 / 壳没交来）就把内容交给后端、抽成当前项目根里的 `.py`。
 * * R / MATLAB 脚本在项目里：出「生成可编辑版本」那张卡（`foreignScriptStore`），不运行。
 * * 项目包：走顶栏「导入项目包」同一条流程（`lib/packageImport`）。
 * * 别的（数据、笔记本、别的图片格式…）：一句话说清能放什么 + 「发送问题反馈」。
 * * 一次放下多个文件：每个按类型处理，**最后一句**汇总，不静默丢；出问题的那句带主按钮、有最后发言权。
 * * 教程层盖着、或正在切项目时不收。
 */
import { useEffect, useRef } from 'react'
import { msg } from '@/i18n'
import { backendErrorMsg } from '@/lib/api'
import {
  feedbackAction,
  importAndPlace,
  planIntake,
  reportImageResult,
  reportProblems,
  sayDrop,
  type IntakePlan,
  type IntakeProblem,
} from '@/lib/canvasFileIntake'
import { createCanvasDropPairer, projectRelativePath, projectRelativeScript, type CanvasDropPlace } from '@/lib/canvasScriptDrop'
import { isDesktop, nativeFileDropAvailable, onNativeFileDrop, pickScriptFile, type NativeFileDrop } from '@/lib/desktop'
import { foreignLanguageOf, type ForeignLanguage } from '@/lib/foreignScript'
import { runNotebook } from '@/lib/notebookRun'
import { openPackageFile } from '@/lib/packageImport'
import { baseName, dragHasFiles, dropTargetOf, parentDir, pathFromFileUri, type DropData } from '@/lib/scriptImport'
import { useForeignScriptStore } from '@/store/foreignScriptStore'
import { useOnboardingStore } from '@/store/onboardingStore'
import { scriptTarget, useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useProjectStore } from '@/store/projectStore'

/** 画布的 drop 到了、壳的事件还没到：最多等这么久（与主页同一个量级；IPC 通常几毫秒） */
const PAIR_GRACE_MS = 1500

const say = sayDrop

/**
 * 「脚本那一句」说完之后才轮到本批里没成的东西说话：带主按钮的问题要有最后发言权，
 * 而脚本那一句是异步的（要等壳交来路径，最多 `PAIR_GRACE_MS`），先说会被它盖掉。
 */
let tail: (() => void) | null = null
function flushTail(): void {
  const t = tail
  tail = null
  t?.()
}

/** 这次拖动画布收不收：只收系统文件（素材卡的内部拖动另有 `application/x-panel-id`），教程层 / 切项目时不收 */
export function acceptsScriptDrag(types: readonly string[]): boolean {
  if (types.includes('application/x-panel-id') || !dragHasFiles(types)) return false
  return useOnboardingStore.getState().surface === 'none' && !useProjectStore.getState().switching
}

/**
 * 打开 `path` 所在的项目，再接着做用户原本想做的事：`.py` → 「打开并运行」（与主页同一条），
 * R / MATLAB → 出「生成可编辑版本」卡。打不开就说一句 + 「发送问题反馈」。
 */
export async function openItsProject(path: string, language: ForeignLanguage | 'notebook' | null): Promise<void> {
  const name = baseName(path)
  try {
    // 带着这个脚本进来：素材区只看脚本的图（ADR 0184 §三）；名字带首尾空白的不进范围（后端同样拒）
    const store = useProjectStore.getState()
    await (name === name.trim()
      ? store.open(parentDir(path), false, { entry: 'script', script: name })
      : store.open(parentDir(path)))
  } catch {
    say('openProjectFailed', { name }, 'error', feedbackAction())
    return
  }
  // 笔记本：打开项目后接着抽成同名 .py 并运行（它就在刚打开的这个文件夹里）
  if (language === 'notebook') {
    await runNotebook({ path: name })
    return
  }
  if (language) {
    useForeignScriptStore.getState().setOffer({ script: name, language })
    return
  }
  // 与主页同一道闸：首尾带空白的文件名后端各脚本端点会 strip，可能找不到、或跑错同目录的另一个脚本
  if (name !== name.trim()) {
    say('cannotRun', { name }, 'error', feedbackAction())
    return
  }
  void useProjectPreparationStore.getState().open(scriptTarget(name, { reuse: true }), { run: true })
  say('openedAndRunning', { name }, 'done')
}

const openItsProjectAction = (path: string, language: ForeignLanguage | 'notebook' | null) => ({
  label: msg('canvasDrop.openItsProject', undefined, 'workspace'),
  run: () => void openItsProject(path, language),
})

/** 拿到了真实路径：在项目里就「打开并运行」并记下要放进的画布，不在项目里就给「打开它所在的项目」 */
export function runDroppedScript(path: string, place: CanvasDropPlace, opts?: { skipped?: number }): void {
  const name = baseName(path)
  const root = useProjectStore.getState().project?.figures_dir
  const rel = root ? projectRelativeScript(root, path) : null
  if (!rel) {
    say('outsideProject', { name }, 'error', openItsProjectAction(path, null))
    flushTail()
    return
  }
  // 与主页同一道闸：首尾带空白的文件名后端各脚本端点会 strip，可能找不到、或跑错同目录的另一个脚本（#1278 之前不运行）
  if (rel.split('/').some((seg) => seg !== seg.trim())) {
    say('cannotRun', { name }, 'error', feedbackAction())
    flushTail()
    return
  }
  void useProjectPreparationStore.getState().open(scriptTarget(rel, { reuse: true }), { run: true, placeIn: place })
  const skipped = opts?.skipped ?? 0
  if (skipped > 0) say('runningFirst', { name, count: skipped }, 'done')
  else say('running', { name }, 'done')
  flushTail()
}

/**
 * 拿到了真实路径的笔记本：在项目里就抽成它旁边的同名 `.py` 并运行（图放进这张画布、落在放下的位置），
 * 不在项目里给「打开它所在的项目」。「那一句」是异步的（要等后端抽完），没成的东西排在它后面说（`flushTail`）。
 */
export function runDroppedNotebook(path: string, place: CanvasDropPlace, opts?: { skipped?: number }): void {
  const name = baseName(path)
  const root = useProjectStore.getState().project?.figures_dir
  const rel = root ? projectRelativePath(root, path) : null
  if (!rel) {
    say('outsideProject', { name }, 'error', openItsProjectAction(path, 'notebook'))
    flushTail()
    return
  }
  void runNotebook({ path: rel }, { place, skipped: opts?.skipped }).finally(flushTail)
}

/** R / MATLAB 脚本：在项目里就出「生成可编辑版本」那张卡（不运行），不在项目里给「打开它所在的项目」 */
export function offerDroppedForeign(path: string, language: ForeignLanguage): void {
  const name = baseName(path)
  const root = useProjectStore.getState().project?.figures_dir
  const rel = root ? projectRelativePath(root, path) : null
  if (rel && rel === name) useForeignScriptStore.getState().setOffer({ script: rel, language })
  else say('outsideForeign', { name }, 'error', openItsProjectAction(path, language))
  flushTail()
}

function nativePath(drop: NativeFileDrop): string | null {
  return drop.kind === 'script' ? drop.script : null
}

/** 只含 `file` 的那一份 `DataTransfer` 视图（`dropTargetOf` 一次只看一个文件：多放的不猜用户要哪个） */
function onlyFile(dt: DropData, file: File): DropData {
  return {
    types: dt.types,
    getData: (format) => {
      if (format !== 'text/uri-list') return dt.getData(format)
      return dt
        .getData(format)
        .split(/\r?\n/)
        .filter((l) => {
          const p = pathFromFileUri(l)
          return p !== null && baseName(p) === file.name
        })
        .join('\n')
    },
    files: [file],
  }
}

/** 一次放下里被忽略的脚本数：只处理第一个脚本，其余的（含没有 .py 时多出的笔记本、R / MATLAB）要说一声 */
function skippedScripts(plan: IntakePlan): number {
  const all = plan.scripts.length + plan.notebooks.length + plan.foreign.length
  return all > 0 ? all - 1 : 0
}

export function useCanvasScriptDrop() {
  const native = useRef<'pending' | boolean>('pending')
  /** 本次放下里被忽略的脚本数（壳的事件异步到达时用，与配对的那次放下同一批） */
  const skipped = useRef(0)
  const pairer = useRef<ReturnType<typeof createCanvasDropPairer<NativeFileDrop>> | null>(null)
  if (!pairer.current)
    pairer.current = createCanvasDropPairer<NativeFileDrop>({
      graceMs: PAIR_GRACE_MS,
      run: (drop, place) => {
        const path = nativePath(drop)
        if (path) runDroppedScript(path, place, { skipped: skipped.current })
        else if (drop.kind === 'notebook') runDroppedNotebook(drop.notebook, place, { skipped: skipped.current })
        else if (drop.kind === 'foreign_script') {
          const language = foreignLanguageOf(drop.name)
          if (language) offerDroppedForeign(drop.script, language)
          else flushTail()
        } else flushTail()
      },
    })

  useEffect(() => {
    let off: (() => void) | undefined
    let disposed = false
    const listening = onNativeFileDrop((drop) => pairer.current!.native(drop)).then(
      (u) => {
        if (disposed) u()
        else off = u
        return true
      },
      () => false,
    )
    void Promise.all([nativeFileDropAvailable(), listening]).then(([ok, listens]) => {
      if (!disposed) native.current = ok && listens
    })
    return () => {
      disposed = true
      off?.()
      pairer.current?.dispose()
    }
  }, [])

  /** 脚本那一条：落点与画布在这一刻定；路径等壳（能给的话），等不到就降级 */
  const dropScript = (dt: DropData, place: CanvasDropPlace, skip: number) => {
    const target = dropTargetOf(dt)
    skipped.current = skip
    const degrade = () => {
      if (useProjectStore.getState().switching) {
        flushTail()
        return
      }
      switch (target.kind) {
        case 'path':
          if (target.script) runDroppedScript(`${target.folder.replace(/[\\/]+$/, '')}/${target.script}`, place, { skipped: skip })
          else {
            say('unsupportedType', { name: baseName(target.folder) }, 'error', feedbackAction())
            flushTail()
          }
          return
        case 'foreign-path':
          offerDroppedForeign(`${target.folder.replace(/[\\/]+$/, '')}/${target.script}`, target.language)
          return
        case 'notebook-path':
          runDroppedNotebook(`${target.folder.replace(/[\\/]+$/, '')}/${target.notebook}`, place, { skipped: skip })
          return
        case 'notebook-no-path': {
          // 拿不到路径（浏览器、壳这次没交来）：页面读得到内容，交给后端抽成当前项目根里的 .py——两种宿主同一条，不弹选择器
          const file = Array.from(dt.files).find((f) => f.name === target.name) as File | undefined
          if (!file) {
            flushTail()
            return
          }
          void runNotebook({ file }, { place, skipped: skip }).finally(flushTail)
          return
        }
        case 'no-path':
        case 'foreign-no-path':
          if (!isDesktop()) {
            say('browser', { name: target.name }, 'error', feedbackAction())
            flushTail()
            return
          }
          say('noPath', { name: target.name }, 'done')
          void pickScriptFile().then((path) => {
            if (!path) return flushTail()
            const language = foreignLanguageOf(path)
            if (language) offerDroppedForeign(path, language)
            else runDroppedScript(path, place, { skipped: skip })
          })
          return
        case 'not-script':
        case 'none':
          flushTail()
          return
      }
    }
    if (target.kind === 'none') {
      flushTail()
      return
    }
    if (native.current === false || target.kind === 'path' || target.kind === 'foreign-path' || target.kind === 'notebook-path')
      degrade()
    else pairer.current!.dom(place, degrade)
  }

  /** 项目包：走顶栏「导入项目包」同一条流程；同批的别的文件这次不处理，说一声 */
  const takePackage = async (file: File, others: number) => {
    try {
      await openPackageFile(file)
    } catch (e) {
      say('packageFailed', { name: file.name, reason: backendErrorMsg(e) }, 'error', feedbackAction())
      return
    }
    if (others > 0) say('packageOnly', { name: file.name, count: others }, 'done')
  }

  /** 画布的 DOM drop */
  return (dt: DropData, place: CanvasDropPlace) => {
    if (useProjectStore.getState().switching) return
    const files = Array.from(dt.files) as File[]
    // 没有文件（只有 file:// 链接）：按路径走脚本那一条（与以前一样）
    if (files.length === 0) {
      dropScript(dt, place, 0)
      return
    }
    const plan = planIntake(files)
    if (plan.packages.length > 0) {
      void takePackage(plan.packages[0], files.length - 1)
      return
    }
    const rest: IntakeProblem[] = plan.unsupported.map((u) => ({ name: u.name, image: u.image }))
    // 优先级与壳的 `drop_paths::classify` 一致：.py 赢笔记本，笔记本赢 R / MATLAB
    const script = plan.scripts[0] ?? plan.notebooks[0] ?? plan.foreign[0]
    const skip = skippedScripts(plan)
    // 脚本那一半（DOM 侧的配对登记、uri-list 的读取）必须在 drop 事件里**同步**做完：壳的原生事件只等 `PAIR_GRACE_MS`，
    // 宿主的 `DataTransfer` 在事件返回后也读不到；图片上传在后面异步跑，两者互不等待
    const images = plan.assets.length > 0 ? importAndPlace(plan.assets, { place }) : Promise.resolve(null)
    if (!script) {
      void images.then((res) => {
        if (res?.aborted) return
        if (res) reportImageResult(res, rest)
        else reportProblems(rest)
      })
      return
    }
    // 有脚本：脚本那一句先说，没成的东西排在它后面（带按钮、有最后发言权）
    tail = () =>
      void images.then((res) => {
        if (res?.aborted) return
        reportProblems([...(res?.problems ?? []), ...rest])
      })
    setTimeout(flushTail, PAIR_GRACE_MS + 500)
    dropScript(onlyFile(dt, script), place, skip)
  }
}
