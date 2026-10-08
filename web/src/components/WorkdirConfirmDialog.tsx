import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { WorkdirMode } from '@/lib/api'
import { useEnvStore } from '@/store/envStore'
import { scriptRunEpoch, useScriptRunStore } from '@/store/scriptRunStore'
import { currentProjectId } from '@/lib/session'
import { Badge } from './ui/Badge'
import { Button } from './ui/Button'
import { Card } from './ui/Card'
import { Dialog } from './ui/Dialog'
import { Notice } from './ui/Notice'
import { Popover } from './ui/Popover'
import { Radio } from './ui/Radio'

/**
 * 首开的那一次确认（U03，ADR 0057 §三）：后端起第一个 worker 之前按脚本的静态证据判出
 * 「数据只有项目根找得到」、「脚本目录与项目根各有一份同名数据、内容不同」或「脚本用 glob /
 * listdir / exists 找数据、只有脚本目录找得到」（ADR 0084），渲染以
 * `workdir_confirmation_required` 回来——这不是错误，是缺一个决定。
 *
 * 三档一次选：项目根 / 脚本目录 / 继续沙盒。每档列出**该目录下找得到的文件**（后端只按
 * 字面量查存在性，不猜、不搜同名）；推荐项只在证据唯一指向一个目录时预选，歧义时**不预选**
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
  const current = useEnvStore((s) => s.workdirConfirmation)
  const dismiss = useEnvStore((s) => s.dismissWorkdirConfirmation)
  const setWorkdirMode = useEnvStore((s) => s.setWorkdirMode)
  const [choice, setChoice] = useState<WorkdirMode | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 常驻挂载：关的那 90ms 里载荷已经清掉，正文按最后一份画（Dialog 的常驻写法）
  const last = useRef(current)
  if (current) last.current = current
  const payload = last.current
  useEffect(() => {
    // 每一份新载荷从它自己的推荐项起步：歧义时 null（不预选）
    if (!current) return
    setChoice(current.recommended ?? null)
    setError(null)
  }, [current])
  if (!payload) return null
  const en = (key: string, values?: Record<string, unknown>) => t(key, values)
  const confirm = async () => {
    if (!choice) return
    setBusy(true)
    const project = currentProjectId()
    const epoch = scriptRunEpoch()
    const err = await setWorkdirMode(choice, { confirmed: true })
    setBusy(false)
    if (err) {
      setError(err)
      return
    }
    // 作答期间换过项目：`setWorkdirMode` 的「换代作废」也回 null，与成功同形——按发起时的**代际**判
    // （A → B → A 项目 id 相同、代际已变，那次选择并没有落在这一代上），别拿它去重跑试运行（#740 Codex P2）
    if (currentProjectId() !== project || scriptRunEpoch() !== epoch) return
    // 素材库脚本行上停在这道门上的试运行重跑（面板的渲染由 `setWorkdirMode` 重排）。运行目录是项目级的：
    // 停在这一相位上的全部重跑。放在这里而不是 envStore：envStore → scriptRunStore 会让既有的 import 环扩大
    useScriptRunStore.getState().rerunGated('needs_workdir')
  }
  return (
    <Dialog
      open={!!current}
      onOpenChange={(v) => {
        if (!v && !busy) dismiss()
      }}
      // Esc = 稍后（只关框，错误块里还能再打开）
      onEscape={dismiss}
      title={en('engine.workdirChooseTitle')}
      description={
        payload.reason === 'ambiguous_data'
          ? en('engine.workdirChooseAmbiguous', { script: payload.script })
          : payload.reason === 'script_dir_evidence'
            ? en('engine.workdirChooseScriptDirEvidence', { script: payload.script })
            : en('engine.workdirChooseRootEvidence', { script: payload.script })
      }
      size="md"
      busy={busy}
      anchor="workdir-confirm"
      footer={{
        secondary: (
          <Button variant="secondary" size="lg" disabled={busy} onClick={dismiss}>
            {en('engine.workdirChooseLater')}
          </Button>
        ),
        primary: (
          <Button
            data-workdir-run
            variant="primary"
            size="lg"
            loading={busy}
            disabled={!choice}
            onClick={() => void confirm()}
          >
            {en('engine.workdirChooseRun')}
          </Button>
        ),
      }}
    >
      <div className="flex flex-col gap-3">
        <fieldset className="flex flex-col gap-2" data-workdir-choice>
          <legend className="sr-only">{en('engine.workdirChooseTitle')}</legend>
          {payload.options.map((opt) => {
            const selected = choice === opt.mode
            return (
              // 三档是三张可选的卡（2026-10-07 设计审计 §10.2）：此前是 360px 里十来行 11px 灰字
              <Card
                key={opt.mode}
                appearance="subtle"
                padding="none"
                interactive={!busy}
                selected={selected}
                data-workdir-option={opt.mode}
              >
                <label className="flex items-start gap-2.5 px-3 py-2.5">
                  <Radio
                    name="workdir-choice"
                    className="mt-0.5"
                    checked={selected}
                    disabled={busy}
                    onChange={() => setChoice(opt.mode)}
                  />
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="flex items-center gap-1.5 text-ink">
                      {en(OPTION_LABEL[opt.mode])}
                      {opt.recommended && (
                        <Badge tone="ok" data-workdir-recommended>
                          {en('engine.workdirRecommended')}
                        </Badge>
                      )}
                    </span>
                    <span className="text-sm text-ink-3">{en(OPTION_HINT[opt.mode])}</span>
                    {/* 找得到的文件是用户自己的数据名，不翻译 */}
                    <span className="break-all font-mono text-sm text-ink-2">
                      {opt.found.length
                        ? en('engine.workdirOptionFound', { files: opt.found.join(', ') })
                        : en('engine.workdirOptionFoundNone')}
                    </span>
                  </span>
                </label>
              </Card>
            )
          })}
        </fieldset>
        {payload.conflicts.length > 0 && (
          <p className="text-ink-2">{en('engine.workdirChooseConflicts', { files: payload.conflicts.join(', ') })}</p>
        )}
        {/* 写出文件的后果：一行警示，完整那段放进「详情」气泡（此前是一整段 11px 灰字） */}
        <Notice
          tone="warn"
          data-workdir-writes
          action={
            <Popover
              align="end"
              side="top"
              width={320}
              trigger={
                <Button data-workdir-writes-more variant="ghost" size="sm" className="text-warn-content">
                  {en('engine.workdirChooseWritesMore')}
                </Button>
              }
            >
              <p className="type-reading text-ink-2">{en('engine.workdirChooseWrites')}</p>
            </Popover>
          }
        >
          {en('engine.workdirChooseWritesShort')}
        </Notice>
        {error && <Notice tone="danger">{error}</Notice>}
      </div>
    </Dialog>
  )
}
