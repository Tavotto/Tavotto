import { useTranslation } from 'react-i18next'
import { TriangleAlert, RotateCcwClock, Lock } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { formatTime } from '@/i18n/format'
import {
  dismissDocNotice,
  overwriteDisk,
  reloadFromDisk,
  saveNow,
  useDocumentStore,
} from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { msg } from '@/i18n'
import { askConfirm, useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'

/**
 * 文档级的常驻提示条 —— 保存状态机里**需要用户裁决**的那几种情况。
 *
 * 为什么是常驻条而不是 toast：这四件事全都"刷新一次就还在"（磁盘上那份
 * 确实被改过、本机确实还躺着一份副本、那份文档确实读不了）。用一个 4.5 秒
 * 后自己消失的状态条报告它们，等于把一个持续存在的事实说成一次事件——
 * 改造前就是这么做的，用户回到界面时什么都看不到，而磁盘上那份还落后半小时。
 *
 * 形态是 `Banner`：贴画布顶的一条 surface-2 状态条。`UpdateBanner` 早已改成启动时的
 * 对话框（`UpdateNoticeDialog`），这里原来的「与它同形」是句过时的注释（2026-09-15 打磨 B3）。
 */
export function DocumentBanner() {
  const { t } = useTranslation('workspace')
  const saveState = useDocumentStore((s) => s.saveState)
  const saveIssue = useDocumentStore((s) => s.saveIssue)
  const notice = useDocumentStore((s) => s.docNotice)

  // 冲突最急：磁盘上那份不是我以为的那份，编辑正堆在本机等裁决
  if (saveState === 'conflict') {
    const disk = saveIssue?.disk
    return (
      <Banner urgent icon={<TriangleAlert size={ICON_SIZE.sm} className="shrink-0 text-danger" />}>
        <span className="min-w-0 flex-1 truncate">
          {t(saveIssue?.kind === 'stale' ? 'docBanner.conflictStale' : 'docBanner.conflictExternal')}
        </span>
        <span className="hidden shrink-0 opacity-80 min-[900px]:inline">
          {disk && typeof disk.objects === 'number'
            ? t('docBanner.conflictDisk', {
                canvases: disk.canvases,
                objects: disk.objects,
                time: formatTime(disk.mtime ?? disk.updatedAt ?? Date.now()),
              })
            : t('docBanner.conflictDiskUnknown')}
        </span>
        {/* 三个出口分轻重（2026-10-07 审计 P0）：「重新加载」是安全的那条（先把本机编辑存成恢复副本
            再读盘），给实心黑；「另存为」两份都留，次按钮；「覆盖」会丢掉磁盘上那份，排最后、
            ghost，并且先问一句（`confirmOverwriteDisk`）。此前三颗同形同重，一下就点没了 */}
        <Button
          variant="primary"
          size="sm"
          className="shrink-0"
          data-doc-conflict-action="reload"
          onClick={() => void reloadFromDisk()}
        >
          {t('docBanner.reload')}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          className="shrink-0"
          data-doc-conflict-action="save-as"
          onClick={() => useUiStore.getState().setLayoutOpen(true, 'save')}
        >
          {t('docBanner.saveAs')}
        </Button>
        <Button
          size="sm"
          className="shrink-0"
          data-doc-conflict-action="overwrite"
          onClick={() => confirmOverwriteDisk(saveIssue?.kind === 'stale')}
        >
          {t('docBanner.overwrite')}
        </Button>
      </Banner>
    )
  }

  if (saveState === 'save_error') {
    return (
      <Banner urgent icon={<TriangleAlert size={ICON_SIZE.sm} className="shrink-0 text-danger" />}>
        <span className="min-w-0 flex-1 truncate">{t('docBanner.saveErrorBody')}</span>
        {/* 唯一出口，给实心黑：白底条上只有它是要按的 */}
        <Button variant="primary" size="sm" className="shrink-0" onClick={() => void saveNow()}>
          {t('docBanner.retry')}
        </Button>
      </Banner>
    )
  }

  // 「发现未恢复的编辑」不再是横幅：它挂在顶栏「已保存」右侧（TopBar 的
  // RecoveryNotice，2026-09-11 用户反馈）

  if (notice?.kind === 'schema_too_new') {
    return (
      <Banner icon={<Lock size={ICON_SIZE.sm} className="shrink-0 text-ink-2" />}>
        <span className="min-w-0 flex-1 truncate">
          {t('docBanner.tooNewTitle', { schema: notice.schema })}
        </span>
        <span className="hidden shrink-0 opacity-80 min-[900px]:inline">
          {t('docBanner.tooNewBody')}
        </span>
        <Button size="sm" className="shrink-0" onClick={dismissDocNotice}>
          {t('docBanner.dismiss')}
        </Button>
      </Banner>
    )
  }

  return <LastDocumentBanner />
}

/**
 * 「用这个窗口的覆盖」之前先问一句：它会让磁盘上那份（另一个窗口存的较新版本 / 外部改过的文件）
 * 被这个窗口的内容替换掉，而且不像「重新加载」那样先留恢复副本。危险档确认框。
 *
 * 等用户回答的那段时间里冲突可能已经被别处裁决了（另一颗按钮、换了文档）：回答之后**冲突还在、
 * 还是同一份文档**才覆盖，否则什么都不做。导出给用例直接驱动。
 */
async function confirmOverwriteDisk(stale: boolean): Promise<boolean> {
  const before = useDocumentStore.getState().documentId
  const ok = await askConfirm({
    title: msg('docBanner.overwriteConfirmTitle', undefined, 'workspace'),
    body: msg(
      stale ? 'docBanner.overwriteConfirmBodyStale' : 'docBanner.overwriteConfirmBodyExternal',
      undefined,
      'workspace',
    ),
    confirmLabel: msg('docBanner.overwrite', undefined, 'workspace'),
    danger: true,
  })
  if (!ok) return false
  const now = useDocumentStore.getState()
  if (now.saveState !== 'conflict' || now.documentId !== before) return false
  await overwriteDisk()
  return true
}

/**
 * 切回项目时记着上次开的是哪份文档、却没读回来（T02）。**说出名字**并给
 * 重试——不说的话用户面对的是一份空白 fig_layout，还以为自己的版没了。
 */
function LastDocumentBanner() {
  const { t } = useTranslation('workspace')
  const issue = useProjectStore((s) => s.lastDocumentIssue)
  if (!issue) return null
  return (
    <Banner icon={<RotateCcwClock size={ICON_SIZE.sm} className="shrink-0 text-ink-2" />}>
      {/* 文档名是用户内容，作为插值原样透出 */}
      <span className="min-w-0 flex-1 truncate">
        {t('docBanner.lastDocTitle', { name: issue.name || t('docBanner.lastDocUnnamed') })}
      </span>
      <span className="hidden shrink-0 opacity-80 min-[900px]:inline">{t('docBanner.lastDocBody')}</span>
      <Button
        size="sm"
        className="shrink-0"
        onClick={() => void useProjectStore.getState().openLastDocument()}
      >
        {t('docBanner.lastDocOpen')}
      </Button>
      <Button
        size="sm"
        className="shrink-0"
        onClick={() => useProjectStore.getState().dismissLastDocumentIssue()}
      >
        {t('docBanner.dismiss')}
      </Button>
    </Banner>
  )
}

/**
 * 画布顶上那条状态条，**全产品一种**（2026-09-15 打磨 B3）：贴边、surface-2 底、
 * 一条 border-b、min-h 32、px 12，无圆角无外边距。
 *
 * 此前是两副壳：这里是「带 12% 实边、四周留 8」的一张卡（宪法第十三节：状态区不是卡片），
 * 接入状态那条是贴边的 24 高 surface-2 条却塞着 28 高的按钮。`ProjectReadinessBanner`
 * 现在直接用这一份，两条挨着出现时是同一条轨的两行。
 */
export function Banner({
  icon,
  children,
  urgent = false,
}: {
  icon: React.ReactNode
  children: React.ReactNode
  /**
   * 需要用户裁决、不处理就会丢编辑的那几条（冲突、保存失败）：`role="alert"`，读屏器立刻打断说。
   * 其余照旧 `role="status"`。role 只是给读屏器的，**用例不认它**（web/AGENTS.md），认 `data-doc-banner`。
   */
  urgent?: boolean
}) {
  return (
    <div
      role={urgent ? 'alert' : 'status'}
      data-doc-banner={urgent ? 'urgent' : ''}
      className="flex min-h-8 shrink-0 items-center gap-2 border-b border-border bg-surface-2 px-3 text-xs text-ink"
    >
      {icon}
      {children}
    </div>
  )
}
