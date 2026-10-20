import { msg } from '@/i18n'
import { stemOf } from '@/lib/openRequest'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { useDepRepairStore } from '@/store/depRepairStore'
import { useDocumentStore } from '@/store/documentStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
import { enterEdit } from '@/store/prepEnterEdit'
import { pngAdmitOwner, registerPngRebuildRunner } from '@/store/pngAdmissionMemory'
import { useEnvStore } from '@/store/envStore'
import { isGatePhase, useScriptRunStore, whenScriptIdle } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'

/** 门框关掉之后等多久还没人重跑，就当用户取消了（作答到 `rerunGated` 之间隔着几个 await） */
export const GATE_CANCEL_GRACE_MS = 1500

export type RebuildOutcome = 'rebuilt' | 'picker' | 'not_entered' | 'failed' | 'stale'

/**
 * 「重建为可编辑图」（ADR 0139 PR1）：PNG 进不了编辑时，用它自己的脚本重画出一张**新的**可编辑图。
 *
 * 复用现有链路，不另起一套：`ScriptRunStore.run`（用户显式触发的试运行，同脚本防并发由它保证）→
 * 捕获的运行期素材描述符 → `prepEnterEdit.enterEdit`（去重、恢复期排队、加到画布并进入图内编辑，
 * 新面板放在旁边，不覆盖任何已有面板）。
 *
 * 原 PNG 面板**一个字节都不动**（overrides / guard / 位置 / 像素）：这里不 commit 它、不调
 * `rebuildPanel`（带 guard 会被拒；无 guard 会触发 #1145 要杜绝的无 policy 渲染）。
 * 多 Figure 时不默认取第一张：同脚本 + 同 stem 找不到**唯一**一张，就交给现有 Figure 选择器。
 */
export async function rebuildEditableFromScript(panelId: string): Promise<RebuildOutcome> {
  const ui = useUiStore.getState()
  const owner = pngAdmitOwner(panelId)
  const panel = findPanel(panelId)
  const script = panel?.script
  if (!panel || !script) return 'stale'
  const stem = stemOf(panel.fileId)
  const guard = captureProjectEpoch(() => useScriptRunStore.getState().epoch)
  // 发起时的排版身份：同项目里打开了另一份已保存的排版（documentId / loadSeq 变）或切了画布，
  // 捕获的面板就会落进错误的排版——整个丢弃，什么都不写
  const origin = documentOrigin(panelId)
  const abandon = (): RebuildOutcome => {
    useUiStore.getState().clearStatusOwnedBy(owner)
    return 'stale'
  }
  ui.setStatus(msg('artifact.rebuilding', undefined, 'errors'), 'progress', { owner })

  await useScriptRunStore.getState().run(script)
  await whenScriptIdle(script) // 同脚本已在跑时 run 是 no-op：等那一次落地再读结果
  if (!guard.still() || !origin.still()) return abandon()

  // 停在起会话之前的门上：弹框（作答后由作答方 `rerunGated` 重跑）。门后只续跑这一次——等那次重跑落地再读结果；
  // 用户取消则结束且不提示
  if (isGatePhase(useScriptRunStore.getState().byScript[script]?.phase)) {
    if (!(await whenGateAnswered(script, () => guard.still() && origin.still()))) {
      return guard.still() && origin.still() ? gateDeclined(owner) : abandon()
    }
    await whenScriptIdle(script)
    if (!guard.still() || !origin.still()) return abandon()
  }

  const row = useScriptRunStore.getState().byScript[script]
  const phase = row?.phase
  if (phase !== 'captured_one' && phase !== 'captured_many') {
    // 起会话之前的门（运行目录 / 依赖准备）已经弹了自己的框，取消是用户自己的决定：都不再补一句
    if (phase === 'needs_workdir' || phase === 'needs_preparation' || phase === 'cancelled') {
      useUiStore.getState().clearStatusOwnedBy(owner)
      return 'failed'
    }
    useUiStore.getState().setStatus(msg('artifact.rebuildFailed', undefined, 'errors'), 'error', { owner })
    return 'failed'
  }
  const matches = row.descriptors.filter((d) => d.script === script && d.stem === stem)
  if (matches.length !== 1) {
    useUiStore.getState().clearStatusOwnedBy(owner)
    useFigurePickerStore.getState().open(script)
    return 'picker'
  }
  if (!origin.still()) return abandon()
  const outcome = await enterEdit(
    { epoch: () => useScriptRunStore.getState().epoch, note: () => {}, stillWanted: () => origin.still() },
    matches[0],
  )
  if (outcome === 'entered') {
    useUiStore.getState().setStatus(msg('artifact.rebuilt', undefined, 'errors'), 'done')
    return 'rebuilt'
  }
  useUiStore.getState().clearStatusOwnedBy(owner)
  return outcome === 'stale' ? 'stale' : 'not_entered'
}

function gateDeclined(owner: string): RebuildOutcome {
  useUiStore.getState().clearStatusOwnedBy(owner)
  return 'failed'
}

/** 发起时的排版：文档身份 + 载入代次 + 激活画布，源面板还在 */
function documentOrigin(panelId: string): { still: () => boolean } {
  const at = () => {
    const s = useDocumentStore.getState()
    return `${s.documentId}|${s.loadSeq}|${s.activeCanvasId}`
  }
  const was = at()
  return { still: () => at() === was && findPanel(panelId) !== null }
}

/**
 * 等门被回答：行离开门相位（作答方已 `rerunGated`，或行被收起）= true；
 * 门框都关了、行还停着、宽限期内也没人重跑 = 用户取消，false；`alive` 失效也是 false。
 */
function whenGateAnswered(script: string, alive: () => boolean): Promise<boolean> {
  return new Promise((resolve) => {
    let timer: ReturnType<typeof setTimeout> | undefined
    const unsubs: Array<() => void> = []
    const finish = (v: boolean) => {
      clearTimeout(timer)
      unsubs.forEach((u) => u())
      resolve(v)
    }
    const check = () => {
      if (!alive()) return finish(false)
      if (!isGatePhase(useScriptRunStore.getState().byScript[script]?.phase)) return finish(true)
      const env = useEnvStore.getState()
      const open = !!env.workdirConfirmation || !!env.dependencyPreparation || useDepRepairStore.getState().busy
      clearTimeout(timer)
      if (!open) timer = setTimeout(() => finish(false), GATE_CANCEL_GRACE_MS)
    }
    unsubs.push(useScriptRunStore.subscribe(check), useEnvStore.subscribe(check), useDepRepairStore.subscribe(check))
    check()
  })
}

function findPanel(panelId: string): PanelObject | null {
  const s = useDocumentStore.getState()
  for (const objects of [s.doc.objects, ...s.canvases.map((c) => c.objects)]) {
    const hit = objects.find((o) => o.id === panelId)
    if (hit?.type === 'panel') return hit
  }
  return null
}

registerPngRebuildRunner(rebuildEditableFromScript)
