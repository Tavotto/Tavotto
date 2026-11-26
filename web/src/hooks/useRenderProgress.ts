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
  // 冷启动按三步说（#479）：找运行环境 → 启动 Python → 运行脚本并生成可编辑元素。热路径的重画不报步数，
  // 只说「正在构建…」——它一两秒就完，步数只会一闪而过。第三步里「运行脚本」与「生成可编辑元素」
  // 在 worker 的同一次 build 里，中途没有信号，所以合成一步，不假装分得开。
  const label = phase === 'preparing'
    ? info?.stage === 'starting'
      ? t('renderProgress.stepStarting', { ns: 'workspace' })
      : info?.stage === 'environment'
        ? t('renderProgress.stepEnvironment', { ns: 'workspace' })
        : t('renderProgress.preparing', { ns: 'workspace' })
    : info?.cold
      ? t('renderProgress.stepRunning', { ns: 'workspace' })
      : buildingLabel
  const text = active && startedAt != null
    ? t('renderProgress.elapsed', {
      ns: 'workspace', message: label,
      seconds: Math.max(0, Math.floor((now - startedAt) / 1000)),
    })
    : label
  return { active, cold: !!info?.cold, phase, text }
}
