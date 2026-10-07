import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import type { RememberedAnswer } from '@/lib/api'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import { cn } from '@/lib/utils'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { FieldGroup } from './ui/FormSection'
import { Trash2 } from './ui/icons'
import { MenuItem } from './ui/Menu'
import { Notice } from './ui/Notice'
import { RowMenu } from './ui/RowMenu'
import { useRowMenu } from './ui/useRowMenu'
import { TextInput } from './ui/Input'

/** 选择器的空值必须是同一个引用：每次新建 `[]` 会让 zustand 判成「变了」而无限重渲染 */
const NONE: RememberedAnswer[] = []

const si = (key: string, values?: Record<string, unknown>) =>
  translate(`scriptInput.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 一个脚本记住的输入（ADR 0099 §七）：看、改、删。改 / 删之后后端作废热会话并通知重渲染用到它的面板，
 * 这里再对这个脚本发起一次运行——产出的图名可能随选择变化，要重新登记。
 * 提示文字是用户脚本的文字，纯文本渲染。
 *
 * 2026-10-07 设计审计 §10.2：以前每一行各挂一颗深色「保存并重新运行」（N 颗主按钮），「删除」点下去
 * 不问就忘掉答案并重跑。现在每一行是字段组里的一行：行内只改值，「删除」收进行尾 ⋯、是**暂存**的
 * （行上标「将删除」、可撤销）；脚部**唯一**的主动作「保存并重新运行（N）」依次提交所有改过 / 标了删除的
 * 答案、**只重跑一次**。没提交前什么都没发生，所以不再需要第二层确认框。
 */
export function ScriptAnswersDialog() {
  useTranslation('dialogs')
  const managing = useScriptInputStore((s) => s.managing)
  // 常驻挂载：关的那 90ms 里 `managing` 已经是 null，正文按最后一个脚本画；每次重新打开换一代 key——
  // 上一次没提交的改动不带进下一次（换了脚本同理）
  const last = useRef<{ script: string; gen: number; open: boolean } | null>(null)
  const prev = last.current
  if (managing && (!prev || !prev.open || prev.script !== managing)) {
    last.current = { script: managing, gen: (prev?.gen ?? 0) + 1, open: true }
  } else if (!managing && prev?.open) {
    last.current = { ...prev, open: false }
  }
  const shown = last.current
  if (!shown) return null
  return <AnswersManager key={`${shown.script}:${shown.gen}`} script={shown.script} open={!!managing} />
}

/** 这次请求还属于发起时那个项目 / 那个对话框吗（换项目 = store 换代，`managing` 被清） */
function stillCurrent(epoch: number, script: string): boolean {
  const s = useScriptInputStore.getState()
  return s.epoch === epoch && s.managing === script
}

/** 只看项目代际：保存在飞时用户关掉对话框不算作罢——答案已经存进同一个项目，重跑仍要做 */
function sameProject(epoch: number): boolean {
  return useScriptInputStore.getState().epoch === epoch
}

function AnswersManager({ script, open }: { script: string; open: boolean }) {
  useTranslation('dialogs')
  const answers = useScriptInputStore((s) => s.answers?.[script] ?? NONE)
  const location = useScriptInputStore((s) => s.location)
  /** 行内改过、还没保存的值（按问题序号）；与已存答案相同的不算改过 */
  const [edits, setEdits] = useState<Record<number, string>>({})
  /** 标了「删除」、还没提交的问题序号（暂存：脚部主按钮才真的忘掉） */
  const [forgets, setForgets] = useState<ReadonlySet<number>>(EMPTY)
  /** 每一行最近一次保存 / 删除失败的原因 */
  const [errors, setErrors] = useState<Record<number, string>>({})
  const [saving, setSaving] = useState(false)

  const close = () => useScriptInputStore.getState().closeManager()
  const valueOf = (a: RememberedAnswer) => edits[a.index] ?? a.answer
  const staged = answers.filter((a) => forgets.has(a.index))
  const changed = answers.filter((a) => !forgets.has(a.index) && valueOf(a) !== a.answer)
  const pending = changed.length + staged.length
  const setError = (index: number, error: string | null) =>
    setErrors((prev) => {
      const next = { ...prev }
      if (error === null) delete next[index]
      else next[index] = error
      return next
    })
  const rerun = () => {
    useUiStore.getState().setStatus(msg('scriptInput.manageSaved', { script }, 'dialogs'), 'done')
    void useScriptRunStore.getState().run(script)
  }
  const toggleForget = (index: number, on: boolean) =>
    setForgets((prev) => {
      const next = new Set(prev)
      if (on) next.add(index)
      else next.delete(index)
      return next
    })

  /**
   * 依次提交所有改过的答案与标了删除的答案，**只重跑一次**；任何一条回来时已换项目就整个作罢
   * （不在新项目里重跑同名脚本）。对话框在途中被关掉：剩下的不再发，已提交好的照常重跑。
   */
  const commitAll = async () => {
    if (!pending || saving) return
    const epoch = useScriptInputStore.getState().epoch
    const batch: { index: number; value: string | null }[] = [
      ...changed.map((a) => ({ index: a.index, value: valueOf(a) })),
      ...staged.map((a) => ({ index: a.index, value: null })),
    ]
    const submitted = new Map(batch.map((b) => [b.index, b.value]))
    setSaving(true)
    const done: number[] = []
    try {
      for (const { index, value } of batch) {
        const res =
          value === null
            ? await useScriptInputStore.getState().forgetAnswer(script, index)
            : await useScriptInputStore.getState().saveAnswer(script, index, value)
        // 请求在飞时换了项目：什么都不做——尤其不在新项目里重跑同名脚本
        if (res.status === 'stale' || !sameProject(epoch)) return
        const stillOpen = stillCurrent(epoch, script)
        if (res.status === 'error') {
          if (stillOpen) setError(index, res.error)
        } else {
          if (stillOpen) setError(index, null)
          done.push(index)
        }
        // 对话框已关：剩下的改动没人看得见，不再接着发；已提交好的照常重跑
        if (!stillOpen) break
      }
    } finally {
      if (stillCurrent(epoch, script)) setSaving(false)
    }
    if (!done.length || !sameProject(epoch)) return
    // 提交好的那几行不再是「改过 / 将删除」：丢掉本地副本，以后台那份为准（对话框已关就不必）
    if (stillCurrent(epoch, script)) {
      setEdits((prev) => {
        const next = { ...prev }
        // 只丢掉与提交时一致的那份；之后又改过的保留（双保险，输入框在批量保存时本就锁着）
        for (const index of done) {
          if (submitted.get(index) === null || next[index] === submitted.get(index)) delete next[index]
        }
        return next
      })
      setForgets((prev) => {
        const next = new Set(prev)
        for (const index of done) if (submitted.get(index) === null) next.delete(index)
        return next
      })
    }
    rerun()
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => !v && close()}
      // Esc 的安全答案 = 不提交、关掉（没提交前什么都没发生）
      onEscape={close}
      // 批量保存在飞时锁住（Codex #831 P1）：×、点外面、Esc 都关不掉——半途关掉会让 commitAll 停在
      // 第一条之后、只带着部分改动重跑，剩下的暂存改动丢掉；「一次提交、只重跑一次」必须走完
      busy={saving}
      anchor="script-answers"
      size="lg"
      title={si('manageTitle', { script })}
      description={si('manageIntro')}
      footer={{
        // 有没提交的改动时这颗是「取消」（放弃这次决定），没有时是「关闭」（无待决）——跨对话框规则
        secondary: (
          <Button variant="secondary" size="lg" data-script-answers-close disabled={saving} onClick={close}>
            {pending ? translate('actions.cancel') : translate('actions.close')}
          </Button>
        ),
        primary: (
          <Button
            variant="primary"
            size="lg"
            data-script-answers-save
            disabled={!pending}
            loading={saving}
            onClick={() => void commitAll()}
          >
            {pending ? si('manageSaveCount', { n: pending }) : si('manageSave')}
          </Button>
        ),
      }}
    >
      <div className="flex flex-col gap-3">
        {answers.length === 0 ? (
          <p className="text-ink-3">{si('manageEmpty')}</p>
        ) : (
          <FieldGroup data-script-answers-rows>
            {answers.map((a) => (
              <AnswerRow
                key={`${a.index}:${a.prompt}`}
                entry={a}
                value={valueOf(a)}
                forgetting={forgets.has(a.index)}
                error={errors[a.index] ?? null}
                disabled={saving}
                onChange={(v) => setEdits((prev) => ({ ...prev, [a.index]: v }))}
                onForget={(on) => toggleForget(a.index, on)}
              />
            ))}
          </FieldGroup>
        )}
        {location && <p className="text-sm text-ink-3">{si('manageWhere', { path: location })}</p>}
      </div>
    </Dialog>
  )
}

const EMPTY: ReadonlySet<number> = new Set()

function AnswerRow({
  entry,
  value,
  forgetting,
  error,
  disabled,
  onChange,
  onForget,
}: {
  entry: RememberedAnswer
  value: string
  forgetting: boolean
  error: string | null
  disabled: boolean
  onChange: (value: string) => void
  onForget: (on: boolean) => void
}) {
  const menu = useRowMenu({ enabled: !forgetting && !disabled })
  return (
    <div
      {...menu.rowProps}
      className="group flex flex-col gap-1.5"
      data-script-answer={entry.index}
      data-dirty={(!forgetting && value !== entry.answer) || undefined}
      data-forgetting={forgetting || undefined}
    >
      <span
        className={cn(
          'whitespace-pre-wrap break-words font-mono text-sm text-ink-2',
          forgetting && 'text-ink-3 line-through',
        )}
      >
        {entry.prompt
          ? si('managePrompt', { index: entry.index, prompt: entry.prompt })
          : si('manageNoPrompt', { index: entry.index })}
      </span>
      <div className="flex items-center gap-1.5">
        {forgetting ? (
          <>
            <span className="min-w-0 flex-1 text-sm text-danger-content" data-script-answer-staged>
              {si('manageForgetStaged')}
            </span>
            <Button variant="ghost" size="sm" data-script-answer-undo disabled={disabled} onClick={() => onForget(false)}>
              {si('manageForgetUndo')}
            </Button>
          </>
        ) : (
          <>
            <TextInput
              align="left"
              value={value}
              aria-label={si('answerLabel')}
              // 批量保存在飞时锁住：存的是点按钮那一刻的快照，途中再打的字会被存好后的清理冲掉（Codex #821 P1）
              disabled={disabled}
              onChange={(e) => onChange(e.target.value)}
            />
            <RowMenu state={menu} label={si('manageRowActions', { index: entry.index })} visible="always">
              <MenuItem icon={Trash2} danger data-script-answer-forget onSelect={() => onForget(true)}>
                {si('manageForget')}
              </MenuItem>
            </RowMenu>
          </>
        )}
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
    </div>
  )
}
