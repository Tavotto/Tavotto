import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { postDiagnosticsBundle, type TelemetrySettings } from '@/lib/api'
import { buildDiagnosticPayload } from '@/diagnostics'
import { PRODUCT_NAME } from '@/lib/brand'
import { TELEMETRY_DISCLOSED_EVENTS } from '@/lib/telemetryDisclosure'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUpdateStore } from '@/store/updateStore'
import { BrandMark } from '../ui/BrandMark'
import { Button } from '../ui/Button'
import {
  DiagnosticDisclosure,
  InlineWarning,
  SettingRow,
  SettingSection,
} from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 隐私、诊断与 About。
 *
 * 修改前这一页同时承担品牌、隐私长文、遥测说明、许可证、渲染环境（含**完整
 * 解释器绝对路径**）、CLI 状态和五条诊断项，全部平铺在首屏
 * （before/zh-1440-settings-about.png）。
 *
 * 现在这一页只有两块（导航 id 仍是 `about`，不动 schema）：
 *   1. 产品与版本；
 *   2. 隐私与匿名数据——**最短摘要常驻**，逐条数据清单进默认折叠的
 *      「会发送哪些数据」（审计 T49；此前那两段清单在小问号里，且已经与
 *      后端 `EVENTS` 表漂开了九条事件）。
 *
 * 渲染环境、健康检查、诊断包在 Session 19 起搬到了独立的「诊断」分区
 * （`DiagnosticsSettings.tsx`，ADR 0038）；内置包版本搬到了「包管理」。
 * 不许被折叠的：遥测开关本身、硬开关生效时的那句话。
 */
export function PrivacyAboutSettings() {
  useTranslation('dialogs')
  const version = useUpdateStore((s) => s.status?.current)
  return (
    <div className="flex flex-col gap-4">
      <ProductBlock version={version} />
      <PrivacyBlock />
    </div>
  )
}

function ProductBlock({ version }: { version?: string }) {
  return (
    <div className="flex items-center gap-3">
      {/* About 是标志唯一允许的 full 档界面位置（54px，弹窗白底用默认灰） */}
      <BrandMark size={54} />
      <div className="min-w-0">
        <p className="text-xs text-ink">
          {PRODUCT_NAME}
          {version && <span className="ml-1.5 font-mono text-ink-2">v{version}</span>}
        </p>
        <p className="mt-0.5 text-xs text-ink-3">{st('about.tagline')}</p>
        <p className="mt-1 text-xs text-ink-3">
          {st('about.licenseBefore')}{' '}
          <a
            href="https://github.com/Tavotto/Tavotto"
            target="_blank"
            rel="noreferrer"
            // 正文句子里的链接必须**不靠颜色**也能认出来（axe
            // link-in-text-block，serious）——只在悬停时下划线等于对色觉障碍
            // 与灰度打印一律无效。这一页此前从没被 axe 跑过，所以一直没人看见
            className="text-accent underline underline-offset-2"
          >
            {st('about.source')}
          </a>
          {st('about.licenseAfter')}
        </p>
      </div>
    </div>
  )
}

/**
 * 隐私与匿名数据。
 *
 * **最短摘要必须常驻**——它是用户判断「这东西会不会上传我的图」的依据，
 * 属于隐私授权，不许折叠。
 *
 * 两处在审计 T49 里改掉：
 *
 * ① **同意是三档，界面也得说得出三档。** 之前这里是个二值开关：`unset`（还没
 *    问过）与 `disabled`（问过了，用户说不）画出来一模一样。那正是后端刻意
 *    分开的两件事——只有前者才该弹询问，后者再弹就是骚扰——被界面重新合并
 *    了一次。现在控件是一个**滑动开关**（`role="switch"`，只表达开 / 关），
 *    当前状态由 `SettingRow.status` 那句话表达：开启 / 关闭 /
 *    尚未选择，三种可辨状态，而**可写的仍然只有开 / 关两档**（回不到 unset
 *    是对的，表过态就是表过态）。
 *    还有第四种情形不能画成「已开启」：同意过、但同意的是上一版采集范围
 *    （后端升了 `CONSENT_VERSION`），此刻一个字节都不发。
 *
 * ② **「会发送什么」不再是一段会过期的散文。** 见 `lib/telemetryDisclosure.ts`：
 *    每条事件一行，与后端 `EVENTS` 表严格同源，默认折叠。
 */
