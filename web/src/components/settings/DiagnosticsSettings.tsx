import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { fetchDiagnosticsSummary } from '@/lib/api'
import { formatDateTime } from '@/i18n/format'
import { PRODUCT_NAME } from '@/lib/brand'
import { apiUrl, withProject } from '@/lib/session'
import { cn } from '@/lib/utils'
import { useEnvStore } from '@/store/envStore'
import { EngineEnvironmentCard } from '../EngineEnvironmentCard'
import { RefreshCw } from 'lucide-react'
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
 * 首屏只有三件事：**健康状态**、**复制诊断**（先预览脱敏后的文本，再复制）、
 * **导出诊断包**。渲染环境不正常时恢复卡片常驻（那是缺件，不许折叠）。
 * 解释器绝对路径、切换解释器的入口全在「技术详情」折叠区——用户不必懂
 * Python 环境路径也能知道能不能用。内置包版本在「包管理」，这里不重复。
 *
 * 审计 T47 改了三件事：
 *
 * 1. **正常项默认折叠**。全绿时首屏只有一句结论，逐项结果在折叠区里——它们
 *    此前既铺在首屏、又在「技术详情」里重复了一遍。异常项照旧列在首屏。
 * 2. **说清检查的边界**。「全部正常」会被读成"图没问题"；这里查的只有运行
 *    环境，图合不合规范是「问题」面板的事，两者互不代表。
 * 3. **两个环境不再同名**。「{{product}} 自带的渲染环境」（随包、只读）与
 *    「这个项目的 {{product}} 环境」（装包时新建、可 pip）是两个不同的东西，
 *    以前都叫「Tavotto 环境」，于是包管理页说「尚未创建」、这一页同时说
 *    「matplotlib 3.11.1」，看起来像两页对不上。名字分开之后，这一句解释
 *    留在技术详情里。
 * 4. **说清这一页的数据什么时候取的**。`/api/diagnostics` 没有服务端时间戳，
 *    所以说的是**本页取数的时刻**（而不是"最近检测"——解释器选择在后端是
 *    进程级缓存，重取不会重挑）。
 */
export function DiagnosticsSettings() {
  useTranslation('dialogs')
  const { env, refresh } = useEnvStore()
  const [checks, setChecks] = useState<Check[] | null>(null)
  /** 本页取数的时刻。**不叫「最近检测」**：后端那几条里有进程级缓存的，
   *  重取不等于重挑解释器，说成"刚检测过"就是在替它担保。 */
  const [fetchedAt, setFetchedAt] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const d = await fetch(apiUrl('/api/diagnostics'), withProject()).then((r) => r.json())
      setChecks(((d.checks ?? []) as Check[]).filter((c) => !DUPLICATED_ELSEWHERE.test(c.id)))
    } catch {
      setChecks([])
    } finally {
      setFetchedAt(Date.now())
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    if (!env) void refresh()
  }, [env, refresh])
  useEffect(() => {
    void load()
  }, [load])

  const failing = (checks ?? []).filter((c) => !c.ok)
  const passing = (checks ?? []).filter((c) => c.ok)

  return (
    <div className="flex flex-col gap-4" data-diagnostics-page>
      <SettingSection title={st('diagnostics.healthTitle')}>
        {checks === null ? (
          <p className="text-xs text-ink-3">{st('about.detecting')}</p>
        ) : (
          <>
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <p className={cn('text-xs', failing.length ? 'text-ink' : 'text-ink-2')}>
                {failing.length
                  ? st('diagnostics.summaryFailing', { count: failing.length })
                  : st('diagnostics.summaryOk')}
              </p>
              {fetchedAt !== null && (
                <span className="text-xs text-ink-3">
                  {st('diagnostics.fetchedAt', { time: formatDateTime(fetchedAt) })}
                </span>
              )}
              <Button variant="ghost" size="sm" loading={busy} onClick={() => void load()}>
                <RefreshCw size={12} aria-hidden />
                {st('diagnostics.refetch')}
              </Button>
            </div>
            {/* 「运行环境检查通过」很容易被读成"图没问题"（审计 T47） */}
            <p className="text-xs leading-relaxed text-ink-3">{st('diagnostics.scopeNote')}</p>
            {/* 异常项常驻首屏；正常项折叠——它们在「技术详情」里还有一份带
                取值的，铺在首屏等于同一件事说两遍。 */}
            {failing.length > 0 && (
              <ul className="flex flex-col gap-1">
                {failing.map((c) => (
                  <CheckLine key={c.id} check={c} />
                ))}
              </ul>
            )}
            {passing.length > 0 && (
              <DiagnosticDisclosure title={st('diagnostics.okDetails')}>
                <ul className="flex flex-col gap-1">
                  {passing.map((c) => (
                    <CheckLine key={c.id} check={c} />
                  ))}
                </ul>
              </DiagnosticDisclosure>
            )}
          </>
        )}
        {/* 缺件 / 损坏：恢复入口整张常驻（那时它给的是「自动安装 / 换解释器」） */}
        {env && !env.ok && <EngineEnvironmentCard />}
      </SettingSection>

      <SettingSection title={st('diagnostics.reportTitle')}>
        <CopySummary
          trailing={
            <>
              <DiagnosticsExportButton />
              <HelpTip label={st('about.diagnosticsHelpAria')}>
                <p>
                  {st('about.diagnosticsHintBefore')}
                  <strong className="font-medium text-ink">{st('about.diagnosticsHintStrong')}</strong>
                  {st('about.diagnosticsHintAfter')}
                </p>
              </HelpTip>
            </>
          }
        />
      </SettingSection>

      {/* 技术详情：来源 / 版本 / 完整路径 / 换解释器。默认折叠 */}
      <DiagnosticDisclosure title={st('techDetails')}>
        {env?.ok && (
          <>
            <DiagnosticItem
              name={st('about.engineStatus')}
              value={en(`sourceLabel.${env.source || 'unknown'}`, { product: PRODUCT_NAME })}
            />
            <DiagnosticItem name="matplotlib" value={env.matplotlib ?? '—'} />
            {/* 两个环境曾经同名，于是两页的状态看起来对不上（审计 T47） */}
            <p className="text-xs leading-relaxed text-ink-3">
              {st('diagnostics.envNote', { product: PRODUCT_NAME })}
            </p>
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

/** 一条检查：状态点 + 名字（+ 坏了时的原因，诊断数据不翻）。 */
function CheckLine({ check: c }: { check: Check }) {
  useTranslation('dialogs')
  return (
    <li className="flex items-start gap-1.5 text-xs">
      <span
        aria-hidden
        className={cn('mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full', c.ok ? 'bg-ink-3' : 'bg-danger')}
      />
      <span className="sr-only">{st(c.ok ? 'about.checkOk' : 'about.checkFail')}</span>
      <span className="shrink-0 text-ink-2">
        {translate(`settings.about.check.${c.id}`, { ns: 'dialogs', defaultValue: c.label })}
      </span>
      {!c.ok && <span className="min-w-0 flex-1 break-all font-mono text-ink-3">{c.detail}</span>}
    </li>
  )
}

/**
 * 「复制诊断」：先把脱敏后的文本摆出来，用户看过再复制。
 * 文本由后端 `/api/diagnostics/summary` 给（与诊断包同一份采集、同一道脱敏），
 * 前端不再自己拼一份——拼一份就是第二个采集出处。
 */
function CopySummary({ trailing }: { trailing?: ReactNode }) {
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
    <div className="flex min-w-0 flex-col gap-1.5">
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
        {trailing}
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
