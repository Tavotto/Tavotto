import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { apiUrl, withProject } from '@/lib/session'
import { PRODUCT_NAME } from '@/lib/brand'
import { useEnvStore } from '@/store/envStore'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUpdateStore } from '@/store/updateStore'
import { BrandMark } from '../ui/BrandMark'
import { Button } from '../ui/Button'
import { Toggle } from '../ui/Toggle'
import { EngineEnvironmentCard } from '../EngineEnvironmentCard'
import {
  DiagnosticDisclosure,
  DiagnosticItem,
  HelpTip,
  InlineWarning,
  SettingRow,
  SettingSection,
} from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })
const en = (key: string, values?: Record<string, unknown>) =>
  translate(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

/**
 * 隐私、诊断与 About。
 *
 * 修改前这一页同时承担品牌、隐私长文、遥测说明、许可证、渲染环境（含**完整
 * 解释器绝对路径**）、CLI 状态和五条诊断项，全部平铺在首屏
 * （before/zh-1440-settings-about.png）。
 *
 * 本轮拆成三块，页面内明显分区（导航 id 仍是 `about`，不动 schema）：
 *   1. 产品与版本；
 *   2. 隐私与匿名数据——**最短摘要常驻**，「会发送什么 / 绝不发送什么」进问号；
 *   3. 渲染环境（状态 + matplotlib 版本）+ 环境诊断折叠区（路径、包清单、
 *      CLI、诊断包）。
 *
 * 不许被折叠的：遥测开关本身、硬开关生效时的那句话、环境不正常时的恢复卡片。
 */
export function PrivacyAboutSettings() {
  useTranslation('dialogs')
  const version = useUpdateStore((s) => s.status?.current)
  return (
    <div className="flex flex-col gap-4">
      <ProductBlock version={version} />
      <PrivacyBlock />
      <EnvironmentBlock />
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
 * 渲染环境。正常时首屏只给「状态 + matplotlib 版本」两行；
 * **完整解释器路径、内置包清单、CLI 路径、诊断项全部进「环境诊断」折叠区**。
 *
 * 环境**不正常**时照旧把 `EngineEnvironmentCard` 整张摆出来——那时它给的是
 * 「自动安装 / 换一个解释器 / 重装」这类恢复动作，属于缺件，不许折叠。
 */
function EnvironmentBlock() {
  useTranslation('dialogs')
  const { env, refresh } = useEnvStore()
  const [checks, setChecks] = useState<
    { id: string; ok: boolean; label: string; detail: string }[] | null
  >(null)
  useEffect(() => {
    if (!env) void refresh()
  }, [env, refresh])
  useEffect(() => {
    void fetch(apiUrl('/api/diagnostics'), withProject())
      .then((r) => r.json())
      .then((d) => setChecks(d.checks ?? []))
      .catch(() => setChecks([]))
  }, [])

  return (
    <SettingSection title={st('about.environmentTitle')}>
      {env && !env.ok ? (
        // 缺件 / 损坏：恢复入口整张常驻
        <EngineEnvironmentCard />
      ) : (
        <>
          <SettingRow label={st('about.engineStatus')} help={st('about.engineStatusHint')}>
            <span className="text-xs text-ink-2">
              {env ? en(`sourceLabel.${env.source || 'unknown'}`) : '…'}
            </span>
            <span className="ml-auto shrink-0 text-xs text-ink-3">
              {env?.ok ? st('about.engineOk') : '…'}
            </span>
          </SettingRow>
          <SettingRow label="matplotlib">
            <span className="font-mono text-xs text-ink-2">{env?.matplotlib ?? '…'}</span>
          </SettingRow>
        </>
      )}

      <DiagnosticDisclosure
        title={st('about.diagnosticsTitle')}
        action={
          <span className="flex items-center gap-1">
            <HelpTip label={st('about.diagnosticsHelpAria')}>
              <p>
                {st('about.diagnosticsHintBefore')}
                <strong className="font-medium text-ink">{st('about.diagnosticsHintStrong')}</strong>
                {st('about.diagnosticsHintAfter')}
              </p>
            </HelpTip>
            <Button variant="outline" size="sm" onClick={downloadDiagnostics}>
              {st('about.exportBundle')}
            </Button>
          </span>
        }
      >
        {checks === null ? (
          <p className="text-xs text-ink-3">{st('about.detecting')}</p>
        ) : (
          checks.map((c) => (
            <DiagnosticItem
              key={c.id}
              ok={c.ok}
              /*
                后端给的是稳定 id + 中文 label；已知 id 在这里换成当前语言，
                没登记的 id 原样用后端那条（新增检查项不会变成空白）。
                detail 是诊断数据（路径 / 版本），刻意不翻。
              */
              name={translate(`settings.about.check.${c.id}`, {
                ns: 'dialogs',
                defaultValue: c.label,
              })}
              value={c.detail}
            />
          ))
        )}
        {/* 解释器绝对路径、内置包清单、「使用其他 Python 环境…」都在这张卡里。
            **只在这里出现一次**——上面首屏那两行是它的摘要，不是第二份实现 */}
        <div className="pt-1">
          <EngineEnvironmentCard />
        </div>
      </DiagnosticDisclosure>
    </SettingSection>
  )
}

/** 诊断包：交给浏览器直接下载，不经前端内存（zip 可能不小） */
function downloadDiagnostics() {
  const a = document.createElement('a')
  a.href = apiUrl('/api/diagnostics/bundle')
  a.download = ''
  a.click()
}