function PrivacyBlock() {
  useTranslation('dialogs')
  const settings = useTelemetryStore((s) => s.settings)
  const choose = useTelemetryStore((s) => s.choose)
  const load = useTelemetryStore((s) => s.load)
  useEffect(() => {
    if (!settings) void load()
  }, [settings, load])

  const hard = settings?.hard_disabled ?? false
  // 首次 `load()` 还在路上时 `settings` 是 null，`hard` 算出来是 false——两档
  // 都点得动。那一下会与在途的 GET 赛跑：PATCH 先回来写下同意态，随后那份
  // **陈旧**的 GET 响应把它连同 `lib/telemetry` 的缓存一起覆盖掉，界面与后端
  // 里刚存下的同意状态从此对不上。二值开关时代这里靠 `!settings` 显式禁用，
  // 换成三档 `Segmented` 时丢了这道守卫（评审 #300-4）。
  const pending = !settings
  const enabled = settings?.consent === 'enabled'
  return (
    <SettingSection title={st('about.privacyTitle')}>
      <SettingRow label={st('about.telemetry.title')} status={consentStatus(settings)}>
        {/* 滑动开关只表达开 / 关（unset 与待重新确认都画成关），完整状态由
            行内 status 那句话说。`choose` 只收得到开 / 关两档，所以界面上说
            得出 unset，却写不回 unset */}
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label={st('about.telemetry.toggle')}
          disabled={hard || pending}
          onClick={() => void choose(enabled ? 'disabled' : 'enabled', 'settings')}
          className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors duration-[var(--duration-fast)] focus-visible:focus-ring disabled:opacity-50 ${
            enabled ? 'bg-ink' : 'bg-border-strong'
          }`}
        >
          <span
            aria-hidden
            className={`inline-block size-3 rounded-full bg-surface transition-transform duration-[var(--duration-fast)] ${
              enabled ? 'translate-x-3.5' : 'translate-x-0.5'
            }`}
          />
        </button>
      </SettingRow>
      {/* 一句话摘要：常驻。这是隐私承诺，不是说明文字 */}
      <p className="text-xs leading-relaxed text-ink-3">{st('about.telemetry.summary')}</p>
      {hard && <InlineWarning>{st('about.telemetry.hardDisabled')}</InlineWarning>}
      <TelemetryDataDisclosure />
      <a
        href="https://github.com/Tavotto/Tavotto/blob/main/docs/privacy.md"
        target="_blank"
        rel="noreferrer"
        className="self-start text-xs text-accent hover:underline"
      >
        {st('about.telemetry.policy')}
      </a>
    </SettingSection>
  )
}

/**
 * 行内状态。控件是个二值开关，说不出 unset 与待重新确认，所以这里得把
 * 当前状态说全：
 *   * `unset` —— 得说清那是「还没问过」，不是「用户说了不」；
 *   * 同意过、但同意的是**上一版采集范围**（后端升了 `CONSENT_VERSION`）——
 *     此刻一个字节都不发，只写「开启」就是一句假话；
 *   * 其余两档如实写「开启」/「关闭」。
 * 硬开关那一档不在这里：它有自己那条常驻警示，说的是「不是你关的」。
 */
function consentStatus(settings: TelemetrySettings | null): string | undefined {
  if (!settings) return undefined
  if (settings.consent === 'unset') return st('about.telemetry.unset')
  if (settings.consent === 'enabled' && settings.needs_reconsent)
    return st('about.telemetry.needsReconsent')
  return settings.consent === 'enabled'
    ? st('about.telemetry.optIn')
    : st('about.telemetry.optOut')
}

/**
 * 「会发送哪些数据」。默认折叠——它是一张清单，读一次就够，不该每次打开设置
 * 都占半屏；但它必须**在同意之前就读得到**，所以留在这一页上而不是文档里。
 *
 * 列的是 `EVENTS` 的每一条，逐条说清它带的字段（见 `lib/telemetryDisclosure.ts`
 * 的同源约定）。「跨启动稳定」那句要突出：没有它，读者会以为每次启动都是全新
 * 的匿名身份，而我们确实靠它算留存。
 */
function TelemetryDataDisclosure() {
  useTranslation('dialogs')
  return (
    <DiagnosticDisclosure title={st('about.telemetry.detailsTitle')}>
      <p className="text-xs leading-relaxed text-ink-3">{st('about.telemetry.autoProps')}</p>
      <p className="text-xs leading-relaxed text-ink-3">
        {st('about.telemetry.sendsBefore')}
        <strong className="font-medium text-ink">{st('about.telemetry.sendsPersist')}</strong>
        {st('about.telemetry.sendsAfter')}
      </p>
      <ul
        data-telemetry-disclosure
        className="flex list-inside list-disc flex-col gap-0.5 text-xs leading-relaxed text-ink-3"
      >
        {TELEMETRY_DISCLOSED_EVENTS.map((event) => (
          <li key={event} data-telemetry-event={event}>
            {st(`about.telemetry.sends.${event}`)}
          </li>
        ))}
      </ul>
      <p className="text-xs leading-relaxed text-ink-3">
        <strong className="font-medium text-ink">{st('about.telemetry.neverLabel')}</strong>
        {st('about.telemetry.never')}
      </p>
      {/* 「本机优先」这条完整承诺 */}
      <p className="text-xs leading-relaxed text-ink-3">{st('about.privacy')}</p>
    </DiagnosticDisclosure>
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
export async function downloadDiagnostics(): Promise<void> {
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
 * 导出按钮。**点了要有反馈**——以前点完没有任何动静，用户不知道成没成；
 * 现在还多了一次真实的网络往返（要把前端状态交上去），沉默更难接受。
 * 失败给的是人话，不是 `POST /diagnostics 500`。
 */
export function DiagnosticsExportButton() {
  const [phase, setPhase] = useState<'idle' | 'busy' | 'done' | 'error'>('idle')
  const run = () => {
    setPhase('busy')
    void downloadDiagnostics()
      .then(() => setPhase('done'))
      .catch(() => setPhase('error'))
  }
  return (
    <>
      <Button variant="outline" size="sm" onClick={run} disabled={phase === 'busy'}>
        {phase === 'busy' ? st('about.exporting') : st('about.exportBundle')}
      </Button>
      {phase === 'done' && (
        <span className="text-xs text-ink-2" role="status">
          {st('about.exported')}
        </span>
      )}
      {phase === 'error' && (
        <span className="text-xs text-danger" role="alert">
          {st('about.exportFailed')}
        </span>
      )}
    </>
  )
}
