import { useTranslation } from 'react-i18next'
import { Ellipsis } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { continueNew, openAlternative, retryRestore, type RestoreIssue } from '@/store/documentRestore'
import { saveNow, useDocumentStore } from '@/store/documentStore'
import { Button } from './ui/Button'
import { Menu, MenuItem } from './ui/Menu'

/**
 * 「上次的排版没能恢复」的说明块（顶栏文档状态芯片的 320 宽气泡里）。
 *
 * 用户看到的只有**一句话 + 一个主按钮**，其余出口折在「⋯」里；不出现 owners / pj / revision / epoch 这类词，
 * 也不并列摆几颗看不懂区别的按钮。主按钮按失败原因定：
 *  - 暂时读不到（网络 / 服务忙）→ 「重试」；
 *  - 那份找不到 / 读不出来 / 版本过新 → 有已验证的别的排版就「打开“它”」，没有就「继续新建」；
 *  - 本机找到了几份 → 「选一份打开」（点开是名单）；
 *  - 示例没打开 → 「继续新建」。
 * 「继续新建」不删任何旧稿：旧候选与旧正文一个字节不动，之后晚到的旧响应也盖不过新文档。
 */
export function RestoreIssueBody({ issue, close }: { issue: RestoreIssue; close: () => void }) {
  const { t } = useTranslation('workspace')
  const name = issue.ref?.name || t('docBanner.lastDocUnnamed')
  const done = (fn: () => unknown) => () => {
    close()
    void fn()
  }
  const title = (() => {
    switch (issue.kind) {
      case 'unavailable':
      case 'session_unavailable':
        return t('restore.title.unavailable')
      case 'missing':
        return t('restore.title.missing', { name })
      case 'invalid':
        return t('restore.title.invalid', { name })
      case 'too_new':
        return t('restore.title.tooNew', { name })
      case 'choose':
        return t('restore.title.choose')
      case 'prepare_failed':
        return t('restore.title.prepareFailed')
      case 'index_degraded':
        return t('restore.title.indexDegraded')
    }
  })()
  const alts = issue.alternatives
  const altLabel = (a: { name: string; localCopy?: true }) =>
    a.localCopy
      ? t('restore.openLocal', { name: a.name || t('docBanner.lastDocUnnamed') })
      : t('restore.openAlt', { name: a.name || t('docBanner.lastDocUnnamed') })
  const openAlt = (id: string, a: { name: string; localCopy?: true }) => (
    <MenuItem key={`${id}${a.localCopy ? ':local' : ''}`} data-restore-action="open-alt" onSelect={done(() => openAlternative(id))}>
      {altLabel(a)}
    </MenuItem>
  )
  const newItem = (
    <MenuItem key="new" data-restore-action="continue-new" onSelect={done(continueNew)}>
      {t('restore.continueNew')}
    </MenuItem>
  )
  const more = (items: React.ReactNode[]) =>
    items.length === 0 ? null : (
      <Menu
        align="end"
        width={240}
        trigger={
          <Button size="icon-sm" data-restore-more aria-label={t('docBanner.moreActions')}>
            <Ellipsis size={ICON_SIZE.sm} />
          </Button>
        }
      >
        {items}
      </Menu>
    )

  let primary: React.ReactNode
  let folded: React.ReactNode[]
  if (issue.kind === 'unavailable' || issue.kind === 'session_unavailable' || issue.kind === 'index_degraded') {
    primary = (
      <Button variant="primary" size="sm" data-restore-action="retry" onClick={done(retryRestore)}>
        {t('restore.retry')}
      </Button>
    )
    // 备选（含「打开本机副本」）折在 ⋯ 里：服务暂时不可用的这段时间里它们是仅有的出路
    folded = [...alts.map((a) => openAlt(a.id, a)), newItem]
  } else if (issue.kind === 'choose') {
    primary = (
      <Menu
        width={260}
        trigger={
          <Button variant="primary" size="sm" data-restore-action="choose">
            {t('restore.choose')}
          </Button>
        }
      >
        {alts.map((a) => openAlt(a.id, a))}
      </Menu>
    )
    folded = [newItem]
  } else if (issue.kind === 'prepare_failed' || alts.length === 0) {
    primary = (
      <Button variant="primary" size="sm" data-restore-action="continue-new" onClick={done(continueNew)}>
        {t('restore.continueNew')}
      </Button>
    )
    folded = alts.map((a) => openAlt(a.id, a))
  } else {
    const [first, ...others] = alts
    primary = (
      <Button
        variant="primary"
        size="sm"
        data-restore-action="open-alt"
        onClick={done(() => openAlternative(first.id))}
      >
        {altLabel(first)}
      </Button>
    )
    folded = [...others.map((a) => openAlt(a.id, a)), newItem]
  }

  return (
    <>
      <p className="type-title text-ink" data-restore-kind={issue.kind}>
        {title}
      </p>
      <div className="flex flex-wrap items-center gap-1.5 pt-1">
        {primary}
        {more(folded)}
      </div>
    </>
  )
}

/**
 * 「正文存下了，但下次自动恢复要用的记录没更新成功」：与保存进度是两件事（`restoreMetaIssue`）。
 * 一句话 + 一个主按钮（再存一次——再排一次保存，成功就自己消失）。
 */
export function RestoreMetaBody({ close }: { close: () => void }) {
  const { t } = useTranslation('workspace')
  const meta = useDocumentStore((s) => s.restoreMetaIssue)
  if (!meta) return null
  return (
    <>
      <p className="type-title text-ink">{t('restore.meta.title')}</p>
      <div className="flex flex-wrap items-center gap-1.5 pt-1">
        <Button
          variant="primary"
          size="sm"
          data-restore-action="meta-retry"
          onClick={() => {
            close()
            void saveNow()
          }}
        >
          {t('restore.meta.retry')}
        </Button>
      </div>
    </>
  )
}
