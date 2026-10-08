import { useTranslation } from 'react-i18next'
import { Ellipsis, TriangleAlert } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { formatTime } from '@/i18n/format'
import { overwriteDisk, reloadFromDisk, saveNow, useDocumentStore } from '@/store/documentStore'
import { msg } from '@/i18n'
import { askConfirm, useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Menu, MenuItem } from './ui/Menu'
import { Notice, type StatusTone } from './ui/Notice'
import type { DiskDocumentSummary } from '@/lib/api'

/**
 * 文档级的常驻提示条 —— 保存状态机里**会丢编辑、需要用户裁决**的那两种：外部冲突、保存失败。
 *
 * 为什么是常驻条而不是 toast：两件事都"刷新一次就还在"（磁盘上那份确实被改过、编辑确实还没落盘）。
 * 用一个 4.5 秒后自己消失的状态条报告它们，等于把一个持续存在的事实说成一次事件。
 *
 * 形态（2026-10-07 设计审计 §10.1）：**工作面板里**、画布标签行上方的一条内嵌说明条（`Notice`，锚点色分轻重：
 * 冲突 warn、保存失败 danger），`role="alert"`。此前是横贯整个窗口、贴在顶栏下面的灰条，把整个应用往下推 32px。
 * 不打断编辑的那几件（未恢复的编辑、上次的排版没打开、版本过新）不再有横幅，住在顶栏的文档状态芯片里。
 */
export function DocumentBanner() {
  const { t } = useTranslation('workspace')
  const saveState = useDocumentStore((s) => s.saveState)
  const saveIssue = useDocumentStore((s) => s.saveIssue)

  // 冲突最急：磁盘上那份不是我以为的那份，编辑正堆在本机等裁决
  if (saveState === 'conflict') {
    return (
      <Banner
        urgent
        tone="warn"
        title={t(saveIssue?.kind === 'stale' ? 'docBanner.conflictStale' : 'docBanner.conflictExternal')}
        detail={<ConflictDetail disk={saveIssue?.disk} />}
        action={<ConflictActions />}
      />
    )
  }

  if (saveState === 'save_error') {
    return (
      <Banner
        urgent
        tone="danger"
        title={t('docBanner.saveErrorBody')}
        // 唯一出口，给实心黑：这条上只有它是要按的
        action={
          <Button variant="primary" size="sm" className="shrink-0" onClick={() => void saveNow()}>
            {t('docBanner.retry')}
          </Button>
        }
      />
    )
  }
  return null
}

/** 磁盘上那份的一句摘要（冲突条与顶栏芯片的说明块共用） */
export function ConflictDetail({ disk }: { disk: DiskDocumentSummary | null | undefined }) {
  const { t } = useTranslation('workspace')
  return (
    <>
      {disk && typeof disk.objects === 'number'
        ? t('docBanner.conflictDisk', {
            canvases: disk.canvases,
            objects: disk.objects,
            time: formatTime(disk.mtime ?? disk.updatedAt ?? Date.now()),
          })
        : t('docBanner.conflictDiskUnknown')}
    </>
  )
}

/**
 * 冲突的三个出口，分轻重（2026-10-07 审计 P0 / §10.1）：「重新加载」是安全的那条（先把本机编辑存成恢复副本
 * 再读盘），主按钮；「另存为」两份都留，次按钮；「覆盖」会丢掉磁盘上那份——收进 ⋯ 里的危险项，并且先问一句
 * （`confirmOverwriteDisk`）。此前三颗同形同重，一下就点没了。冲突条与顶栏芯片的说明块用同一份。
 */
export function ConflictActions({ onAction }: { onAction?: () => void }) {
  const { t } = useTranslation('workspace')
  const stale = useDocumentStore((s) => s.saveIssue?.kind === 'stale')
  const then = (fn: () => unknown) => () => {
    onAction?.()
    void fn()
  }
  return (
    <>
      <Button
        variant="primary"
        size="sm"
        className="shrink-0"
        data-doc-conflict-action="reload"
        onClick={then(reloadFromDisk)}
      >
        {t('docBanner.reload')}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        className="shrink-0"
        data-doc-conflict-action="save-as"
        onClick={then(() => useUiStore.getState().setLayoutOpen(true, 'save'))}
      >
        {t('docBanner.saveAs')}
      </Button>
      <Menu
        align="end"
        width={220}
        trigger={
          <Button size="icon-sm" data-doc-conflict-more aria-label={t('docBanner.moreActions')}>
            <Ellipsis size={ICON_SIZE.sm} />
          </Button>
        }
      >
        <MenuItem
          danger
          icon={TriangleAlert}
          data-doc-conflict-action="overwrite"
          onSelect={then(() => confirmOverwriteDisk(stale))}
        >
          {t('docBanner.overwrite')}
        </MenuItem>
      </Menu>
    </>
  )
}

/**
 * 「用这个窗口的覆盖」之前先问一句：它会让磁盘上那份（另一个窗口存的较新版本 / 外部改过的文件）
 * 被这个窗口的内容替换掉，而且不像「重新加载」那样先留恢复副本。危险档确认框。
 *
 * 等用户回答的那段时间里冲突可能已经被别处裁决了（另一颗按钮、换了文档）：回答之后**冲突还在、
 * 还是同一份文档**才覆盖，否则什么都不做。
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
 * 工作面板顶上那条提示条，**全产品一种**（2026-10-07 设计审计 §10.1）：`Notice` 的锚点色底，内嵌在
 * `[data-work-panel]` 里、画布标签行上方（外边距 8、圆角 8），一句标题 + 可选的一句细节（窄于 900 收起）+ 出口。
 * 冲突 / 保存失败 / 接入状态 / 构建版本不一致四条同形；两条以上挨着出现时是同一摞。
 */
export function Banner({
  tone = 'neutral',
  title,
  detail,
  action,
  urgent = false,
  icon,
  ...rest
}: {
  tone?: StatusTone
  title: React.ReactNode
  detail?: React.ReactNode
  action?: React.ReactNode
  /**
   * 不处理就会丢编辑的那几条（冲突、保存失败）：`role="alert"`，读屏器立刻打断说。
   * 其余照旧 `role="status"`。role 只是给读屏器的，**用例不认它**（web/AGENTS.md），认 `data-doc-banner`。
   */
  urgent?: boolean
  icon?: Parameters<typeof Notice>[0]['icon']
} & Record<`data-${string}`, string | undefined>) {
  return (
    <Notice
      {...rest}
      tone={tone}
      icon={icon}
      role={urgent ? 'alert' : 'status'}
      data-doc-banner={urgent ? 'urgent' : ''}
      className="shrink-0 items-center py-1.5"
      action={action}
    >
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="min-w-0 truncate">{title}</span>
        {detail && <span className="hidden shrink-0 opacity-80 min-[900px]:inline">{detail}</span>}
      </span>
    </Notice>
  )
}

/** 工作面板里那一摞提示条的容器：一条都没有时不占位 */
export function BannerStack({ children }: { children: React.ReactNode }) {
  return (
    <div data-banner-stack className="flex shrink-0 flex-col gap-1.5 px-2 pt-2 empty:hidden">
      {children}
    </div>
  )
}
