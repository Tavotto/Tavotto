/**
 * 编辑器里把 `.py` 从系统拖到画布上 = 「打开并运行」它，图放进**这张**画布、落在放下的位置（2026-10-27 用户拍板）。
 * 运行要 AI 修复时，修好后的验证重跑沿用同一个意图（`PrepEntry.placeIn`），照样放回这张画布。
 *
 * * 路径：macOS 壳旁听系统拖放交来真实路径（ADR 0092），与画布的 DOM `drop` 配对（`createCanvasDropPairer`）；
 *   拿不到路径的宿主（其它平台的桌面壳）退回系统选择器，说清是哪个文件；浏览器里说清怎么办（左栏脚本库运行），不猜。
 * * 只运行**当前项目里**的脚本：项目外的不切项目、不运行，说一句回主页拖入。
 * * 教程层盖着、或正在切项目时不收。
 */
import { useEffect, useRef } from 'react'
import { msg } from '@/i18n'
import { createCanvasDropPairer, projectRelativeScript, type CanvasDropPlace } from '@/lib/canvasScriptDrop'
import { isDesktop, nativeFileDropAvailable, onNativeFileDrop, pickScriptFile, type NativeFileDrop } from '@/lib/desktop'
import { baseName, dragHasFiles, dropTargetOf, type DropData } from '@/lib/scriptImport'
import { useOnboardingStore } from '@/store/onboardingStore'
import { scriptTarget, useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'

/** 画布的 drop 到了、壳的事件还没到：最多等这么久（与主页同一个量级；IPC 通常几毫秒） */
const PAIR_GRACE_MS = 1500

const say = (key: string, values: Record<string, unknown>, tone: 'done' | 'error') =>
  useUiStore.getState().setStatus(msg(`canvasDrop.${key}`, values, 'workspace'), tone)

/** 这次拖动画布收不收：只收系统文件（素材卡的内部拖动另有 `application/x-panel-id`），教程层 / 切项目时不收 */
export function acceptsScriptDrag(types: readonly string[]): boolean {
  if (types.includes('application/x-panel-id') || !dragHasFiles(types)) return false
  return useOnboardingStore.getState().surface === 'none' && !useProjectStore.getState().switching
}

/** 拿到了真实路径：在项目里就「打开并运行」并记下要放进的画布，否则说清为什么没运行 */
export function runDroppedScript(path: string, place: CanvasDropPlace): void {
  const name = baseName(path)
  const root = useProjectStore.getState().project?.figures_dir
  const rel = root ? projectRelativeScript(root, path) : null
  if (!rel) {
    say('outsideProject', { name }, 'error')
    return
  }
  // 与主页同一道闸：首尾带空白的文件名后端各脚本端点会 strip，可能找不到、或跑错同目录的另一个脚本（#1278 之前不运行）
  if (rel.split('/').some((seg) => seg !== seg.trim())) {
    say('cannotRun', { name }, 'error')
    return
  }
  void useProjectPreparationStore.getState().open(scriptTarget(rel, { reuse: true }), { run: true, placeIn: place })
  say('running', { name }, 'done')
}

function nativePath(drop: NativeFileDrop): string | null {
  return drop.kind === 'script' ? drop.script : null
}

export function useCanvasScriptDrop() {
  const native = useRef<'pending' | boolean>('pending')
  const pairer = useRef<ReturnType<typeof createCanvasDropPairer<NativeFileDrop>> | null>(null)
  if (!pairer.current)
    pairer.current = createCanvasDropPairer<NativeFileDrop>({
      graceMs: PAIR_GRACE_MS,
      run: (drop, place) => {
        const path = nativePath(drop)
        if (path) runDroppedScript(path, place)
        else say('notScript', { name: drop.name }, 'error')
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

  /** 画布的 DOM drop：落点与画布在这一刻定；路径等壳（能给的话），等不到就降级 */
  return (dt: DropData, place: CanvasDropPlace) => {
    const target = dropTargetOf(dt)
    const degrade = () => {
      if (useProjectStore.getState().switching) return
      switch (target.kind) {
        case 'path':
          if (target.script) runDroppedScript(`${target.folder.replace(/[\\/]+$/, '')}/${target.script}`, place)
          else say('notScript', { name: baseName(target.folder) }, 'error')
          return
        case 'no-path':
          if (!isDesktop()) {
            say('browser', { name: target.name }, 'error')
            return
          }
          say('noPath', { name: target.name }, 'done')
          void pickScriptFile().then((path) => {
            if (path) runDroppedScript(path, place)
          })
          return
        case 'foreign-path':
          say('notScript', { name: target.script }, 'error')
          return
        case 'foreign-no-path':
        case 'not-script':
          say('notScript', { name: target.name }, 'error')
          return
        case 'none':
          return
      }
    }
    if (target.kind === 'none') return
    if (native.current === false || target.kind === 'path') degrade()
    else pairer.current!.dom(place, degrade)
  }
}
