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
 * **结构（关掉整类“切项目竞态”）**：外层 `ReadOnlyProjectPrompt` 只决定哪个项目（root）该弹；弹窗的全部状态
 * （选文件夹、忙、错误、未完成的副本、浏览器目录浏览器）都在 `key={root}` 的内层 `Prompt` 里。项目一换，A 的内层卸载，
 * 所有挂起的状态一并作废；内层的每个异步回来都先看自己还活着没有，请求也显式带发起时的项目 id（不看“此刻是哪个项目”）。
 *
 * 事实在后端：`project.writable`（`os.access`）与 `settings.readonly_save_as_declined`；这里不判断权限。
 */

export function ReadOnlyProjectPrompt() {
  const phase = useProjectStore((s) => s.phase)
  const project = useProjectStore((s) => s.project)
  /** 正在为哪个项目（root）弹着；null = 没有 */
  const [active, setActive] = useState<string | null>(null)

  const root = project?.open ? (project.figures_dir ?? null) : null
  const needsPrompt =
    phase === 'open' &&
    !!root &&
    project?.writable === false &&
    !project.tutorial &&
    project.settings?.readonly_save_as_declined !== true

  // 开与收只在这**一个**效果里决定：正在弹的还成立就留着；否则是第一次见到的只读项目；否则没有
  useEffect(() => {
    const target = needsPrompt && root && (active === root || !wasAsked(root)) ? root : null
    if (target === active) return
    if (target) markAsked(target)
    setActive(target)
  }, [needsPrompt, root, active])

  // 渲染期就用 root 对账：项目刚换、效果还没跑的那一帧，旧项目的内层已经卸载
  if (!needsPrompt || !root || active !== root) return null
  return <Prompt key={root} projectId={project?.id ?? null} onDone={() => setActive(null)} />
}

function Prompt({
  projectId,
  onDone,
}: {
  /** 发起时的项目 id：所有请求显式带它，不跟着“此刻的项目”走 */
  projectId: string | null
  onDone: () => void
}) {
  const { t } = useTranslation('project')
  const open = useProjectStore((s) => s.open)
  const [browsing, setBrowsing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** 副本已经建好、但有文件读不了没复制进去：先让用户看见这句，点「打开副本」才切过去 */
  const [incomplete, setIncomplete] = useState<{ path: string; count: number; paths: string[] } | null>(null)
  // 卸载（项目换了 / 弹窗收了）之后回来的异步一律作废：不改状态、不保存、不打开
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  const pj = projectId ?? undefined

  const close = () => {
    // 不确认就关：副本留在磁盘上，但不切过去
    if (!busy) onDone()
  }

  const saveTo = async (parent: string) => {
    setBusy(true)
    setError(null)
    try {
      const copy = await saveProjectAs(parent, pj)
      if (!alive.current) return
      if (copy.skipped > 0) {
        setIncomplete({ path: copy.path, count: copy.skipped, paths: copy.skipped_paths ?? [] })
        return
      }
      onDone()
      await open(copy.path)
    } catch (e) {
      if (alive.current) setError(backendErrorText(e))
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  const startSave = async () => {
    if (isDesktop()) {
      const picked = await pickDirectory(t('readOnlyPrompt.pickTitle'))
      if (!alive.current) return // 选文件夹期间项目换了：这次选择不属于现在的任何项目
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
      onDone()
      await open(path)
    } catch (e) {
      if (alive.current) setError(backendErrorText(e))
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  const keepReadOnly = async () => {
    setBusy(true)
    setError(null)
    try {
      await applyProjectSettingsPatch({ readonly_save_as_declined: true }, pj)
      if (alive.current) onDone()
    } catch (e) {
      if (alive.current) setError(backendErrorText(e))
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  return (
    <>
      <Dialog
        open={!browsing}
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
