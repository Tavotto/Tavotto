import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { fetchDiagnosticsSummary, postDiagnosticsBundle } from '@/lib/api'
import { buildDiagnosticPayload } from '@/diagnostics'
import { formatDateTime } from '@/i18n/format'
import { PRODUCT_NAME } from '@/lib/brand'
import { apiUrl, withProject } from '@/lib/session'
import { usePerfProbeStore } from '@/perf/probeStore'
import { useUiStore } from '@/store/uiStore'
import { useEnvStore } from '@/store/envStore'
import { EngineEnvironmentCard } from '../EngineEnvironmentCard'
import { RefreshCw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button } from '../ui/Button'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { StatusPill } from '../ui/StatusPill'
import { CopyButton } from './CopyButton'
import { PathValue } from './PathValue'
import { DiagnosticDisclosure, DiagnosticItem, SettingRow } from './SettingRow'

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
 * 设置 → 帮助与诊断（原「诊断」，ADR 0038）。
 *
 * **顺序：健康 → 报告 → 开发者**（2026-10-07 设计审计 §9.1 P0：「环境是否正常」此前排在最后）。与 #797 的
 * 「异步结果不挪动正在按的入口」同时成立的办法：**结论一行高度不变**——取数中与取数后是同一行（「运行环境」+
 * 一行现状 + 控件列），只把现状那句话从「正在检测…」换成「本页数据取自 …」、控件列多一枚结论胶囊；会随结果
 * 长高的东西（异常项逐条、各项检查结果、恢复卡）全部在**最后**的「检查结果」组里，报告与开发者两组的入口
 * 一个像素都不动（`e2e/perf-probe.spec.ts` 的 clickAcrossUpdate 量它）。
 *
 * 首屏只有三件事：**健康结论**、**诊断报告**（导出诊断包；复制诊断先预览脱敏后的文本，再复制）、**给开发者**
 * （记录拖动性能 + 技术详情，默认折叠）。渲染环境不正常时恢复入口整组常驻（那是缺件，不许折叠）。用哪个 Python、
 * 脚本运行目录等运行设置在「项目」页；内置包版本在「Python 库」，这里不重复。
 *
 * 审计 T47 的四条照旧：正常项默认折叠、说清检查的边界、两个环境不再同名、说清这一页的数据什么时候取的
 * （`/api/diagnostics` 没有服务端时间戳，说的是**本页取数的时刻**——解释器选择在后端是进程级缓存，重取不会重挑）。
 */
