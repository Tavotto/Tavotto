import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { postDiagnosticsBundle } from '@/lib/api'
import { buildDiagnosticPayload } from '@/diagnostics'
import { PRODUCT_NAME } from '@/lib/brand'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUpdateStore } from '@/store/updateStore'
import { BrandMark } from '../ui/BrandMark'
import { Button } from '../ui/Button'
import { Toggle } from '../ui/Toggle'
import { InlineWarning, SettingRow, SettingSection } from './SettingRow'

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
 *   2. 隐私与匿名数据——**最短摘要常驻**，「会发送什么 / 绝不发送什么」进问号。
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
      <BrandMark size={54} variant="full" />
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
            className="text-accent hover:underline"
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
 * 属于隐私授权，不许折叠。完整的「会发送什么」「绝不发送什么」进问号：
 * 那两段是清单，读一次就够，不该每次打开设置都占半屏。
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
  return (
    <SettingSection title={st('about.privacyTitle')}>
      <SettingRow
        label={st('about.telemetry.title')}
        helpLabel={st('about.telemetry.helpAria')}
        help={
          <>
            {/*
              「跨会话稳定」这一句要突出：它是这段话诚实性的关键——没有它，读者会
              以为每次启动都是全新的匿名身份，而我们确实靠它算留存。
              **强调走 JSX 的 <strong>，不是文案里的 Markdown `**`**：这些是纯文本
              插值，不是 Markdown 渲染器。
            */}
            <p>
              {st('about.telemetry.sendsBefore')}
              <strong className="font-medium text-ink">{st('about.telemetry.sendsPersist')}</strong>
              {st('about.telemetry.sendsAfter')}
            </p>
            <p>
              <strong className="font-medium text-ink">{st('about.telemetry.neverLabel')}</strong>
              {st('about.telemetry.never')}
            </p>
            {/* 「本机优先」这条完整承诺原来常驻在首屏，现在收进来读一次就够 */}
            <p>{st('about.privacy')}</p>
          </>
        }
        status={st(settings?.enabled ? 'about.telemetry.on' : 'about.telemetry.off')}
      >
        <Toggle
          checked={settings?.enabled ?? false}
          // 管理员关掉时开关是死的：还能点的话用户会以为自己打开了，
          // 而实际上一个字节都不会发
          disabled={hard || !settings}
          aria-label={st('about.telemetry.toggle')}
          onChange={(v) => void choose(v ? 'enabled' : 'disabled', 'settings')}
        />
      </SettingRow>
      {/* 一句话摘要：常驻。这是隐私承诺，不是说明文字 */}
      <p className="text-xs leading-relaxed text-ink-3">{st('about.telemetry.summary')}</p>
      {hard && <InlineWarning>{st('about.telemetry.hardDisabled')}</InlineWarning>}
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
