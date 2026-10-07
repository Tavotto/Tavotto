import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import type { RememberedAnswer } from '@/lib/api'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { askConfirm, useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
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
 * 2026-10-07 设计审计 §10.2 P0：以前每一行各挂一颗深色「保存并重新运行」（N 颗主按钮），「删除」点下去
 * 不问就忘掉答案并重跑。现在行内只改值；脚部**唯一**的主动作「保存并重新运行（N）」依次保存所有改过的
 * 答案、**只重跑一次**；删除先问（danger），点头才忘掉并重跑。
 */
export function ScriptAnswersDialog() {
  useTranslation('dialogs')
  const script = useScriptInputStore((s) => s.managing)
  if (!script) return null
  // 按脚本换 key：换了脚本，上一个脚本没保存的改动不带过来
  return <AnswersManager key={script} script={script} />
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

function AnswersManager({ script }: { script: string }) {
  useTranslation('dialogs')
  const answers = useScriptInputStore((s) => s.answers?.[script] ?? NONE)
  const location = useScriptInputStore((s) => s.location)
  /** 行内改过、还没保存的值（按问题序号）；与已存答案相同的不算改过 */
  const [edits, setEdits] = useState<Record<number, string>>({})
  /** 每一行最近一次保存 / 删除失败的原因 */
  const [errors, setErrors] = useState<Record<number, string>>({})
  const [saving, setSaving] = useState(false)

  const close = () => useScriptInputStore.getState().closeManager()
  const valueOf = (a: RememberedAnswer) => edits[a.index] ?? a.answer
  const changed = answers.filter((a) => valueOf(a) !== a.answer)
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

  /** 依次保存所有改过的答案，**只重跑一次**；任何一条回来时已换项目就整个作罢（不在新项目里重跑同名脚本） */
  const saveAll = async () => {
    if (!changed.length || saving) return
    const epoch = useScriptInputStore.getState().epoch
    const batch = changed.map((a) => ({ index: a.index, value: valueOf(a) }))
    const submitted = new Map(batch.map((b) => [b.index, b.value]))
    setSaving(true)
    const saved: number[] = []
    try {
      for (const { index, value } of batch) {
        const res = await useScriptInputStore.getState().saveAnswer(script, index, value)
        // 请求在飞时换了项目：什么都不做——尤其不在新项目里重跑同名脚本
        if (res.status === 'stale' || !sameProject(epoch)) return
        const open = stillCurrent(epoch, script)
        if (res.status === 'error') {
          if (open) setError(index, res.error)
        } else {
          if (open) setError(index, null)
          saved.push(index)
        }
        // 对话框已关：剩下的改动没人看得见，不再接着存；已存好的照常重跑
        if (!open) break
      }
    } finally {
      if (stillCurrent(epoch, script)) setSaving(false)
    }
    if (!saved.length || !sameProject(epoch)) return
    // 存好的那几行不再是「改过」：丢掉本地副本，以后台那份为准（对话框已关就不必）
    if (stillCurrent(epoch, script)) {
      setEdits((prev) => {
        const next = { ...prev }
        // 只丢掉与提交时一致的那份；之后又改过的保留（双保险，输入框在批量保存时本就锁着）
        for (const index of saved) {
          if (next[index] === submitted.get(index)) delete next[index]
        }
        return next
      })
    }
    rerun()
  }

  /** 删除这一条：先问，点头才忘掉并重跑 */
  const forget = async (a: RememberedAnswer) => {
    const epoch = useScriptInputStore.getState().epoch
    const ok = await askConfirm({
      title: msg('scriptInput.manageForgetTitle', { index: a.index }, 'dialogs'),
      body: msg('scriptInput.manageForgetBody', { script }, 'dialogs'),
      confirmLabel: msg('actions.delete', undefined, 'common'),
      danger: true,
    })
    // 确认框开着时换了项目 / 关了对话框：点头属于旧的那一份，不发请求
    if (!ok || !stillCurrent(epoch, script)) return
    const res = await useScriptInputStore.getState().forgetAnswer(script, a.index)
    // 请求在飞时换了项目：什么都不做——尤其不在新项目里重跑同名脚本
    if (res.status === 'stale') return
    setError(a.index, res.status === 'error' ? res.error : null)
    if (res.status === 'ok') {
      setEdits((prev) => {
        const next = { ...prev }
        delete next[a.index]
        return next
      })
      rerun()
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && close()}
      anchor="script-answers"
      size="lg"
      title={si('manageTitle', { script })}
      description={si('manageIntro')}
      footer={
        <>
          <Button variant="secondary" size="md" onClick={close}>
            {translate('actions.close')}
          </Button>
          <Button
            variant="primary"
            size="md"
            data-script-answers-save
            disabled={!changed.length || saving}
            onClick={() => void saveAll()}
          >
            {changed.length
              ? si('manageSaveCount', { n: changed.length })
              : si('manageSave')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3 text-xs">
        {answers.length === 0 ? (
          <p className="text-ink-3">{si('manageEmpty')}</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {answers.map((a) => (
              <AnswerRow
                key={`${a.index}:${a.prompt}`}
                entry={a}
                value={valueOf(a)}
                error={errors[a.index] ?? null}
                disabled={saving}
                onChange={(v) => setEdits((prev) => ({ ...prev, [a.index]: v }))}
                onForget={() => void forget(a)}
              />
            ))}
          </ul>
        )}
        {location && <p className="text-ink-3">{si('manageWhere', { path: location })}</p>}
      </div>
    </Dialog>
  )
}

function AnswerRow({
  entry,
  value,
  error,
  disabled,
  onChange,
  onForget,
}: {
  entry: RememberedAnswer
  value: string
  error: string | null
  disabled: boolean
  onChange: (value: string) => void
  onForget: () => void
}) {
  return (
    <li
      className="flex flex-col gap-1"
      data-script-answer={entry.index}
      data-dirty={value !== entry.answer || undefined}
    >
      <span className="whitespace-pre-wrap break-words font-mono text-ink-2">
        {entry.prompt
          ? si('managePrompt', { index: entry.index, prompt: entry.prompt })
          : si('manageNoPrompt', { index: entry.index })}
      </span>
      <div className="flex items-center gap-1.5">
        <TextInput
          align="left"
          value={value}
          aria-label={si('answerLabel')}
          // 批量保存在飞时锁住：存的是点按钮那一刻的快照，途中再打的字会被存好后的清理冲掉（Codex #821 P1）
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
        <Button
          variant="ghost"
          size="md"
          data-script-answer-forget
          disabled={disabled}
          aria-label={si('manageForgetAria', { index: entry.index })}
          onClick={onForget}
        >
          {si('manageForget')}
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
    </li>
  )
}
