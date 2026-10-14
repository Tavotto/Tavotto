import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t } from '@/i18n'
import { useRenderStore } from '@/store/renderStore'

/** 所有属性页/元素树消费同一份文件级等待，不靠会自动消失的 toast 维持进度。 */
export function useRenderProgress(fileId: string, rendering: boolean, buildingLabel: string) {
  useTranslation(['workspace', 'inspector'])
  const info = useRenderStore((s) => s.building[fileId])
  const active = rendering || !!info
  const startedAt = info?.startedAt
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!active || startedAt == null) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active, startedAt])

  const phase = info?.phase ?? 'rendering'
  const label = phase === 'preparing'
    ? t('renderProgress.preparing', { ns: 'workspace' })
    : info?.cold
      ? t('panel.coldBuilding', { ns: 'inspector' })
      : buildingLabel
  const text = active && startedAt != null
    ? t('renderProgress.elapsed', {
      ns: 'workspace', message: label,
      seconds: Math.max(0, Math.floor((now - startedAt) / 1000)),
    })
    : label
  return { active, cold: !!info?.cold, phase, text }
}
