import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { fetchBuildVersion, type TelemetrySettings } from '@/lib/api'
import { PRIVACY_DOC_URL, PRODUCT_NAME, REPO_URL } from '@/lib/brand'
import { TELEMETRY_DISCLOSED_EVENTS } from '@/lib/telemetryDisclosure'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUpdateStore } from '@/store/updateStore'
import { BrandMark } from '../ui/BrandMark'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { Toggle } from '../ui/Toggle'
import { DiagnosticDisclosure, SettingRow } from './SettingRow'
import { UpdateSettings } from './UpdateSettings'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 隐私、诊断与 About。
 *
 * 修改前这一页同时承担品牌、隐私长文、遥测说明、许可证、渲染环境（含**完整
 * 解释器绝对路径**）、CLI 状态和五条诊断项，全部平铺在首屏
 * （before/zh-1440-settings-about.png）。
 *
 * 现在这一页是三块（导航 id 仍是 `about`，不动 schema；2026-09-30 并进了「更新」）：
 *   1. 产品与版本（页首：标志 + 名字 + 版本 + 许可）；
 *   2. 更新（`UpdateSettings`：自动检查 / 检查更新 / 有新版本时一条 accent Notice + 32px 主按钮）；
 *   3. 隐私与数据——**最短摘要常驻**，逐条数据清单进默认折叠的
 *      「会发送哪些数据」（审计 T49；此前那两段清单在小问号里，且已经与
 *      后端 `EVENTS` 表漂开了九条事件）。
 *
 * 渲染环境、健康检查、诊断包在 Session 19 起搬到了独立的「诊断」分区
 * （`DiagnosticsSettings.tsx`，ADR 0038）；内置包版本搬到了「包管理」。
 * 不许被折叠的：遥测开关本身、硬开关生效时的那句话。
 */
