import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { TextInput } from '@/components/ui/Input'
import { VERSION_NAME_MAX, backendErrorText } from '@/lib/api'
import { saveNamedNode } from '@/lib/timelineCheckpoint'
import { afterAwait, timelineCtxKey } from '@/lib/timelineContext'
import { useInFlight } from '@/hooks/useInFlight'
import { useDocumentStore } from '@/store/documentStore'
import { useTimelineStore } from '@/store/timelineStore'

/**
 * ⌥⌘S / 命令面板「把现在存为命名节点…」的就地小框（ADR 0101 修订，2026-10-01）：
 * 浮在工作面板顶部居中（挂在 `[data-work-panel]` 内部，居中于面板自己），**不打开时间线抽屉**。名字框 + 「存为命名节点」，回车保存、
 * Esc / 点外面关闭；成功后关闭（`saveNamedNode` 自带状态条提示），失败（命名节点超上限
 * 的 409）那句话留在小框里、名字不丢。开关是 `timelineStore.namingOpen`。
 *
 * 失败那句话与草稿都记在所属上下文（项目代际 + 排版 id）名下：A 排版里存失败 / 敲了一半的
 * 名字，不挂到换上来的 B 下面（Codex #679 同形状扫查，与时间线抽屉同一套记账）。
 */
export function NamedNodeQuickBox() {
  const open = useTimelineStore((s) => s.namingOpen)
  return open ? <QuickBox /> : null
}

function QuickBox() {
  const { t } = useTranslation('dialogs')
  const gen = useTimelineStore((s) => s.gen)
  const docId = useDocumentStore((s) => s.documentId)
  const ctx = timelineCtxKey(gen, docId)
  const [draft, setDraft] = useState<{ ctx: string; text: string } | null>(null)
  const name = draft?.ctx === ctx ? draft.text : ''
  const [failure, setFailure] = useState<{ ctx: string; text: string } | null>(null)
  const error = failure?.ctx === ctx ? failure.text : null
  const [busyCtx, setBusyCtx] = useState<string | null>(null)
  const busy = busyCtx === ctx
  const submitOnce = useInFlight()
  const boxRef = useRef<HTMLDivElement>(null)
  const busyRef = useRef(false)
  busyRef.current = busy
  const close = () => useTimelineStore.getState().setNamingOpen(false)

  // 关闭（Esc / 点外面 / 保存成功）后把焦点还给打开前的元素，键盘用户不落到 body。
  // 不用 `ui/Popover`：它必须有触发器、焦点还给触发器，而这个小框由快捷键 / 命令面板打开，
  // 没有触发器；`ui/Dialog` 是模态，盖住画布不合适。所以在这里记下打开前的 activeElement。
  // render 阶段读：此刻 `autoFocus` 还没发生（命令面板自己关闭后元素已不在文档里则不还）
  const prevFocus = useRef<Element | null>(null)
  if (prevFocus.current === null) prevFocus.current = document.activeElement
  useEffect(
    () => () => {
      const el = prevFocus.current
      if (el instanceof HTMLElement && el.isConnected) el.focus()
    },
    [],
  )

  // 点外面关闭（在途时不关：名字还没落盘）
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (busyRef.current) return
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) close()
    }
    document.addEventListener('pointerdown', onDown, true)
    return () => document.removeEventListener('pointerdown', onDown, true)
  }, [])

  const save = async () => {
    if (!name.trim() || busy) return
    const at = ctx
    // 在途时回车 / 再点一次都不再发（`useInFlight`：与时间线抽屉同一份）
    await submitOnce(at, async () => {
      const after = afterAwait(at)
      setBusyCtx(at)
      try {
        await saveNamedNode(name)
        // 换走之后才回来的，不关 B 里开着的小框
        after(() => close())
      } catch (e) {
        // 旧上下文的失败不碰错误槽：槽只有一个，写进来会顶掉 B 自己的错误
        after(() => setFailure({ ctx: at, text: backendErrorText(e) }))
      } finally {
        setBusyCtx((c) => (c === at ? null : c))
      }
    })
  }

  return (
    <div
      ref={boxRef}
      role="dialog"
      aria-label={t('versions.save')}
      data-timeline-quick-name
      onKeyDown={(e) => {
        if (e.key === 'Escape' && !busy) {
          e.stopPropagation()
          close()
        }
      }}
      className="absolute left-1/2 top-12 z-overlay w-[360px] max-w-[92vw] -translate-x-1/2 rounded-lg bg-surface p-3 shadow-pop"
    >
      <form
        className="flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <div className="flex gap-1.5">
          <TextInput
            autoFocus
            value={name}
            aria-label={t('versions.versionName')}
            placeholder={t('versions.namePlaceholder')}
            data-timeline-quick-name-input
            maxLength={VERSION_NAME_MAX}
            onChange={(e) => setDraft({ ctx, text: e.target.value })}
            className="min-w-0 flex-1"
          />
          <Button type="submit" variant="primary" size="sm" loading={busy} disabled={!name.trim()} data-timeline-quick-name-save>
            {t('versions.save')}
          </Button>
        </div>
        <p className="text-xs text-ink-3">{t('versions.nameHint')}</p>
        {error && (
          <p role="alert" className="text-xs leading-relaxed text-danger">
            {error}
          </p>
        )}
      </form>
    </div>
  )
}
