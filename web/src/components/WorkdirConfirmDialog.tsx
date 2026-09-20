import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { WorkdirMode } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useEnvStore } from '@/store/envStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Radio } from './ui/Radio'

/**
 * 首开的那一次确认（U03，ADR 0057 §三）：后端起第一个 worker 之前按脚本的静态证据判出
 * 「数据只有项目根找得到」或「脚本目录与项目根各有一份同名数据、内容不同」，渲染以
 * `workdir_confirmation_required` 回来——这不是错误，是缺一个决定。
 *
 * 三档一次选：项目根 / 脚本目录 / 继续沙盒。每档列出**该目录下找得到的文件**（后端只按
 * 字面量查存在性，不猜、不搜同名）；推荐项只在证据唯一指向项目根时预选，歧义时**不预选**
 * ——机器不裁决。选定 = 记住（项目级）+ 真实 cwd 写入许可（沙盒除外）+ 重排失败的面板；
 * 「稍后」只关框，错误块里还能再打开。机制在后端（`workdir.decision_for`），这里只翻译。
 */
//: 三档的文案键**写成字面量**：i18n 的死键门禁按「源码里出现过这个串」判活，
//: 模板拼出来的键它看不见，删了文案也不会红。
const OPTION_LABEL: Record<WorkdirMode, string> = {
  project_root: 'engine.workdirOption_project_root',
  project: 'engine.workdirOption_project',
  sandbox: 'engine.workdirOption_sandbox',
}
const OPTION_HINT: Record<WorkdirMode, string> = {
  project_root: 'engine.workdirOptionHint_project_root',
  project: 'engine.workdirOptionHint_project',
  sandbox: 'engine.workdirOptionHint_sandbox',
}

export function WorkdirConfirmDialog() {
  const { t } = useTranslation('errors')
  const payload = useEnvStore((s) => s.workdirConfirmation)
  const dismiss = useEnvStore((s) => s.dismissWorkdirConfirmation)
  const setWorkdirMode = useEnvStore((s) => s.setWorkdirMode)
  const [choice, setChoice] = useState<WorkdirMode | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    // 每一份新载荷从它自己的推荐项起步：歧义时 null（不预选）
    setChoice(payload?.recommended ?? null)
    setError(null)
  }, [payload])
  if (!payload) return null
  const en = (key: string, values?: Record<string, unknown>) => t(key, values)
  const confirm = async () => {
    if (!choice) return
    setBusy(true)
    const err = await setWorkdirMode(choice, { confirmed: true })
    setBusy(false)
    if (err) setError(err)
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v && !busy) dismiss()
      }}
      title={en('engine.workdirChooseTitle')}
      description={
        payload.reason === 'ambiguous_data'
          ? en('engine.workdirChooseAmbiguous', { script: payload.script })
          : en('engine.workdirChooseRootEvidence', { script: payload.script })
      }
      size="sm"
      busy={busy}
      anchor="workdir-confirm"
      footer={
        <>
          <Button variant="secondary" size="md" disabled={busy} onClick={dismiss}>
            {en('engine.workdirChooseLater')}
          </Button>
          <Button variant="primary" size="md" disabled={busy || !choice} onClick={() => void confirm()}>
            {en('engine.workdirChooseRun')}
          </Button>
        </>
      }
    >
      <fieldset className="flex flex-col gap-1" data-workdir-choice>
        <legend className="sr-only">{en('engine.workdirChooseTitle')}</legend>
        {payload.options.map((opt) => {
          const selected = choice === opt.mode
          return (
            <label
              key={opt.mode}
              className={cn(
                'flex cursor-pointer items-start gap-2 rounded-sm px-2 py-1.5',
                selected ? 'bg-selected' : 'hover:bg-surface-hover',
              )}
              data-workdir-option={opt.mode}
            >
              <Radio
                name="workdir-choice"
                className="mt-0.5"
                checked={selected}
                disabled={busy}
                onChange={() => setChoice(opt.mode)}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-ink">
                  {en(OPTION_LABEL[opt.mode])}
                  {opt.recommended && (
                    <span className="ml-1.5 text-xs text-ink-3">{en('engine.workdirRecommended')}</span>
                  )}
                </span>
                <span className="mt-0.5 block text-xs leading-relaxed text-ink-3">
                  {en(OPTION_HINT[opt.mode])}
                </span>
                {/* 找得到的文件是用户自己的数据名，不翻译 */}
                <span className="mt-0.5 block font-mono text-xs text-ink-2">
                  {opt.found.length
                    ? en('engine.workdirOptionFound', { files: opt.found.join(', ') })
                    : en('engine.workdirOptionFoundNone')}
                </span>
              </span>
            </label>
          )
        })}
      </fieldset>
      {payload.conflicts.length > 0 && (
        <p className="mt-2 text-xs leading-relaxed text-ink-2">
          {en('engine.workdirChooseConflicts', { files: payload.conflicts.join(', ') })}
        </p>
      )}
      <p className="mt-2 text-xs leading-relaxed text-ink-3">{en('engine.workdirChooseWrites')}</p>
      {error && <p className="mt-1 text-xs text-danger">{error}</p>}
    </Dialog>
  )
}
