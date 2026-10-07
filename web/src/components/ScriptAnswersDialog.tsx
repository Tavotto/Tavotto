import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import type { RememberedAnswer } from '@/lib/api'
import { useScriptInputStore, type AnswerChange } from '@/store/scriptInputStore'
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
 *
 * 答案按运行配置分开记（`run_config`，null = 默认配置）：同一序号在两份配置下是两行，改 / 删只动那一行，
 * 重跑也只重跑那份配置（`run(script, run_config)`）。批量保存涉及几份配置就各重跑一次，一份配置仍是一次。
 */
export function ScriptAnswersDialog() {
  useTranslation('dialogs')
  const script = useScriptInputStore((s) => s.managing)
  if (!script) return null
  // 按脚本换 key：换了脚本，上一个脚本没保存的改动不带过来
  return <AnswersManager key={script} script={script} />
}

/** 一行答案的身份：同一序号在不同运行配置下是不同的行 */
const rowKey = (a: RememberedAnswer) => JSON.stringify([a.run_config ?? null, a.index])

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
  /** 行内改过、还没保存的值（按行身份 `rowKey`）；与已存答案相同的不算改过 */
  const [edits, setEdits] = useState<Record<string, string>>({})
  /** 每一行最近一次保存 / 删除失败的原因 */
  const [errors, setErrors] = useState<Record<string, string>>({})
  /**
   * 答案改动锁归 store（`answersBusy`）：关掉再打开，新挂上的对话框照样锁着，直到在飞的那件回来
   * （维护者复审：组件自己的 `saving` 随卸载消失，重开后能再存一次、两份快照互盖、重跑两次）
   */
  const saving = useScriptInputStore((s) => s.answersBusy)

  const close = () => useScriptInputStore.getState().closeManager()
  const valueOf = (a: RememberedAnswer) => edits[rowKey(a)] ?? a.answer
  const changed = answers.filter((a) => valueOf(a) !== a.answer)
  const setError = (key: string, error: string | null) =>
    setErrors((prev) => {
      const next = { ...prev }
      if (error === null) delete next[key]
      else next[key] = error
      return next
    })
  const rerun = (configs: Array<string | null>) => {
    useUiStore.getState().setStatus(msg('scriptInput.manageSaved', { script }, 'dialogs'), 'done')
    for (const config of configs) void useScriptRunStore.getState().run(script, config)
  }

  /** 依次保存所有改过的答案，**只重跑一次**；任何一条回来时已换项目就整个作罢（不在新项目里重跑同名脚本） */
  const saveAll = async () => {
    if (!changed.length) return
    const store = useScriptInputStore.getState()
    const epoch = store.epoch
    // 整批算一件改动；已有一件在飞（含关掉重开前没回来的删除）就一个请求都不发
    const token = store.beginAnswersChange()
    if (token === null) return
    const batch = changed.map((a) => ({
      key: rowKey(a),
      index: a.index,
      config: a.run_config ?? null,
      value: valueOf(a),
    }))
    const submitted = new Map(batch.map((b) => [b.key, b.value]))
    const saved: string[] = []
    const configs: Array<string | null> = []
    try {
      for (const { key, index, config, value } of batch) {
        const res = await useScriptInputStore
          .getState()
          .saveAnswer(token, script, index, value, config)
        // 请求在飞时换了项目：什么都不做——尤其不在新项目里重跑同名脚本
        if (res.status === 'stale' || !sameProject(epoch)) return
        const open = stillCurrent(epoch, script)
        if (res.status === 'error') {
          if (open) setError(key, res.error)
        } else {
          if (open) setError(key, null)
          saved.push(key)
          if (!configs.includes(config)) configs.push(config)
        }
        // 对话框已关：剩下的改动没人看得见，不再接着存；已存好的照常重跑
        if (!open) break
      }
    } finally {
      useScriptInputStore.getState().endAnswersChange(token)
    }
    if (!saved.length || !sameProject(epoch)) return
    // 存好的那几行不再是「改过」：丢掉本地副本，以后台那份为准（对话框已关就不必）
    if (stillCurrent(epoch, script)) {
      setEdits((prev) => {
        const next = { ...prev }
        // 只丢掉与提交时一致的那份；之后又改过的保留（双保险，输入框在批量保存时本就锁着）
        for (const key of saved) {
          if (next[key] === submitted.get(key)) delete next[key]
        }
        return next
      })
    }
    rerun(configs)
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
    // 删除与批量保存共用 store 那把锁：输入框、保存钮、各行删除都锁住（关掉重开也锁着），
    // 否则并发的保存与删除谁先回来谁赢、还会重跑两次（Codex #821 P2 / 维护者复审）
    const token = useScriptInputStore.getState().beginAnswersChange()
    if (token === null) return
    let res: AnswerChange
    try {
      res = await useScriptInputStore
        .getState()
        .forgetAnswer(token, script, a.index, a.run_config ?? null)
    } finally {
      useScriptInputStore.getState().endAnswersChange(token)
    }
    // 请求在飞时换了项目：什么都不做——尤其不在新项目里重跑同名脚本
    if (res.status === 'stale') return
    setError(rowKey(a), res.status === 'error' ? res.error : null)
    if (res.status === 'ok') {
      setEdits((prev) => {
        const next = { ...prev }
        delete next[rowKey(a)]
        return next
      })
      rerun([a.run_config ?? null])
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
                key={JSON.stringify([script, a.run_config ?? null, a.index, a.prompt])}
                entry={a}
                value={valueOf(a)}
                error={errors[rowKey(a)] ?? null}
                disabled={saving}
                onChange={(v) => setEdits((prev) => ({ ...prev, [rowKey(a)]: v }))}
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
      <span className="text-ink-3">
        {entry.run_config ? si('manageConfig', { config: entry.run_config }) : si('manageDefaultConfig')}
      </span>
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