export function PrivacyAboutSettings() {
  useTranslation('dialogs')
  const statusVersion = useUpdateStore((s) => s.status?.current)
  // 更新接口失败时 status 取不到——那时恰恰最需要看到版本号（排错、报 bug）。
  // 退回 `/api/version`（后端同一个 `current_version()`），status 取到之后仍以它为准
  const [fallbackVersion, setFallbackVersion] = useState<string | undefined>()
  useEffect(() => {
    if (statusVersion) return
    let live = true
    fetchBuildVersion()
      .then((r) => live && setFallbackVersion(r.version))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [statusVersion])
  const version = statusVersion ?? fallbackVersion
  // 分区之间的间距由外壳统一给（`display: contents`）
  return (
    <div className="contents">
      <ProductBlock version={version} />
      {/* 「更新」并进这一页（2026-09-30）：版本在上、检查更新紧随其后，再是隐私 */}
      <UpdateSettings />
      <PrivacyBlock />
    </div>
  )
}

function ProductBlock({ version }: { version?: string }) {
  return (
    <div className="flex items-center gap-4">
      {/* About 是标志唯一允许的 full 档界面位置（54px，弹窗白底用默认灰） */}
      <BrandMark size={54} />
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="type-title">
          {PRODUCT_NAME}
          {version && <span className="ml-1.5 font-mono text-sm font-normal text-ink-2">v{version}</span>}
        </p>
        <p className="type-caption">{st('about.tagline')}</p>
        <p className="type-meta">
          {st('about.licenseBefore')}{' '}
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
            // 正文句子里的链接必须**不靠颜色**也能认出来（axe
            // link-in-text-block，serious）——只在悬停时下划线等于对色觉障碍
            // 与灰度打印一律无效。这一页此前从没被 axe 跑过，所以一直没人看见
            className="text-ink-2 underline underline-offset-2 hover:text-ink"
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
 * 隐私与数据。
 *
 * **最短摘要必须常驻**——它是用户判断「这东西会不会上传我的图」的依据，
 * 属于隐私授权，不许折叠。
 *
 * ① **同意是三档，界面也得说得出三档。** 控件是一个滑动开关（`role="switch"`，只表达开 / 关），可写的只有
 *    开 / 关两档（回不到 unset 是对的，表过态就是表过态）。行上的现状**只说开关说不出的那两种**
 *    （2026-10-07 设计审计 §9.1：此前现状把开关的「开启 / 关闭」再念一遍）：
 *      * `unset` —— 「尚未选择」：还没问过，不是用户说了不；
 *      * 同意过、但同意的是上一版采集范围（后端升了 `CONSENT_VERSION`）—— 「待重新确认」：此刻一个字节都不发，
 *        开关画成关，不能画成「已开启」。
 *    于是三档仍然可辨：开着 = 同意；关着且没有现状 = 拒绝；关着且写着「尚未选择」= 还没问过。
 *    硬开关那一档的第一层是「已由本机配置关闭」，环境变量名是第二层。
 *
 * ② **「会发送什么」不再是一段会过期的散文。** 见 `lib/telemetryDisclosure.ts`：
 *    每条事件一行，与后端 `EVENTS` 表严格同源，默认折叠（组里一行原地展开）。
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
  // **陈旧**的 GET 响应把它连同 `lib/telemetry` 的缓存一起覆盖掉（评审 #300-4）。
  const pending = !settings
  const enabled = settings?.consent === 'enabled' && !settings.needs_reconsent
  return (
    <FormSection title={st('about.privacyTitle')} data-settings-anchor="about.telemetry">
      <FieldGroup>
        {/* 一句话摘要是这一行的说明（常驻，不折叠）：它是隐私承诺，不是说明文字 */}
        <SettingRow
          label={st('about.telemetry.title')}
          description={st('about.telemetry.summary')}
          status={
            hard ? (
              <>
                {st('about.telemetry.hardDisabled')}
                <span className="type-meta block" data-telemetry-hard-detail>
                  {st('about.telemetry.hardDisabledDetail', { env: 'TAVOTTO_NO_TELEMETRY=1' })}
                </span>
              </>
            ) : (
              consentStatus(settings)
            )
          }
        >
          {/* 滑动开关只表达开 / 关（unset 与待重新确认都画成关），开关说不出的那两种由
              行内现状那句话说。`choose` 只收得到开 / 关两档，所以界面上说得出 unset，却写不回 unset */}
          <Toggle
            checked={enabled}
            aria-label={st('about.telemetry.toggle')}
            disabled={hard || pending}
            onChange={(next) => void choose(next ? 'enabled' : 'disabled', 'settings')}
          />
        </SettingRow>
        <TelemetryDataDisclosure />
        <div>
          <a
            href={PRIVACY_DOC_URL}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-ink-2 underline underline-offset-2 hover:text-ink"
          >
            {st('about.telemetry.policy')}
          </a>
        </div>
      </FieldGroup>
    </FormSection>
  )
}

/**
 * 行内现状：**只说开关说不出的那两种**（见上）。开着 / 关着就是开关本身，不再念一遍。
 * 硬开关那一档不在这里：它有自己那句常驻的话，说的是「不是你关的」。
 */
function consentStatus(settings: TelemetrySettings | null): string | undefined {
  if (!settings) return undefined
  if (settings.consent === 'unset') return st('about.telemetry.unset')
  if (settings.consent === 'enabled' && settings.needs_reconsent) return st('about.telemetry.needsReconsent')
  return undefined
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
    <DiagnosticDisclosure variant="row" data-privacy-disclosure title={st('about.telemetry.detailsTitle')}>
      <p className="type-caption">{st('about.telemetry.autoProps')}</p>
      <p className="type-caption">
        {st('about.telemetry.sendsBefore')}
        <strong className="font-medium text-ink">{st('about.telemetry.sendsPersist')}</strong>
        {st('about.telemetry.sendsAfter')}
      </p>
      <ul
        data-telemetry-disclosure
        className="type-caption flex list-inside list-disc flex-col gap-0.5"
      >
        {TELEMETRY_DISCLOSED_EVENTS.map((event) => (
          <li key={event} data-telemetry-event={event}>
            {st(`about.telemetry.sends.${event}`)}
          </li>
        ))}
      </ul>
      <p className="type-caption">
        <strong className="font-medium text-ink">{st('about.telemetry.neverLabel')}</strong>
        {st('about.telemetry.never')}
      </p>
      {/* 「本机优先」这条完整承诺 */}
      <p data-privacy-network-summary className="type-caption">{st('about.privacy')}</p>
    </DiagnosticDisclosure>
  )
}