export function DiagnosticsSettings() {
  useTranslation('dialogs')
  const { env, refresh } = useEnvStore()
  const [checks, setChecks] = useState<Check[] | null>(null)
  /** 本页取数的时刻。**不叫「最近检测」**：后端那几条里有进程级缓存的，
   *  重取不等于重挑解释器，说成"刚检测过"就是在替它担保。 */
  const [fetchedAt, setFetchedAt] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  /** 取数失败（网络 / 鉴权 / 后端出错，或回包里没有 checks）。**不能当成「零项异常」**：那会在拿不到结果时
   *  亮出「检查通过」的绿胶囊（Codex #828 P2） */
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const r = await fetch(apiUrl('/api/diagnostics'), withProject())
      const d = r.ok ? await r.json() : null
      if (!Array.isArray(d?.checks)) throw new Error('diagnostics unavailable')
      setChecks((d.checks as Check[]).filter((c) => !DUPLICATED_ELSEWHERE.test(c.id)))
      setFetchedAt(Date.now())
      setFailed(false)
    } catch {
      setChecks([])
      setFailed(true)
    } finally {
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
  /** 恢复入口此刻在不在这一屏上——「下一步」指得着它才说得出口 */
  const repairCard = !!env && !env.ok
  const showResults = checks !== null && (failing.length > 0 || passing.length > 0)

  return (
    <div className="contents" data-diagnostics-page>
      {/* ---------------- 健康：一行结论（高度不随结果变） ---------------- */}
      <FormSection title={st('diagnostics.healthTitle')} data-settings-anchor="diagnostics.health" data-diagnostics-health>
        <FieldGroup>
          <SettingRow
            label={st('diagnostics.verdictLabel')}
            status={
              checks === null ? (
                <span data-diagnostics-loading>{st('about.detecting')}</span>
              ) : failed ? (
                st('diagnostics.fetchFailed')
              ) : fetchedAt !== null ? (
                st('diagnostics.fetchedAt', { time: formatDateTime(fetchedAt) })
              ) : undefined
            }
          >
            {checks !== null && (
              <StatusPill data-diagnostics-summary tone={failed ? 'warn' : failing.length ? 'danger' : 'ok'} dot>
                {failed
                  ? st('diagnostics.summaryUnavailable')
                  : failing.length
                  ? st('diagnostics.summaryFailing', { count: failing.length })
                  : st('diagnostics.summaryOk')}
              </StatusPill>
            )}
            <Button
              data-diagnostics-refetch
              variant="ghost"
              size="sm"
              loading={busy}
              onClick={() => void load()}
            >
              <RefreshCw size={ICON_SIZE.sm} aria-hidden />
              {st('diagnostics.refetch')}
            </Button>
          </SettingRow>
        </FieldGroup>
      </FormSection>

      <DiagnosticsReportSection />

      {/* ---------------- 给开发者：记录拖动性能 + 技术详情（默认折叠） ---------------- */}
      <FieldGroup data-settings-anchor="diagnostics.dev">
        <DiagnosticDisclosure variant="row" title={st('diagnostics.devTitle')} data-diagnostics-dev>
          <PerfProbeRow />
          {env?.ok && (
            <>
              <DiagnosticItem
                name={st('about.engineStatus')}
                value={en(`sourceLabel.${env.source || 'unknown'}`, { product: PRODUCT_NAME })}
              />
              <DiagnosticItem name="matplotlib" value={env.matplotlib ?? '—'} />
              {/* 两个环境曾经同名，于是两页的状态看起来对不上（审计 T47） */}
              <p className="type-caption">{st('diagnostics.envNote', { product: PRODUCT_NAME })}</p>
            </>
          )}
          {(checks ?? [])
            .filter((c) => c.ok && c.detail)
            .map((c) => (
              <DiagnosticItem
                key={c.id}
                name={checkLabel(c)}
                value={DIR_DETAIL_CHECKS.has(c.id) ? <PathValue path={c.detail} name={checkLabel(c)} /> : c.detail}
              />
            ))}
        </DiagnosticDisclosure>
      </FieldGroup>

      {/* ---------------- 检查结果：会随结果长高的都在这里（最后一组） ---------------- */}
      {(showResults || repairCard) && (
        <FormSection title={st('diagnostics.resultsTitle')}>
          {showResults && (
            <FieldGroup data-diagnostics-failures={failing.length ? '' : undefined}>
              {/* 异常项一条一行、常驻；正常项收成一行原地展开——它们在「给开发者」里还有一份带取值的 */}
              {failing.map((c) => (
                <FailingCheckRow key={c.id} check={c} repairCard={repairCard} />
              ))}
              {passing.length > 0 && (
                <DiagnosticDisclosure
                  variant="row"
                  title={st('diagnostics.okDetails')}
                  value={st('diagnostics.okCount', { count: passing.length })}
                >
                  <ul className="flex flex-col gap-1">
                    {passing.map((c) => (
                      <li key={c.id} className="flex items-center gap-1.5 text-sm">
                        <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-ok" />
                        <span className="sr-only">{st('about.checkOk')}</span>
                        <span className="text-ink-2">{checkLabel(c)}</span>
                      </li>
                    ))}
                  </ul>
                </DiagnosticDisclosure>
              )}
            </FieldGroup>
          )}
          {/* 缺件 / 损坏：恢复入口整组常驻（那时它给的是「自动安装 / 换解释器」） */}
          {repairCard && <EngineEnvironmentCard />}
        </FormSection>
      )}
    </div>
  )
}

