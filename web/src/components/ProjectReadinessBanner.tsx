import { useTranslation } from 'react-i18next'
import { Images } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { pendingCount } from '@/lib/readinessText'
import { bannerReport, useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Banner } from './DocumentBanner'

/**
 * 打开一个旧项目时的那一句话。
 *
 * ```text
 * 已找到 18 张图：8 张可编辑，5 张待连接，5 张仅排版。 [查看接入状态] [关闭]
 * ```
 *
 * 壳直接用 `DocumentBanner` 的 `Banner`（2026-09-15 打磨 B3）：它们是同一类东西
 * ——**不打断编辑、不抢焦点、不自动弹对话框**，说明加一到两个出口——此前却是两副壳，
 * 24 高的条里塞 28 高的按钮。`UpdateBanner` 已经不是横幅了（改成了启动对话框）。
 *
 * 语气是中性的：`layout_only` 不是错误，只是"没有源脚本"，那些图照旧能排版、
 * 裁剪、标注和导出。所以这里既不用 danger 也不用 accent 底色——把一个正常
 * 状态画成警告，用户就会去找一个并不存在的故障。
 *
 * 关闭是**按报告版本**记的（`项目 id + fingerprint`），不是"永久别再提"：
 * 事实变了（连上了新脚本、冒出一个冲突）就该再说一次。
 */
export function ProjectReadinessBanner() {
  const { t } = useTranslation(['workspace', 'common'])
  const report = useProjectReadinessStore(bannerReport)
  // 接入中心开着的时候不重复说一遍：那里显示的是同一份事实，而且更详细
  const centerOpen = useUiStore((s) => s.registryOpen)

  if (!report || centerOpen) return null

  const { total, editable, layout_only: layoutOnly } = report.summary
  const pending = pendingCount(report.summary)

  return (
    <Banner icon={<Images size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />}>
      <span className="min-w-0 flex-1 truncate">
        {t('workspace:readiness.bannerSummary', { total, editable, pending, layoutOnly })}
      </span>
      <Button
        size="sm"
        className="shrink-0"
        onClick={() => useProjectReadinessStore.getState().openCenter({ source: 'banner' })}
      >
        {t('workspace:readiness.openCenter')}
      </Button>
      <Button
        size="sm"
        className="shrink-0 text-ink-3"
        onClick={() => useProjectReadinessStore.getState().dismissBanner()}
      >
        {t('common:actions.close')}
      </Button>
    </Banner>
  )
}
