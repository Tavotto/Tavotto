import type { ReactNode } from 'react'
import { t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import type { ConvertDependencyCard } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { Button } from './ui/Button'

const pt = (key: string, values?: Record<string, unknown>) =>
  translate(`prep.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** The same explicit authorization facts and actions for conversion and figure edits. */
export function dependencyDecision(
  card: ConvertDependencyCard,
  actions: { install: () => void; decline: () => void; chooseEnv: () => void },
): { title: string; primary?: ReactNode; ghost?: ReactNode } {
  const env = card.env.kind === 'managed'
    ? pt('convert.deps.env.managed', { product: PRODUCT_NAME })
    : `${card.env.kind === 'conda' ? 'conda' : card.env.kind === 'project_venv' ? 'venv' : 'python'}: ${card.env.name}`
  const decline = (
    <Button variant="ghost" size="md" data-convert-decline className="text-ink-2" onClick={actions.decline}>
      {pt('convert.deps.btn.decline')}
    </Button>
  )
  const builtin = (card.env.kind === 'builtin' || card.env.kind === 'system') && card.action === 'install_managed'
  const otherEnv = builtin ? (
    <Button variant="ghost" size="md" data-convert-choose-env className="text-ink-2" onClick={actions.chooseEnv}>
      {pt('convert.deps.btn.otherEnv')}
    </Button>
  ) : null
  if (card.kind === 'system') {
    return {
      title: pt('convert.deps.system', { product: PRODUCT_NAME }),
      primary: <Button variant="primary" size="md" data-convert-choose-env data-prep-primary="convert_choose_env" onClick={actions.chooseEnv}>{pt('convert.deps.btn.chooseEnv')}</Button>,
      ghost: decline,
    }
  }
  const uncommon = card.kind === 'uncommon'
  const size = card.downloads_python && (card.download_bytes ?? 0) > 0
    ? pt('convert.deps.downloadSize', { mb: Math.max(1, Math.round((card.download_bytes ?? 0) / 1048576)) })
    : ''
  const title = uncommon
    ? pt(builtin ? 'convert.deps.uncommonManaged' : 'convert.deps.uncommon', {
        names: listJoin(card.outside_list.length ? card.outside_list : card.packages), product: PRODUCT_NAME,
      }) + size
    : builtin
      ? pt(card.env.kind === 'system' ? 'convert.deps.askSystem' : 'convert.deps.askBuiltin', {
          product: PRODUCT_NAME, names: listJoin(card.packages), size,
        })
      : pt(card.action === 'install_managed' ? 'convert.deps.askOnceManaged' : 'convert.deps.askOnce', {
          env, product: PRODUCT_NAME,
        }) + size
  return {
    title,
    primary: <Button variant="primary" size="md" data-convert-deps-install={card.kind} data-prep-primary="convert_deps_install" onClick={actions.install}>{pt(uncommon ? 'convert.deps.btn.installOnce' : builtin ? 'convert.deps.btn.prepareEnv' : 'convert.deps.btn.installAlways')}</Button>,
    ghost: <>{otherEnv}{decline}</>,
  }
}