/** 渲染引擎那一族：它们的下一步都指向这一页上的恢复入口。 */
const ENGINE_CHECKS = new Set(['worker_python', 'matplotlib', 'bundled_runtime'])

/**
 * `detail` 是一条**裸的目录路径**的那几项——只有它们能交给 `PathValue`
 * （末级目录 + 展开看全文 + 复制，与写回确认框同一份实现）。
 *
 * 这是一张**点名的表，不是形状猜测**：`worker_python` 的 detail 是
 * `路径（来源）`、`bundled_runtime` 是 `Python 3.13 + 14 个包`，拿末级目录去
 * 截它们只会截出半句话。后端那几行在 `app.py::api_diagnostics`。
 */
const DIR_DETAIL_CHECKS = new Set(['project_readable', 'project_writable'])

/**
 * 异常项的下一步（审计 T47）。
 *
 * **只登记确实说得出真实动作的那几条**：说不出来就不说——编一句「请检查配置」
 * 比不说更坏，它让人以为自己漏了什么。`registry_conflicts` 现在就没有登记，
 * 那条的处置路径我没能在代码里确认下来。
 *
 * 渲染引擎那三条指向的是这一页上的恢复入口，所以**只在它真的在的时候才说**
 * ——指着一个不存在的东西，比不给下一步更糟。
 */
function nextStepOf(id: string, repairCardVisible: boolean): string | null {
  if (ENGINE_CHECKS.has(id)) return repairCardVisible ? st('diagnostics.nextStep.engine') : null
  const text = translate(`settings.diagnostics.nextStep.${id}`, { ns: 'dialogs', defaultValue: '' })
  return text || null
}

/** 检查项的名字：登记过的翻，没登记的用后端给的那句（诊断数据，不翻）。 */
const checkLabel = (c: Check): string =>
  translate(`settings.about.check.${c.id}`, { ns: 'dialogs', defaultValue: c.label })

/**
 * 一条异常：名字 + 原因（诊断数据，不翻）+ 下一步（说明一行）+ 控件列一枚「异常」胶囊。
 * 目录类的原因走 `PathValue`（末级目录 + 展开 + 复制），在 fill 行里——现状槽只放文字。
 */
function FailingCheckRow({ check: c, repairCard }: { check: Check; repairCard: boolean }) {
  useTranslation('dialogs')
  const next = nextStepOf(c.id, repairCard)
  const dir = DIR_DETAIL_CHECKS.has(c.id)
  return (
    <SettingRow
      label={checkLabel(c)}
      data-diagnostics-check={c.id}
      status={dir ? undefined : <span className="break-all font-mono text-xs">{c.detail}</span>}
      description={next ? <span data-next-step>{next}</span> : undefined}
      below={dir ? <PathValue path={c.detail} name={checkLabel(c)} /> : undefined}
    >
      <StatusPill tone="danger">{st('about.checkFail')}</StatusPill>
    </SettingRow>
  )
}

/**
 * 性能分析（ADR 0075）：点「开始」关掉设置、在画布上挂出探针面板。报告只含
 * 数字，由用户自己保存、自己决定发给谁。
 */
function PerfProbeRow() {
  useTranslation('dialogs')
  const start = () => {
    if (usePerfProbeStore.getState().start()) useUiStore.getState().setSettingsOpen(false)
  }
  return (
    <SettingRow label={st('diagnostics.perfRow')} data-settings-anchor="diagnostics.perf">
      <Button variant="secondary" size="sm" onClick={start} data-perf-probe-start>
        {st('diagnostics.perfStart')}
      </Button>
    </SettingRow>
  )
}

/**
 * 诊断包（ADR 0016）。
 *
 * 以前是「给浏览器一个链接让它自己下」，现在必须走 POST：前端状态与交互轨迹
 * 只活在浏览器内存里，得随请求现交上去。代价是 zip 要过一遍前端内存——
 * 它只有几十到几百 KB，可以接受。
 *
 * **载荷是现采的**：点这个按钮之前，什么都没有被序列化过。
 */
