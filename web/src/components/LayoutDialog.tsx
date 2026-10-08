import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { literal, msg } from '@/i18n'
import { FolderOpen, Save } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  ApiError,
  REVISION_ABSENT,
  backendErrorText,
  fetchLayout,
  fetchLayoutList,
  saveLayout,
  type DiskDocumentSummary,
} from '@/lib/api'
import {
  canonicalLayoutName,
  knownLayoutRevision,
  rememberLayoutName,
  rememberLayoutRevision,
} from '@/lib/layoutRevision'
import { normalizeLayout } from '@/lib/migrate'
import { cn } from '@/lib/utils'
import { openLayoutDocument } from '@/store/actions'
import { projectFileSnapshot, setProjectFile, useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { captureSaveContext, ifStillCurrent, stillCurrent } from '@/store/saveContext'
import { useUiStore } from '@/store/uiStore'
import { dirTail } from '@/lib/pathDisplay'
import { FormRow } from './FormRow'
import { emitLayoutSaved } from '@/lib/layoutSaved'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'
import { listRowClass, rowMetaClass } from './ui/listRow'
import { Notice } from './ui/Notice'
import { formatRelativeTime } from '@/i18n/format'

/**
 * 「另存为」与「打开」（审计 T04）。
 *
 * 改造前是**一个**弹窗同时承担两件事：上半截是保存表单，下半截是一列可载入
 * 的文件，底部一颗「保存为画布文件」。用户从「载入」进来时正对着的却是保存
 * 表单，而那颗主按钮会把当前文档写到框里的名字下。两件事的后果相反（一个
 * 写盘、一个丢弃当前工作换一份进来），不该共用一屏。
 *
 * 现在按 `uiStore.layoutIntent` 分成两种形态，各自只做一件事：
 * - `save`：只有名字和位置，主按钮是「另存为」；
 * - `load`：只有文档列表，一个能写盘的控件都没有。
 *
 * 词汇统一到**项目 > 文档 > 画布**：这里存取的是一份文档（schema 3，含它
 * 全部画布），所以不再叫「画布文件」，字段也不再叫「布局名称」。
 *
 * 第三种形态 `saveToProject`（ADR 0096）：⌘S 时这份排版还没有项目文件。表单与
 * 另存为是同一个，只是标题与按钮说「存进项目」，写的时候要求开着项目（不退回数据
 * 目录）。**存进项目的——不管从哪一屏——都绑定到那个文件**，之后 ⌘S 直接写回；
 * 从「项目里的排版」打开的同样绑定。没开项目时另存为照旧写数据目录、不绑定。
 */
export function LayoutDialog() {
  const { t } = useTranslation(['dialogs', 'common'])
  const open = useUiStore((s) => s.layoutOpen)
  const setOpen = useUiStore((s) => s.setLayoutOpen)
  /**
   * 预填的是**排版名**（`projectMeta.name`），不是激活画布的名字——后者是「Figure 1」，
   * 存成 `tavottofile/Figure 1.json` 的话项目里每一份排版都叫这个。⌘S 写回撞上冲突时
   * 预填绑定的那个文件名（`layoutName`）。
   */
  const layoutName = useDocumentStore((s) => s.projectMeta.name)
  const presetName = useUiStore((s) => s.layoutName)
  const presetConflict = useUiStore((s) => s.layoutConflict)
  const docName = presetName ?? layoutName
  /**
   * 文档落在哪个目录——**后端说了算**（`project_status.document_dir`）。
   * 「项目内 tavottofile/」这条规则的出处只有 `app.project_layout_dir()`，
   * 界面自己拼一个路径就是把它抄成了第二份，而「旧位置只读兼容」「没打开
   * 项目时退回数据目录」这两条分支抄不过去。
   */
  const documentDir = useProjectStore((s) => s.project?.document_dir)

  const intent = useUiStore((s) => s.layoutIntent)
  const toProject = intent === 'saveToProject'
  const saving = intent === 'save' || toProject
  const [names, setNames] = useState<string[]>([])
  /** 每份的修改时间（epoch 秒）；老后端没有这个字段 = 空表，行上不写日期 */
  const [modified, setModified] = useState<Record<string, number>>({})
  /** 清单取不回来：加载错误放在正文顶部（与保存失败分开，那个放在页脚上方） */
  const [listError, setListError] = useState<string | null>(null)
  const [name, setName] = useState(docName)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /**
   * 磁盘上那个名字下已经有一份**不是本窗口写的**内容（后端 409）。
   * 这不是错误，是一个待用户裁决的岔口：出口只有「覆盖」一条，而覆盖要
   * 拿 409 里回的 hash 当基线（ADR 0024 §3c——**不是清空基线**：清空等于
   * 用户按一次覆盖就把这个名字的外部修改检测永久关掉了）。
   */
  const [conflict, setConflict] = useState<{
    name: string
    revision: string
    summary: DiskDocumentSummary | null
  } | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  /** 保存进行中（同步可读，不等渲染）：挡回车连按造成的并发写 */
  const inFlight = useRef(false)
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => {
    if (!open) return
    setName(docName)
    setError(null)
    setListError(null)
    setConflict(presetConflict)
    // 另存那一屏也要这份清单：撞名的裁决在后端，但「这个名字已经有了」
    // 要在用户按下按钮之前就说
    fetchLayoutList()
      .then((r) => {
        setNames(r.names)
        setModified(r.modified)
      })
      .catch((e) => setListError(backendErrorText(e)))
  }, [open, docName, presetConflict])

  // 从菜单进来时焦点直接落在用户选的那件事上。
  // 要等一帧：弹窗自己的焦点陷阱在挂载后也会抢焦点，抢早了会被它覆盖。
  useEffect(() => {
    if (!open) return
    const id = requestAnimationFrame(() => {
      if (saving) {
        nameRef.current?.focus()
        nameRef.current?.select()
      } else {
        listRef.current?.querySelector('button')?.focus()
      }
    })
    return () => cancelAnimationFrame(id)
  }, [open, saving, names.length])

  /**
   * `overwrite` = 用户在冲突提示上按了「覆盖」，带上 409 里回的那份 hash 与**冲突那份文件的名字**。
   * 没有它时基线是本窗口读到 / 写成功过的那一份；一次都没确认过就发
   * `REVISION_ABSENT`——后端于是把「磁盘上有一份我从没读过的内容」判成冲突。
   *
   * 覆盖是裁决冲突，不是改名：写的是冲突那份文件（可能是后端净化过的规范名 `Untitled_layout`），
   * **不动文档标题**——拿它当名字字段送进来的话，`renameProject` 会把标题静默改成规范名
   * （#674 评审第 5 轮）。另存为那一路撞出的冲突，标题在第一次提交时已经改过了。
   */
  const doSave = async (overwrite?: { revision: string; target: string }) => {
    const stem = overwrite ? overwrite.target : name.trim()
    if (!stem) return
    // 回车连按两下：`busy` 要等下一次渲染才进闭包，两次提交会带着同一个基线并发写同一个
    // 文件，后到的那次撞上前一次刚写成的修订号，回一个「被别处改过」——而别处就是自己
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError(null)
    setConflict(null)
    // 点下另存为那一刻的上下文：await 之后的一切落账（修订号、绑定、冲突岔口）都记在它名下
    // （`saveContext.ts`）。改名只动文档名，不换排版、不换载入代次，先取后取一样
    const ctx = captureSaveContext()
    const pj = ctx.pj
    try {
      // 保存整份文档（schema 3，含全部画布）；文件名即文档名
      if (!overwrite) useDocumentStore.getState().renameProject(stem)
      const edited = projectFileSnapshot()
      const baseRevision = overwrite?.revision ?? knownLayoutRevision(stem, pj) ?? REVISION_ABSENT
      const res = await saveLayout(
        stem,
        useDocumentStore.getState().buildProject(),
        baseRevision,
        toProject ? { target: 'project' } : undefined,
      )
      // 写成了：立刻说「排版写成了」，时间线打「保存」点（ADR 0101 §7）——在后面任何一步
      // （关对话框、记账、状态条）之前，与它们的成败无关（Codex #679）；点属于点下另存为的
      // 那一份（`ctx.moment`：入口取的快照，与上面序列化的是同一刻）。存进项目的是项目文件，
      // 另存为画布文件的是画布文件
      emitLayoutSaved(toProject ? 'project_file' : 'layout_file', { moment: ctx.moment })
      // await 之后（清单见 saveContext.ts）：对话框是此刻界面上的东西，切走了就不替别人关——
      // 先判，下面记账会改绑定，改完之后 ctx 本来就不再「当前」
      ifStillCurrent(ctx, () => setOpen(false))
      // 写成了是事实：照样记在 ctx 名下
      // 后端净化过的名字（空格 → `_`）：之后按输入名查修订号也要落到同一份上
      rememberLayoutName(stem, res.name)
      rememberLayoutRevision(res.name ?? stem, res.revision, pj)
      // 存进了项目（后端交回了项目里的相对路径）→ 绑定，之后 ⌘S 写回这个文件
      if (pj && res.file) {
        setProjectFile(
          {
            projectId: pj,
            name: res.name ?? stem,
            file: res.file,
            revision: res.revision,
            dirty: edited(),
          },
          ctx.documentId,
        )
      }
      // 状态条是全局通知、话里点名了写到哪：切走了也照说（结果要说出来）
      useUiStore
        .getState()
        .setStatus(
          res.file
            ? msg('save.doneProject', { file: res.file }, 'workspace')
            : msg('layout.saved', { name: stem }, 'dialogs'),
          'done',
        )
    } catch (e) {
      const revision =
        e instanceof ApiError && e.status === 409 && e.body.code === 'external_change'
          ? e.body.revision
          : null
      // 岔口 / 错误进对话框：只在原来那份还开着时。切走了岔口不给——此刻按「仍然覆盖」会把
      // 请求发到新项目的同名文件上——改在状态条上说
      const shown = ifStillCurrent(ctx, () => {
        if (typeof revision === 'string') {
          setConflict({
            name: stem,
            revision,
            summary: (e as ApiError).body.summary as DiskDocumentSummary | null,
          })
        } else {
          setError(backendErrorText(e))
        }
      })
      if (!shown) {
        useUiStore
          .getState()
          .setStatus(
            typeof revision === 'string'
              ? msg('layout.projectSwitched', undefined, 'dialogs')
              : literal(backendErrorText(e)),
            'error',
          )
      }
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  const doLoad = async (target: string) => {
    setBusy(true)
    setError(null)
    setConflict(null)
    // 读的是**这个**项目里的那份：读回来之前切走了，就不再把它当成新项目的文件打开
    const ctx = captureSaveContext()
    const pj = ctx.pj
    try {
      const { doc, revision, file } = await fetchLayout(target)
      // 读到了就记下基线：之后覆盖这个名字不必再打扰用户一次（事实，记在 ctx 名下）
      rememberLayoutRevision(target, revision, pj)
      // 切走了就不打开、不碰此刻的对话框，在状态条上说出来
      if (!stillCurrent(ctx)) {
        useUiStore.getState().setStatus(msg('layout.openSkipped', { name: target }, 'dialogs'), 'error')
        return
      }
      // 从项目里打开的排版绑定那个文件：之后 ⌘S 写回它，基线是这次读到的那一份
      await openLayoutDocument(
        normalizeLayout(doc, target),
        pj && file ? { projectId: pj, name: target, file, revision, dirty: false } : undefined,
      )
      setOpen(false)
    } catch (e) {
      if (!ifStillCurrent(ctx, () => setError(backendErrorText(e)))) {
        useUiStore.getState().setStatus(literal(backendErrorText(e)), 'error')
      }
    } finally {
      setBusy(false)
    }
  }

  /** 撞名岔口上的「改名」：回到名字框、全选，让用户换一个名字再存（安全答案，也是 Esc） */
  const rename = () => {
    setConflict(null)
    requestAnimationFrame(() => {
      nameRef.current?.focus()
      nameRef.current?.select()
    })
  }
  /** 打开列表的方向键：↑↓ 在行间走（Enter / 空格是按钮自己的「打开」） */
  const onListKey = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    const rows = [...(listRef.current?.querySelectorAll<HTMLButtonElement>('[data-layout-row]') ?? [])]
    const i = rows.indexOf(document.activeElement as HTMLButtonElement)
    const next = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))]
    if (next) {
      e.preventDefault()
      next.focus()
    }
  }

  // 页脚（2026-10-07 设计审计 §10.2）：撞名时岔口进页脚——「改名」(secondary) + 「覆盖」(危险浅底胶囊)，
  // 不再是正文里一颗红字小按钮；「打开」那一屏是浏览型，没有页脚（×、Esc 关）
  const footer = conflict
    ? {
        secondary: (
          <Button variant="secondary" size="lg" disabled={busy} data-layout-rename onClick={rename}>
            {t('dialogs:layout.rename')}
          </Button>
        ),
        primary: (
          <Button
            variant="danger-tinted"
            size="lg"
            loading={busy}
            data-layout-overwrite
            onClick={() => doSave({ revision: conflict.revision, target: conflict.name })}
          >
            {t('dialogs:layout.overwrite')}
          </Button>
        ),
      }
    : saving
      ? {
          secondary: (
            <Button variant="secondary" size="lg" disabled={busy} onClick={() => setOpen(false)}>
              {t('common:actions.cancel')}
            </Button>
          ),
          primary: (
            <Button
              variant="primary"
              size="lg"
              data-layout-save
              disabled={!name.trim()}
              loading={busy}
              loadingLabel={t('dialogs:layout.saving')}
              onClick={() => doSave()}
            >
              <Save size={ICON_SIZE.sm} />
              {t(toProject ? 'dialogs:layout.saveToProject' : 'dialogs:layout.saveAs')}
            </Button>
          ),
        }
      : undefined

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      // Esc 的安全答案：撞名岔口上是「改名」（绝不是覆盖），其余是关掉
      onEscape={busy ? undefined : conflict ? rename : () => setOpen(false)}
      title={t(
        toProject
          ? 'dialogs:layout.saveToProjectTitle'
          : saving
            ? 'dialogs:layout.saveTitle'
            : 'dialogs:layout.openTitle',
      )}
      size="md"
      busy={busy}
      anchor="layout"
      footer={footer}
    >
      <div className="flex flex-col gap-3">
        {/* 加载错误放在顶部：清单都没取回来，下面的东西都不可信 */}
        {listError && <Notice tone="danger">{listError}</Notice>}
        {saving ? (
          /* 标签在左、控件在右（全面打磨 D29，L1）：全站表单都是这一副 */
          <div className="flex flex-col gap-1.5">
            <FormRow label={t('dialogs:layout.nameLabel')}>
              <TextInput
                id="layout-save-name"
                ref={nameRef}
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void doSave()
                }}
                placeholder={t('dialogs:layout.namePlaceholder')}
                aria-label={t('dialogs:layout.nameLabel')}
                className="min-w-0 flex-1"
              />
            </FormRow>
            {/* 位置：另存要回答的第二件事。后端没给就不编一个出来。
                只写**末级目录**（全面打磨 D29）：一条绝对路径末尾必被截掉，
                而末尾正是能认出「这是哪个目录」的那一段（与设置页的 `PathValue` 同一份
                `dirTail` 判据，完整路径在 title 里） */}
            {documentDir && (
              <FormRow label={t('dialogs:layout.savesIntoLabel')}>
                <span className="min-w-0 flex-1 truncate font-mono text-sm text-ink-3" title={documentDir}>
                  {dirTail(documentDir)}
                </span>
              </FormRow>
            )}
            {!conflict && names.includes(canonicalLayoutName(name.trim())) && (
              <p className="text-sm text-ink-2">{t('dialogs:layout.nameTaken')}</p>
            )}
            {toProject && !conflict && <p className="text-sm text-ink-3">{t('dialogs:layout.saveToProjectHint')}</p>}
          </div>
        ) : names.length === 0 ? (
          !listError && <p className="py-2 text-ink-3">{t('dialogs:layout.empty')}</p>
        ) : (
          /* 40px 的列表行（listRowClass）：名字 + 修改时间，hover / 聚焦时行尾浮出「打开」；↑↓ 走行、Enter 打开 */
          <ul ref={listRef} className="-mx-1 flex max-h-80 flex-col gap-0.5 overflow-y-auto" onKeyDown={onListKey}>
            {names.map((n) => (
              <li key={n} className="flex">
                <button
                  type="button"
                  data-layout-row={n}
                  disabled={busy}
                  onClick={() => doLoad(n)}
                  className={cn(
                    listRowClass({ size: 'md' }),
                    'min-h-10 w-full gap-2.5 px-2 text-left disabled:opacity-40',
                  )}
                >
                  <FolderOpen size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />
                  <span className="min-w-0 flex-1 truncate">{n}</span>
                  {modified[n] !== undefined && (
                    <span className={cn(rowMetaClass(), 'shrink-0 group-hover:hidden group-focus-visible:hidden')}>
                      {formatRelativeTime(modified[n] * 1000)}
                    </span>
                  )}
                  <span className="hidden shrink-0 text-sm text-ink-2 group-hover:inline group-focus-visible:inline">
                    {t('dialogs:layout.load')}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* 撞名：一条警示放在页脚上方，裁决（改名 / 覆盖）在页脚 */}
        {conflict && (
          <Notice tone="warn" data-layout-conflict>
            {t('dialogs:layout.conflict', { name: conflict.name })}
            {conflict.summary && (
              <span className="block text-ink-2">
                {t('dialogs:layout.conflictDisk', {
                  objects: conflict.summary.objects,
                  canvases: conflict.summary.canvases,
                })}
              </span>
            )}
          </Notice>
        )}

        {error && <Notice tone="danger">{error}</Notice>}
      </div>
    </Dialog>
  )
}
