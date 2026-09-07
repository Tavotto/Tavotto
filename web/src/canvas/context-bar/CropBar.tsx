import { Check, RotateCcw, X } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { Button } from '@/components/ui/Button'
import { Tip } from '@/components/ui/Tooltip'
import { cancelCrop, finishCrop, resetPanelCrop } from '@/store/actions'
import { Sep } from './shared'

/**
 * 裁剪态的「完成 / 取消」，贴在被裁的面板旁边（审计 T26）。
 *
 * 以前这两件事只在右栏属性页里：一颗按钮在「完成裁剪」与「裁剪」之间换字，
 * **没有取消**——Esc 退出裁剪态，但这一轮拖出来的取景窗留下了。用户离选区
 * 一整个画布的距离去点一颗改了名的按钮，还找不到反悔的路。
 *
 * 现在两颗都在手边，键盘同样走得通：Enter = 完成、Esc = 取消（
 * `hooks/useKeyboard`，与这里调的是同一对 action）。取消还原到**进裁剪那一刻**，
 * 不是还原到「从没裁剪过」——后者是「重置裁剪」，第三颗按钮，语义不混。
 *
 * 落位、拖动期间隐藏、portal 都由 `ContextBar` 外壳负责，这里只出内容。
 */
const pn = (key: string) => translate(`panel.${key}`, { ns: 'inspector' })

export function CropBar({ panelId }: { panelId: string }) {
  return (
    <span className="contents" data-crop-bar>
      <Tip label={pn('resetCropTip')} side="bottom">
        <Button
          size="sm"
          className="gap-1 px-1.5"
          data-crop-action="reset"
          onClick={() => resetPanelCrop([panelId])}
        >
          <RotateCcw size={ICON_SIZE.sm} />
          {pn('resetCrop')}
        </Button>
      </Tip>
      <Sep />
      <Tip label={pn('cropCancelTip')} side="bottom">
        <Button
          size="sm"
          className="gap-1 px-1.5"
          data-crop-action="cancel"
          onClick={cancelCrop}
        >
          <X size={ICON_SIZE.sm} />
          {translate('actions.cancel')}
        </Button>
      </Tip>
      {/* 这条工具条自己就是一个上下文，「完成」是它唯一的填色主动作 */}
      <Tip label={pn('cropDoneTip')} side="bottom">
        <Button
          variant="primary"
          size="sm"
          className="gap-1 px-1.5"
          data-crop-action="done"
          onClick={finishCrop}
        >
          <Check size={ICON_SIZE.sm} />
          {pn('cropDone')}
        </Button>
      </Tip>
    </span>
  )
}