async function downloadDiagnostics(): Promise<void> {
  const blob = await postDiagnosticsBundle(buildDiagnosticPayload())
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = `tavotto-diagnostics-${stampForFilename()}.zip`
    a.click()
  } finally {
    // 不撤销就是一条挂到刷新为止的引用，而 zip 全在内存里
    URL.revokeObjectURL(url)
  }
}

/** 本地时间的 YYYYMMDD-HHMMSS，与后端给的 Content-Disposition 同一形状 */
function stampForFilename(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
    `-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
  )
}

/**
 * 「诊断报告」组：两行。
 *
 *   * **诊断包**：导出（secondary）；结果（「诊断包已生成」/ 失败的人话）就是这一行的现状——此前它挤在分区头的
 *     按钮组里（2026-10-07 设计审计 §9.1）。问号说清包里有什么、没有什么。
 *   * **诊断摘要**：「复制诊断」先把脱敏后的文本摆出来，用户看过再复制；预览是这一行的 fill 行。文本由后端
 *     `/api/diagnostics/summary` 给（与诊断包同一份采集、同一道脱敏），前端不再自己拼一份。
 */
function DiagnosticsReportSection() {
  useTranslation('dialogs')
  const [phase, setPhase] = useState<'idle' | 'busy' | 'ready' | 'error'>('idle')
  const [text, setText] = useState('')
  const [bundle, setBundle] = useState<'idle' | 'busy' | 'done' | 'error'>('idle')
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
  const exportBundle = () => {
    setBundle('busy')
    void downloadDiagnostics()
      .then(() => setBundle('done'))
      .catch(() => setBundle('error'))
  }
  return (
    <FormSection title={st('diagnostics.reportTitle')} data-settings-anchor="diagnostics.report">
      <FieldGroup>
        <SettingRow
          label={st('diagnostics.bundleLabel')}
          help={
            <p>
              {st('about.diagnosticsHintBefore')}
              <strong className="font-medium text-ink">{st('about.diagnosticsHintStrong')}</strong>
              {st('about.diagnosticsHintAfter')}
            </p>
          }
          helpLabel={st('about.diagnosticsHelpAria')}
          status={
            bundle === 'done' ? (
              <span role="status">{st('about.exported')}</span>
            ) : bundle === 'error' ? (
              <span role="alert" className="text-danger-content">
                {st('about.exportFailed')}
              </span>
            ) : undefined
          }
          data-diagnostics-bundle
        >
          <Button variant="secondary" size="sm" onClick={exportBundle} disabled={bundle === 'busy'}>
            {bundle === 'busy' ? st('about.exporting') : st('about.exportBundle')}
          </Button>
        </SettingRow>
        <SettingRow
          label={st('diagnostics.summaryLabel')}
          status={
            phase === 'error' ? (
              <span role="alert" className="text-danger-content">
                {st('diagnostics.prepareFailed')}
              </span>
            ) : undefined
          }
          below={
            phase === 'ready' ? (
              <div className="flex flex-col gap-1" data-diagnostics-preview>
                <p className="type-caption">{st('diagnostics.previewNote')}</p>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface p-2 font-mono text-xs leading-relaxed text-ink-3">
                  {text}
                </pre>
              </div>
            ) : undefined
          }
        >
          {phase !== 'ready' ? (
            <Button variant="ghost" size="sm" onClick={() => void prepare()} disabled={phase === 'busy'}>
              {phase === 'busy' ? st('diagnostics.preparing') : st('diagnostics.copyReport')}
            </Button>
          ) : (
            <>
              <Button variant="ghost" size="sm" onClick={() => setPhase('idle')}>
                {st('diagnostics.hidePreview')}
              </Button>
              <CopyButton text={text} label={st('diagnostics.copyReport')} variant="secondary" />
            </>
          )}
        </SettingRow>
      </FieldGroup>
    </FormSection>
  )
}
