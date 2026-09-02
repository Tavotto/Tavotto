import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { fetchDiagnosticsSummary } from '@/lib/api'
import { apiUrl, withProject } from '@/lib/session'
import { cn } from '@/lib/utils'
import { useEnvStore } from '@/store/envStore'
import { EngineEnvironmentCard } from '../EngineEnvironmentCard'
import { Button } from '../ui/Button'
import { CopyButton } from './CopyButton'
import { DiagnosticsExportButton } from './PrivacyAboutSettings'
import { DiagnosticDisclosure, DiagnosticItem, HelpTip, SettingSection } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })
const en = (key: string, values?: Record<string, unknown>) =>
  translate(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

interface Check {
  id: string
  ok: boolean
  label: string
  detail: string
}

/**
 * 「编码 Agent」分区已经把每个 CLI 的状态说清了；诊断页不再重复它们
 * （ADR 0038 §诊断去重）。诊断包里照旧带着——那是给排障的人看的另一份。
 */
const DUPLICATED_ELSEWHERE = /^cli_/

/**
 * 设置 → 诊断（ADR 0038）。
 *
 * 首屏只有三件事：**健康状态**（每一项一行、坏的在前、说明为什么坏）、
 * **复制诊断**（先预览脱敏后的文本，再复制）、**导出诊断包**。渲染环境不正常
 * 时恢复卡片常驻（那是缺件，不许折叠）；正常时一行「解释器来源」摘要。
 * 解释器绝对路径、切换解释器的入口全在「技术详情」折叠区——用户不必懂
 * Python 环境路径也能知道能不能用。内置包版本在「包管理」，这里不重复。
 */
export function DiagnosticsSettings() {
  useTranslation('dialogs')
  const { env, refresh } = useEnvStore()
  const [checks, setChecks] = useState<Check[] | null>(null)
  useEffect(() => {
    if (!env) void refresh()
  }, [env, refresh])
  useEffect(() => {
    void fetch(apiUrl('/api/diagnostics'), withProject())
      .then((r) => r.json())
      .then((d) => setChecks(((d.checks ?? []) as Check[]).filter((c) => !DUPLICATED_ELSEWHERE.test(c.id))))
      .catch(() => setChecks([]))
  }, [])

  const failing = (checks ?? []).filter((c) => !c.ok)
  const passing = (checks ?? []).filter((c) => c.ok)

  return (
    <div className="flex flex-col gap-4" data-diagnostics-page>
      <SettingSection title={st('diagnostics.healthTitle')}>
        {checks === null ? (
          <p className="text-xs text-ink-3">{st('about.detecting')}</p>
        ) : (
          <>
            <p className={cn('text-xs', failing.length ? 'text-ink' : 'text-ink-2')}>
              {failing.length
                ? st('diagnostics.summaryFailing', { count: failing.length })
                : st('diagnostics.summaryOk')}
            </p>
            <ul className="flex flex-col gap-1">
              {[...failing, ...passing].map((c) => (
                <li key={c.id} className="flex items-start gap-1.5 text-xs">
                  <span
                    aria-hidden
                    className={cn('mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full', c.ok ? 'bg-ink-3' : 'bg-danger')}
                  />
                  <span className="sr-only">{st(c.ok ? 'about.checkOk' : 'about.checkFail')}</span>
                  <span className="shrink-0 text-ink-2">
                    {translate(`settings.about.check.${c.id}`, { ns: 'dialogs', defaultValue: c.label })}
                  </span>
                  {/* 坏的说原因（诊断数据，不翻）；好的只说一个字 */}
                  {!c.ok && (
                    <span className="min-w-0 flex-1 break-all font-mono text-ink-3">{c.detail}</span>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
        {/* 缺件 / 损坏：恢复入口整张常驻（那时它给的是「自动安装 / 换解释器」） */}
        {env && !env.ok && <EngineEnvironmentCard />}
      </SettingSection>

      <SettingSection title={st('diagnostics.reportTitle')}>
        <div className="flex flex-wrap items-center gap-2">
          <CopySummary />
          <DiagnosticsExportButton />
          <HelpTip label={st('about.diagnosticsHelpAria')}>
            <p>
              {st('about.diagnosticsHintBefore')}
              <strong className="font-medium text-ink">{st('about.diagnosticsHintStrong')}</strong>
              {st('about.diagnosticsHintAfter')}
            </p>
          </HelpTip>
        </div>
      </SettingSection>

      {/* 技术详情：来源 / 版本 / 完整路径 / 换解释器。默认折叠 */}
      <DiagnosticDisclosure title={st('techDetails')}>
        {env?.ok && (
          <>
            <DiagnosticItem
              name={st('about.engineStatus')}
              value={en(`sourceLabel.${env.source || 'unknown'}`)}
            />
            <DiagnosticItem name="matplotlib" value={env.matplotlib ?? '—'} />
          </>
        )}
        {(checks ?? [])
          .filter((c) => c.ok && c.detail)
          .map((c) => (
            <DiagnosticItem
              key={c.id}
              name={translate(`settings.about.check.${c.id}`, { ns: 'dialogs', defaultValue: c.label })}
              value={c.detail}
            />
          ))}
        {/* 解释器绝对路径、「使用其他 Python 环境…」都在这张卡里，**只在这里出现一次** */}
        {env?.ok && (
          <div className="pt-1">
            <EngineEnvironmentCard />
          </div>
        )}
      </DiagnosticDisclosure>
    </div>
  )
}

/**
 * 「复制诊断」：先把脱敏后的文本摆出来，用户看过再复制。
 * 文本由后端 `/api/diagnostics/summary` 给（与诊断包同一份采集、同一道脱敏），
 * 前端不再自己拼一份——拼一份就是第二个采集出处。
 */
function CopySummary() {
  useTranslation('dialogs')
  const [phase, setPhase] = useState<'idle' | 'busy' | 'ready' | 'error'>('idle')
  const [text, setText] = useState('')
  const prepare = async () => {
    setPhase('busy')
    try {
      const res = await fetchDiagnosticsSummary()
      setText(res.text)
      setPhase('ready')
    } catch {
      setPhase('error')
    }
  }
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {phase !== 'ready' ? (
          <Button variant="outline" size="sm" onClick={() => void prepare()} disabled={phase === 'busy'}>
            {phase === 'busy' ? st('diagnostics.preparing') : st('diagnostics.copyReport')}
          </Button>
        ) : (
          <>
            <CopyButton text={text} label={st('diagnostics.copyReport')} className="h-7 border border-border px-2" />
            <Button variant="ghost" size="sm" onClick={() => setPhase('idle')}>
              {st('diagnostics.hidePreview')}
            </Button>
          </>
        )}
        {phase === 'error' && (
          <span role="alert" className="text-xs text-danger">
            {st('diagnostics.prepareFailed')}
          </span>
        )}
      </div>
      {phase === 'ready' && (
        <div className="flex flex-col gap-1" data-diagnostics-preview>
          <p className="text-xs text-ink-3">{st('diagnostics.previewNote')}</p>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-border bg-surface-2 p-1.5 font-mono text-[11px] leading-relaxed text-ink-3">
            {text}
          </pre>
        </div>
      )}
    </div>
  )
}
