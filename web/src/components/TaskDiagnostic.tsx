import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { fetchTaskDiagnostic, type TaskDiagnosticKind } from '@/lib/api'
import { t as translate } from '@/i18n'
import { currentProjectId } from '@/lib/session'
import { Button } from './ui/Button'
import { Details, Summary } from './ui/Details'

/** 本组件的文案都在 `dialogs:taskDiagnostic.*` 下 */
const td = (key: string) => translate(`taskDiagnostic.${key}`, { ns: 'dialogs' })

type Phase = 'idle' | 'busy' | 'done' | 'error' | 'not_found' | 'expired'

/**
 * 「本次问题的诊断」（T04）：失败提示处一键拿到**那一次**尝试的快照。
 *
 * * 默认只露一个折叠标题（卡片一句话：失败提示本身的主按钮是「重试」，这里不抢）；展开才是一句说明 + 一个按钮。
 * * 快照是终局时冻结的白名单摘要；后端没有这条记录（过期 / 应用重启过）时**如实说没有了**，
 *   不去下载一份当前状态的诊断包冒充当时的状态。
 * * 项目取组件出现那一刻的：失败提示可能比项目切换活得久，点按钮时再取就会拿别的项目的 id 去问。
 *
 * `folded={false}` 给已经住在折叠区里的调用方（脚本行的「详情」）：只出按钮和结果，不再套一层折叠。
 */
export function TaskDiagnostic({
  kind,
  refId,
  folded = true,
}: {
  kind: TaskDiagnosticKind
  refId: string
  folded?: boolean
}) {
  useTranslation('dialogs')
  const pj = useRef(currentProjectId())
  const [phase, setPhase] = useState<Phase>('idle')

  const run = () => {
    setPhase('busy')
    void fetchTaskDiagnostic(kind, refId, pj.current)
      .then((res) => {
        if (!res.available) {
          setPhase(res.reason)
          return
        }
        const url = URL.createObjectURL(res.blob)
        try {
          const a = document.createElement('a')
          a.href = url
          a.download = res.filename
          a.click()
        } finally {
          URL.revokeObjectURL(url)
        }
        setPhase('done')
      })
      .catch(() => setPhase('error'))
  }

  const body = (
    <div className="flex flex-col gap-1.5" data-task-diagnostic-body>
      {folded && <p className="type-caption">{td('hint')}</p>}
      <div className="flex flex-wrap items-center gap-1.5">
        <Button variant="secondary" size="sm" onClick={run} disabled={phase === 'busy'}>
          {td(phase === 'busy' ? 'busy' : 'download')}
        </Button>
        {phase === 'done' && (
          <span className="text-xs text-ink-2" role="status">
            {td('done')}
          </span>
        )}
        {phase === 'error' && (
          <span className="text-xs text-danger" role="alert">
            {td('failed')}
          </span>
        )}
        {(phase === 'not_found' || phase === 'expired') && (
          <span className="text-xs text-ink-2" role="status" data-task-diagnostic-gone={phase}>
            {td(phase === 'expired' ? 'expired' : 'notFound')}
          </span>
        )}
      </div>
    </div>
  )
  if (!folded) return body
  return (
    <Details className="mt-0.5" data-task-diagnostic data-task-diagnostic-kind={kind}>
      <Summary className="type-meta">{td('title')}</Summary>
      <div className="mt-1.5">{body}</div>
    </Details>
  )
}
