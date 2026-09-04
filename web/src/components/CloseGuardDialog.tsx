import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { answerCloseRequest, beginCloseRequest, type CloseAnswer } from '@/lib/closeGuard'
import { armDesktopCloseGuard, onDesktopCloseRequested, resolveDesktopCloseRequest } from '@/lib/desktop'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'

const cg = (key: string) => translate(`closeGuard.${key}`, { ns: 'dialogs' })

/**
 * 桌面壳的关窗三选一（issue #223，Prompt 03 §六的原始合同）。
 *
 * 壳在 `WindowEvent::CloseRequested` 上拦住窗口并发 `tavotto:close-requested`；
 * 这里回答它。**必须做出选择**（`blockDismiss`）：点外面 / Esc 都不算回答，
 * 随手关掉这个框的表现会是窗口挂在那儿，直到壳的看门狗超时。
 *
 * 浏览器模式下 `onDesktopCloseRequested()` 是空订阅，这个组件永远不出现——
 * 那条路仍归 `beforeunload`（ADR 0024）。
 */
export function CloseGuardDialog() {
  useTranslation('dialogs')
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  // 事件回调活在订阅里，读不到 open 那个 state 的最新值；用户在框开着的时候
  // 又按一次关闭按钮**会**再来一次事件，那一次只需要再接手一下、别弹第二个框。
  const asking = useRef(false)

  useEffect(() => {
    let unlisten: (() => void) | undefined
    let disposed = false
    void onDesktopCloseRequested(() => {
      if (asking.current) {
        void resolveDesktopCloseRequest('hold')
        return
      }
      asking.current = true
      void beginCloseRequest().then((ask) => {
        if (!ask) {
          // 没有未落盘的工作：已经替用户答了「关」，窗口这就没了
          asking.current = false
          return
        }
        setFailed(false)
        setOpen(true)
      })
    }).then((u) => {
      if (disposed) {
        u()
        return
      }
      unlisten = u
      // **注册成功之后**才让壳开始拦：反过来的话，两者之间的那次关闭会拦下
      // 一个没人听的问题，用户看到的是关闭按钮按了不动。
      void armDesktopCloseGuard()
    })
    return () => {
      disposed = true
      unlisten?.()
    }
  }, [])

  const answer = async (a: CloseAnswer) => {
    setBusy(a === 'save')
    const outcome = await answerCloseRequest(a)
    setBusy(false)
    // 窗口马上就没了：框留在原地，别在关闭动画里闪一下
    if (outcome === 'closing') return
    if (a === 'save') {
      // 存不成就不关（写盘失败 / 冲突未决）。说清楚为什么窗口还在，
      // 三个出口照旧留着——用户可以改主意直接关掉。
      setFailed(true)
      return
    }
    asking.current = false
    setOpen(false)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={() => void answer('cancel')}
      title={cg('title')}
      size="sm"
      busy={busy}
      blockDismiss
      footer={
        <>
          <Button variant="outline" size="md" disabled={busy} onClick={() => answer('cancel')}>
            {translate('actions.cancel')}
          </Button>
          <Button variant="danger" size="md" disabled={busy} onClick={() => answer('discard')}>
            {cg('discard')}
          </Button>
          <Button variant="primary" size="md" loading={busy} onClick={() => answer('save')}>
            {cg('save')}
          </Button>
        </>
      }
    >
      <p className="text-xs leading-relaxed text-ink-2">{cg('body')}</p>
      {failed && <p className="mt-2 text-xs leading-relaxed text-danger">{cg('saveFailed')}</p>}
    </Dialog>
  )
}
