import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { fetchConvertAutoInstallEnvs, revokeConvertAutoInstallEnv, type ConvertAutoInstallEnv } from '@/lib/api'
import { Button } from '../ui/Button'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { SettingRow } from './SettingRow'

/** 本页文案在 dialogs:settings.packages.autoInstall.* 下 */
const ai = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.packages.autoInstall.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 环境的可读名：`conda: myenv` / `python: myenv`（后端只存种类 + 目录名，没有路径） */
export function autoInstallEnvLabel(env: Pick<ConvertAutoInstallEnv, 'kind' | 'name'>): string {
  const prefix = env.kind === 'conda' ? 'conda' : env.kind === 'project_venv' ? 'venv' : 'python'
  return `${prefix}: ${env.name}`
}

/**
 * 设置 → Python 库 →「已允许自动装包的环境」（ADR 0143 修订）：「生成可编辑版本」在某个环境里第一次缺包时问过一次、
 * 用户点了“装，以后这个环境都自动装”的环境都在这里；每行一个「撤销」，撤销后这个环境再缺包会重新问。
 * 一句话 + 每行一个按钮；没有任何环境时只有一句说明。
 */
export function AutoInstallEnvsSection() {
  useTranslation('dialogs')
  const [envs, setEnvs] = useState<ConvertAutoInstallEnv[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetchConvertAutoInstallEnvs()
      .then((r) => alive && setEnvs(r.envs))
      .catch(() => alive && setEnvs([]))
    return () => {
      alive = false
    }
  }, [])

  const revoke = async (key: string) => {
    setBusy(key)
    try {
      setEnvs((await revokeConvertAutoInstallEnv(key)).envs)
    } catch {
      /* 撤销失败：列表保持原样，用户可再点一次 */
    } finally {
      setBusy(null)
    }
  }

  return (
    <FormSection title={ai('title')} data-settings-anchor="packages.autoInstall" data-auto-install-envs>
      <FieldGroup>
        {envs && envs.length > 0 ? (
          envs.map((env) => (
            <SettingRow key={env.key} label={autoInstallEnvLabel(env)} data-auto-install-env={env.key}>
              <Button
                variant="secondary"
                size="md"
                data-auto-install-revoke={env.key}
                disabled={busy === env.key}
                onClick={() => void revoke(env.key)}
              >
                {ai('revoke')}
              </Button>
            </SettingRow>
          ))
        ) : (
          <p className="type-caption" data-auto-install-empty>
            {envs === null ? ai('loading') : ai('empty')}
          </p>
        )}
      </FieldGroup>
    </FormSection>
  )
}
