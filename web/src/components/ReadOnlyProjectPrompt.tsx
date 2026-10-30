import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { backendErrorText, saveProjectAs } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { isDesktop, pickDirectory } from '@/lib/desktop'
import { applyProjectSettingsPatch } from '@/lib/projectSettingsPatch'
import { markAsked, wasAsked } from '@/lib/readOnlyPromptMemory'
import { useProjectStore } from '@/store/projectStore'
import { DirBrowser } from './DirBrowser'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Notice } from './ui/Notice'

/**
 * 打开一个 Tavotto 没有写入权限的项目时问**一次**：另存一份到你自己的文件夹再编辑？
 *
 * 一句话 + 一颗主按钮（另存为…）+ 一颗次按钮（继续只读打开），别的说明一概没有。
 *
 * * 另存为 = 选一个文件夹（桌面壳是系统选择器，浏览器版是服务器端目录浏览器），后端把项目复制到
 *   那里（`engine/projectcopy`），然后 `projectStore.open` 打开副本——副本是个新项目、可写。
 * * 继续只读打开 = 按项目路径记住（`settings.readonly_save_as_declined`），之后每次打开不再问；
 *   项目照常只读运行，脚本写不出的输出文件由失败卡说一句（`output_folder_read_only`）。
 * * Esc / 点外面只是「这次先不决定」：本次会话不再弹，但不写记忆，下次打开还会问。
 *
 * 事实在后端：`project.writable`（`os.access`）与 `settings.readonly_save_as_declined`；这里不判断权限。
 */

export function ReadOnlyProjectPrompt() {
  const { t } = useTranslation('project')
  const phase = useProjectStore((s) => s.phase)
  const project = useProjectStore((s) => s.project)
  const open = useProjectStore((s) => s.open)
  const [shown, setShown] = useState<string | null>(null)
  const [browsing, setBrowsing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** 副本已经建好、但有文件读不了没复制进去：先让用户看见这句，点「打开副本」才切过去 */
  const [incomplete, setIncomplete] = useState<{ path: string; count: number; paths: string[] } | null>(null)
  // 保存期间项目被换掉：别把旧项目的副本打开成新项目
  const shownRef = useRef<string | null>(null)
  shownRef.current = shown

  const root = project?.open ? (project.figures_dir ?? null) : null
  const needsPrompt =
    phase === 'open' &&
    !!root &&
    project?.writable === false &&
    !project.tutorial &&
    project.settings?.readonly_save_as_declined !== true

  // 开与收只在这**一个**效果里决定（A → B 直接切换时，旧的收和新的开不能各排一次 setShown，后到的 null 会盖掉 B 的弹窗）：
  // 目标值 = 此刻该显示哪个项目的弹窗——正在显示的还成立就留着；否则是第一次见到的只读项目；否则没有。
  useEffect(() => {
    const target =
      needsPrompt && root && (shown === root || !wasAsked(root)) ? root : null
    if (target === shown) return
    if (target) markAsked(target)
    setError(null)
    setIncomplete(null)
    setShown(target)
  }, [needsPrompt, root, shown])

  const close = () => {
    // 不确认就关：副本留在磁盘上，但不切过去
    if (!busy) {
      setShown(null)
      setIncomplete(null)
    }
  }

  const saveTo = async (parent: string) => {
    const from = shownRef.current
    setBusy(true)
    setError(null)
    try {
      const copy = await saveProjectAs(parent)
      if (shownRef.current !== from) return
      if (copy.skipped > 0) {
        setIncomplete({ path: copy.path, count: copy.skipped, paths: copy.skipped_paths ?? [] })
        return
      }
      setShown(null)
      await open(copy.path)
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  const startSave = async () => {
    if (isDesktop()) {
      const picked = await pickDirectory(t('readOnlyPrompt.pickTitle'))
      if (picked) await saveTo(picked) // 取消不是错误，弹窗留着
    } else {
      setBrowsing(true)
    }
  }

  const openCopy = async () => {
    if (!incomplete) return
    setBusy(true)
    try {
      const path = incomplete.path
      setShown(null)
      setIncomplete(null)
      await open(path)
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  const keepReadOnly = async () => {
    // 请求在飞时项目可能被换掉（桌面 tavotto:open 直接切到另一个只读项目）：完成回来只收**发起时那个**项目的弹窗，
    // 不能把新项目刚弹出、用户还没回答的那个一起收掉
    const asked = shown
    setBusy(true)
    setError(null)
    try {
      await applyProjectSettingsPatch({ readonly_save_as_declined: true })
      setShown((cur) => (cur === asked ? null : cur))
    } catch (e) {
      if (shownRef.current === asked) setError(backendErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Dialog
        open={!!shown && !browsing}
        onOpenChange={(v) => {
          if (!v) close()
        }}
        // Esc = 这次先不决定：不写记忆，下次打开还会问
        onEscape={close}
        title={
          incomplete
            ? t('readOnlyPrompt.partial', { count: incomplete.count })
            : t('readOnlyPrompt.title', { product: PRODUCT_NAME })
        }
        size="sm"
        busy={busy}
        anchor="readonly-project"
        status={
          error ? (
            <Notice tone="danger" data-readonly-project-error>
              {error}
            </Notice>
          ) : undefined
        }
        footer={
          incomplete
            ? {
                primary: (
                  <Button
                    variant="primary"
                    size="lg"
                    data-readonly-project-open-copy
                    loading={busy}
                    onClick={() => void openCopy()}
                  >
                    {t('readOnlyPrompt.openCopy')}
                  </Button>
                ),
              }
            : {
                secondary: (
                  <Button
                    variant="secondary"
                    size="lg"
                    data-readonly-project-continue
                    disabled={busy}
                    onClick={() => void keepReadOnly()}
                  >
                    {t('readOnlyPrompt.continue')}
                  </Button>
                ),
                primary: (
                  <Button
                    variant="primary"
                    size="lg"
                    data-readonly-project-save
                    loading={busy}
                    onClick={() => void startSave()}
                  >
                    {t('readOnlyPrompt.save')}
                  </Button>
                ),
              }
        }
      >
        {incomplete && incomplete.paths.length > 0 ? (
          <ul data-readonly-project-skipped className="m-0 list-none p-0 text-sm text-ink-3">
            {incomplete.paths.map((rel) => (
              <li key={rel} className="truncate font-mono">
                {rel}
              </li>
            ))}
          </ul>
        ) : null}
      </Dialog>
      {browsing && (
        <DirBrowser
          mode="open"
          title={t('readOnlyPrompt.pickTitle')}
          onClose={() => setBrowsing(false)}
          onPick={(path) => {
            setBrowsing(false)
            void saveTo(path)
          }}
        />
      )}
    </>
  )
}
