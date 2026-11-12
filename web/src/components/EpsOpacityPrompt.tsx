import { useCallback, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { epsTranslucentCount, swapEpsForPdf } from '@/lib/epsOpacity'
import type { ExportRequestInput } from '@/lib/exportRequest'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'

const eo = (key: string, values?: Record<string, unknown>) =>
  translate(`epsOpacity.${key}`, { ns: 'dialogs', ...(values ?? {}) })

interface Pending {
  input: ExportRequestInput
  count: number
}

/**
 * 导出前的 EPS 半透明提醒（ADR 0174 后续）：PostScript 没有透明，单图带标注导 EPS 时标注里有半透明就先问一句。
 * 三个出口：改导出 PDF / EPS 按不透明导出（EPS 里这些标注按 100% 画、其余格式不变）/ 取消。
 *
 * 逻辑都在这里，`ExportDialog` 只接两处：`gate.intercept(input, run)` 放在发请求之前，`gate.prompt` 放进渲染。
 * 必须做出选择（`blockDismiss`），Esc = 取消（安全答案：什么都不导）。
 */
export function useEpsOpacityGate(setFormats: (formats: string[]) => void): {
  /** 命中就弹提示并返回 true（调用方这次不再发）；没命中返回 false，请求原样走 */
  intercept: (input: ExportRequestInput, run: (input: ExportRequestInput) => unknown) => boolean
  prompt: ReactNode
} {
  useTranslation('dialogs')
  const [pending, setPending] = useState<(Pending & { run: (i: ExportRequestInput) => unknown }) | null>(null)
  // 关的那一小段退场动画里 pending 已经是 null，正文仍要画得出来
  const [last, setLast] = useState<number>(0)
  const primaryRef = useRef<HTMLButtonElement | null>(null)

  const intercept = useCallback(
    (input: ExportRequestInput, run: (i: ExportRequestInput) => unknown) => {
      const count = epsTranslucentCount(input)
      if (count === 0) return false
      setLast(count)
      setPending({ input, count, run })
      return true
    },
    [],
  )

  const answer = (choice: 'pdf' | 'opaque' | 'cancel') => {
    const p = pending
    setPending(null)
    if (!p || choice === 'cancel') return
    if (choice === 'pdf') {
      const formats = swapEpsForPdf(p.input.formats)
      setFormats(formats) // 对话框里的勾选跟着变，用户看得到换成了什么
      void p.run({ ...p.input, formats })
    } else {
      void p.run({ ...p.input, epsOpaque: true })
    }
  }

  const prompt = (
    <Dialog
      open={!!pending}
      onOpenChange={() => answer('cancel')}
      title={eo('title')}
      size="sm"
      blockDismiss
      onEscape={() => answer('cancel')}
      anchor="eps-opacity"
      // 盖在导出对话框上：后面再压暗一点；焦点落在主按钮上，Enter = 主按钮
      dimBehind
      initialFocusRef={primaryRef}
      footer={{
        start: (
          <Button variant="secondary" size="lg" data-eps-opacity="pdf" onClick={() => answer('pdf')}>
            {eo('pdf')}
          </Button>
        ),
        secondary: (
          <Button variant="secondary" size="lg" data-eps-opacity="cancel" onClick={() => answer('cancel')}>
            {translate('actions.cancel')}
          </Button>
        ),
        primary: (
          <Button ref={primaryRef} variant="primary" size="lg" data-eps-opacity="opaque" onClick={() => answer('opaque')}>
            {eo('opaque')}
          </Button>
        ),
      }}
    >
      <p className="text-ink-2">{eo('body', { count: pending?.count ?? last })}</p>
    </Dialog>
  )
  return { intercept, prompt }
}
