import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useEnvStore } from '@/store/envStore'
import { Button } from './ui/Button'
import { Toggle } from './ui/Toggle'

const en = (key: string, values?: Record<string, unknown>) =>
  translate(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

/**
 * 「在脚本目录里运行」——safe worker 工作目录模式的项目级开关（ADR 0045）。
 *
 * 文案与机制逐条一致：开了之后脚本用相对路径读的数据找得到、用相对路径写的
 * 文件落进项目目录；Tavotto 仍然不替它保存图片、不删不改项目里的文件。
 * 开启要确认一次（在 envStore.setWorkdirMode 里），关闭不用。
 */
export function WorkdirRow() {
  useTranslation('errors')
  const { env, setWorkdirMode } = useEnvStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const project = env?.project
  if (!project?.open || !project.workdir) return null
  const on = project.workdir.mode === 'project'
  const flip = async (next: boolean) => {
    setBusy(true)
    setError(await setWorkdirMode(next ? 'project' : 'sandbox'))
    setBusy(false)
  }
  return (
    <div className="mt-1.5 flex flex-col gap-0.5 border-t border-border pt-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-ink-2">{en('workdirLabel')}</span>
        <Toggle checked={on} disabled={busy} onChange={(v) => void flip(v)} aria-label={en('workdirAria')} />
      </div>
      <span className="text-xs leading-relaxed text-ink-3">
        {on ? en('workdirHintProject') : en('workdirHintSandbox')}
      </span>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}

/**
 * 「脚本跑完没出图」错误块里的出口：多半是沙盒 cwd 下相对路径找不到数据。
 * 已经是脚本目录模式时不显示——那时原因在别处。
 */
export function WorkdirSuggestion() {
  useTranslation('errors')
  const { env, setWorkdirMode } = useEnvStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const project = env?.project
  if (!project?.open || project.workdir?.mode !== 'sandbox') return null
  return (
    <div className="mt-1.5 flex flex-col gap-1">
      <p className="text-xs leading-relaxed text-ink-2">{en('workdirSuggest')}</p>
      <Button
        className="self-start"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          setError(await setWorkdirMode('project'))
          setBusy(false)
        }}
      >
        {en('workdirSuggestButton')}
      </Button>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}
