import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useEnvStore } from '@/store/envStore'
import { t as translate } from '@/i18n'
import type { EngineSource } from '@/lib/api'
import { Button } from './ui/Button'
import { TextInput } from './ui/Input'

/**
 * 渲染环境的状态与出口。
 *
 * 三种局面，给的东西完全不同：
 *
 *  1. **一切正常**（多数用户，尤其 Windows 桌面版——安装包自带内置环境）。
 *     `compact` 时什么都不显示：正常工作流里不该有一个常驻卡片提醒你「环境没问题」。
 *     设置页里显示一行状态 + 折叠起来的高级入口。
 *  2. **缺环境**（源码 / pip 安装，机器上没有科学栈）：给「自动安装」按钮。
 *  3. **内置环境缺失或损坏**（桌面版）：这不是用户的环境问题，是我们的安装包
 *     不完整——只能让他重装，绝不假装能现场修（embeddable 里连 pip 都没有）。
 *
 * 「用户脚本要的包内置环境里没有」是第四种，由渲染错误单独引导（见
 * MissingDependencyCard），不在这里处理——那时环境本身是好的。
 */

/** 本卡片的文案在 errors:engine.* 下 */
const en = (key: string, values?: Record<string, unknown>) =>
  translate(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

/** 后端给的是稳定的 source 枚举，人话在这里按当前语言取 */
const sourceLabel = (source: EngineSource): string =>
  en(`sourceLabel.${source || 'unknown'}`)

export function EngineEnvironmentCard({ compact }: { compact?: boolean }) {
  useTranslation('errors')
  const { env, log, installing, refresh, install, setPython } = useEnvStore()
  const [manual, setManual] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [advanced, setAdvanced] = useState(false)

  useEffect(() => {
    if (!env) void refresh()
  }, [env, refresh])

  if (!env) return null
  // 正常工作流里不制造多余提示：环境没问题时，紧凑位置（图内元素面板）什么都不显示
  if (env.ok && compact) return null

  const apply = async () => {
    const failure = await setPython(manual.trim() || null)
    setError(failure)
    if (!failure) setManual('')
  }

  const advancedBlock = (
    <div className="flex flex-col gap-1.5 border-t border-border pt-2.5">
      {advanced ? (
        <>
          <span className="text-xs text-ink-2">{en('useOther')}</span>
          <div className="flex items-center gap-1.5">
            <TextInput
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              placeholder={en('pathPlaceholder')}
              aria-label={en('pathAria')}
            />
            <Button onClick={() => void apply()}>{en('apply')}</Button>
          </div>
          <p className="text-xs leading-relaxed text-ink-3">
            {en('useOtherHintBefore')}
            <strong className="font-medium text-ink-2">{en('useOtherHintStrong')}</strong>
            {en('useOtherHintAfter')}
          </p>
          {error && <p className="text-xs text-danger">{error}</p>}
        </>
      ) : (
        <button
          type="button"
          onClick={() => setAdvanced(true)}
          className="self-start text-xs text-accent hover:underline"
        >
          {en('useOtherLink')}
        </button>
      )}
    </div>
  )

  // ---- 1. 一切正常 -------------------------------------------------------
  if (env.ok) {
    const label = sourceLabel(env.source)
    return (
      <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
        <div>
          <h3 className="text-xs font-medium text-ink">{en('okTitle')}</h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {label}
            {env.matplotlib && (
              <span className="ml-1.5 font-mono text-ink-3">
                {en('matplotlibVersion', { version: env.matplotlib })}
              </span>
            )}
          </p>
          {env.bundled ? (
            <p className="mt-1 text-xs leading-relaxed text-ink-3">{en('bundledHint')}</p>
          ) : (
            <p className="mt-1 break-all font-mono text-xs text-ink-3">{env.python}</p>
          )}
        </div>
        {env.bundled && Object.keys(env.runtime?.packages ?? {}).length > 0 && (
          <details className="text-xs text-ink-3">
            <summary className="cursor-pointer select-none text-ink-2">
              {en('bundledPackages')}
            </summary>
            <ul className="mt-1 flex flex-col gap-0.5 font-mono">
              {Object.entries(env.runtime.packages).map(([name, ver]) => (
                <li key={name}>
                  {name} {ver}
                </li>
              ))}
            </ul>
          </details>
        )}
        {!compact && advancedBlock}
      </div>
    )
  }

  // ---- 3. 内置环境缺失 / 损坏（桌面版）-----------------------------------
  if (env.runtime?.expected) {
    return (
      <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
        <div>
          <h3 className="text-xs font-medium text-ink">{en('incompleteTitle')}</h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {en('incompleteBefore')}
            {en(env.code === 'bundled_runtime_invalid' ? 'incompleteInvalid' : 'incompleteMissing')}
            {en('incompleteAfter')}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-ink-3">{en('incompleteHint')}</p>
        </div>
        {!compact && advancedBlock}
      </div>
    )
  }

  // ---- 2. 缺环境（源码 / pip 安装）---------------------------------------
  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
      <div>
        <h3 className="text-xs font-medium text-ink">{en('missingTitle')}</h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">{en('missingBody')}</p>
      </div>

      {env.can_install ? (
        <>
          <Button variant="primary" onClick={() => void install()} disabled={installing}>
            {en(installing ? 'installing' : 'autoInstall')}
          </Button>
          <p className="text-xs leading-relaxed text-ink-3">
            {en('autoInstallHintBefore')}
            <strong className="font-medium text-ink-2">{en('autoInstallHintStrong')}</strong>
            {en('autoInstallHintAfter')}
          </p>
        </>
      ) : (
        <p className="text-xs leading-relaxed text-danger">
          {en('noPythonBefore')}{' '}
          <a
            href="https://www.python.org/downloads/"
            target="_blank"
            rel="noreferrer"
            className="text-accent hover:underline"
          >
            {en('noPythonLink')}
          </a>
          {en('noPythonAfter')}
        </p>
      )}

      {log && (
        <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs text-ink-3">
          {log}
        </pre>
      )}

      {!compact && advancedBlock}
    </div>
  )
}

/**
 * 用户脚本 import 了当前渲染环境里没有的包。
 *
 * 这一档**刻意不提供「帮你装上」**：往内置环境里随便 pip install 会让它不再
 * 可复现，也让「重装就能修」这条退路失效。给的是另一个出口——换成用户自己
 * 那套已经装好这些包的科研环境。
 */
export function MissingDependencyCard({ module }: { module: string }) {
  useTranslation('errors')
  const { setPython } = useEnvStore()
  const [manual, setManual] = useState('')
  const [error, setError] = useState<string | null>(null)

  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
      <div>
        <h3 className="text-xs font-medium text-ink">
          {/* 包名是脚本里的标识符，原样显示 */}
          {en('missingModuleTitle', { module: module || en('missingModulePackage') })}
        </h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">{en('missingModuleBody')}</p>
      </div>
      <div className="flex items-center gap-1.5">
        <TextInput
          value={manual}
          onChange={(e) => setManual(e.target.value)}
          placeholder={en('pathPlaceholder')}
          aria-label={en('pathAria')}
        />
        <Button onClick={() => void setPython(manual.trim() || null).then(setError)}>
          {en('apply')}
        </Button>
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
      <p className="text-xs leading-relaxed text-ink-3">
        {en('missingModuleNoteBefore')}
        <strong className="font-medium text-ink-2">{en('missingModuleNoteStrong')}</strong>
        {en('missingModuleNoteAfter')}
      </p>
    </div>
  )
}
