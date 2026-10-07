/**
 * 首次引导：一个非阻塞的两步小任务，只对内置案例出现（§十五/十六）。
 *
 * 纪律：**只观察，不代劳。** 不自动选中、不自动改值、不自动创建 override
 * ——每一步的完成判据都是真实状态：
 *
 *   第 1 步   uiStore.selectedGids 里出现目标 gid（用户亲手点了标题）
 *   第 2 步   panel.overrides 里目标 (gid, prop) 的值达到 targetValue，
 *             且这一版真的画出来了（不 rendering、无 pending、无渲染错误）
 *   完成语    「源码未改动」只在 verifySourceIntegrity 真跑完且结论是
 *             unchanged 时才说；核对中只显示「正在核对源文件」；
 *             unavailable 说「查不了」；changed 时本组件闭嘴——常驻报警
 *             横幅才是那个状态的权威。
 *
 * 完成状态一旦达成就**锁存**：随后的 undo 不把用户拽回第 2 步（他已经
 * 亲手完成过一次，undo 本来就是完成面板上的一个出口）。
 * 关闭/跳过记在父级（EditorView）的会话态里——同一会话不再出现，
 * 不跨会话持久化。浮在画布左下角，不遮画布、不遮右栏、无全屏遮罩。
 */
import { useEffect, useRef, useState } from 'react'
import { Button, IconButton } from '@/components/ui/Button'
import { buttonClass } from '@/components/ui/buttonClass'
import { Check, Download, LoaderCircle, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { runUndoRedo } from '@/hooks/useKeyboard'
import { t as translate } from '@/i18n'
import { RELEASES_LATEST_URL } from '@/lib/brand'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { cn } from '@/lib/utils'
import type { GuidedTaskSpec } from '../examples'
import { pg } from '../pgText'
import type { SourceIntegrity } from '../sourceIntegrity'
import { effectiveOverride } from '@/lib/effectiveOverride'

export function GuidedTask({
  task,
  scriptName,
  panel,
  integrity,
  renderBusy,
  renderFailed,
  onRequestIntegrityRecheck,
  onViewSource,
  onDismiss,
}: {
  task: GuidedTaskSpec
  scriptName: string
  panel: PanelObject
  integrity: SourceIntegrity
  /** 正在重渲染或有改动尚未应用 */
  renderBusy: boolean
  renderFailed: boolean
  onRequestIntegrityRecheck: () => void
  onViewSource: () => void
  onDismiss: () => void
}) {
  const selectedGids = useUiStore((s) => s.selectedGids)
  const targetSelected = selectedGids.includes(task.targetGid)

  // 目标元素身上有任何 override = 用户已经选中并动过它，第 1 步早就过了
  const touchedTarget = panel.overrides.some((o) => o.gid === task.targetGid)
  const override = effectiveOverride(panel.overrides, task.targetGid, task.prop)
  const valueHit =
    override != null && Number(override.value) === Number(task.targetValue)

  // 第 2 步一旦到达就不回退（用户取消选中不该把提示拽回第 1 步）
  const [reachedStep2, setReachedStep2] = useState(false)
  useEffect(() => {
    if (targetSelected || touchedTarget) setReachedStep2(true)
  }, [targetSelected, touchedTarget])

  // 完成 = 值达标 + 这一版真的画出来了。锁存：随后的 undo 不清空完成态。
  const [achieved, setAchieved] = useState(false)
  useEffect(() => {
    if (achieved || !valueHit || renderBusy || renderFailed) return
    setAchieved(true)
  }, [achieved, valueHit, renderBusy, renderFailed])

  // 完成那一刻复核一次源文件——「kinetics.py 一个字也没动」必须是刚核对过的结论
  const recheckSent = useRef(false)
  useEffect(() => {
    if (!achieved || recheckSent.current) return
    recheckSent.current = true
    onRequestIntegrityRecheck()
  }, [achieved, onRequestIntegrityRecheck])

  // 完整性真的失效时让常驻报警横幅说话，引导不抢戏
  if (integrity.verdict === 'changed') return null

  const step: 1 | 2 = reachedStep2 ? 2 : 1

  return (
    <aside
      role="complementary"
      aria-label={pg('taskTitle')}
      data-guided-task={achieved ? 'done' : `step-${step}`}
      className={cn(
        'absolute bottom-3 left-3 z-canvas-chrome w-[272px] max-w-[calc(100%-1.5rem)]',
        'rounded-lg bg-surface p-3 shadow-pop',
        'animate-rise-in',
      )}
    >
      <IconButton
        label={translate('actions.close')}
        iconSize="xs"
        onClick={onDismiss}
        className="absolute right-2 top-2 text-ink-3"
      >
        <X size={ICON_SIZE.sm} />
      </IconButton>

      {achieved ? (
        <div aria-live="polite" className="flex flex-col gap-1.5 pr-5">
          <p className="flex items-center gap-1.5 text-base font-medium text-ink">
            <Check size={ICON_SIZE.md} className="text-sel" aria-hidden />
            {pg('taskDoneTitle')}
          </p>
          {integrity.verdict === 'unchanged' ? (
            <p className="text-xs leading-relaxed text-ink-2">
              {pg('taskDoneBody', { filename: scriptName })}
            </p>
          ) : integrity.verdict === 'checking' ? (
            <p className="flex items-center gap-1.5 text-xs text-ink-3">
              <LoaderCircle size={ICON_SIZE.sm} className="animate-spin" aria-hidden />
              {pg('taskChecking')}
            </p>
          ) : (
            <p className="text-xs leading-relaxed text-ink-2">{pg('taskDoneUnverified')}</p>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Button variant="secondary" size="sm" onClick={() => runUndoRedo(false)}>
              {translate('topbar.undo', { ns: 'workspace' })}
            </Button>
            <Button variant="secondary" size="sm" onClick={onDismiss}>
              {pg('taskContinue')}
            </Button>
            <Button variant="secondary" size="sm" onClick={onViewSource}>
              {pg('taskViewSource')}
            </Button>
            <a href={RELEASES_LATEST_URL} className={buttonClass({ variant: 'secondary', size: 'sm' })}>
              <Download size={ICON_SIZE.xs} aria-hidden />
              {pg('downloadDesktop')}
            </a>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5 pr-5">
          <p className="text-base font-medium text-ink">{pg('taskTitle')}</p>
          <p className="type-section">
            {pg('taskStep', { n: step })}
          </p>
          <p aria-live="polite" className="text-xs leading-relaxed text-ink-2">
            {step === 1 ? pg('taskSelectTitle') : pg('taskEditFontsize')}
          </p>
          <Button size="sm" onClick={onDismiss} className="-ml-2.5 self-start text-ink-3 hover:text-ink">
            {pg('taskSkip')}
          </Button>
        </div>
      )}
    </aside>
  )
}
