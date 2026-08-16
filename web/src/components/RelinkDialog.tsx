import { useEffect, useState } from 'react'
import { Link2, TriangleAlert } from 'lucide-react'
import {
  materializePaste,
  materializeRelink,
  useClipboardStore,
  type MissingAsset,
} from '@/lib/clipboard'
import { useAssetStore } from '@/store/assetStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Select } from './ui/Select'

/**
 * 缺失素材处置：粘贴（跨图库剪贴板）与项目包导入共用。
 * 要么重新链接到现有素材，要么明确跳过——绝不静默生成空面板。
 */
export function RelinkDialog() {
  const pending = useClipboardStore((s) => s.pending)
  const setPending = useClipboardStore((s) => s.setPending)
  const assets = useAssetStore((s) => s.byId)
  const [choices, setChoices] = useState<MissingAsset[]>([])

  useEffect(() => {
    if (!pending) return
    // 预填：同名素材自动匹配（常见于同图库不同机器的相对路径差异）
    setChoices(
      pending.missing.map((m) => {
        const match = Object.values(assets).find(
          (a) => a.name === m.name || a.id.endsWith(`/${m.name}.pdf`) || a.id.endsWith(`/${m.name}.png`),
        )
        return { ...m, relinkTo: match?.id }
      }),
    )
  }, [pending, assets])

  if (!pending) return null
  const isPaste = pending.mode === 'paste'

  const options = Object.values(assets)
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((a) => ({ value: a.id, label: `${a.name}（${a.folder}）` }))

  const confirm = () => {
    if (pending.mode === 'paste') materializePaste(pending.payload, choices)
    else materializeRelink(choices)
    setPending(null)
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && setPending(null)}
      title={isPaste ? '粘贴的面板缺少素材' : '文档里的面板缺少素材'}
      description={
        isPaste
          ? '剪贴板里的面板引用了当前图库中不存在的文件。逐个选择替代素材，或跳过该面板。'
          : '布局引用了当前图库中不存在的文件（常见于换机器打开项目包）。逐个选择替代素材；不处理的面板会保持缺失状态并在导出前检查里提示。'
      }
      size="lg"
      footer={
        <>
          <Button variant="outline" size="md" onClick={() => setPending(null)}>
            {isPaste ? '取消粘贴' : '暂不处理'}
          </Button>
          <Button variant="primary" size="md" onClick={confirm}>
            <Link2 size={14} />
            {isPaste ? '按上述处置粘贴' : '重新链接所选素材'}
          </Button>
        </>
      }
    >
      <ul className="flex flex-col gap-2">
        {choices.map((m, i) => (
          <li key={m.fileId} className="flex items-center gap-2">
            <TriangleAlert size={13} className="shrink-0 text-danger" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs text-ink" title={m.fileId}>
                {m.name}
                {m.count > 1 && <span className="text-ink-3">（{m.count} 个面板引用）</span>}
              </p>
              <p className="truncate text-xs text-ink-3">{m.fileId}</p>
            </div>
            <div className="w-56 shrink-0">
              <Select
                value={m.relinkTo ?? ''}
                placeholder={isPaste ? '跳过该面板' : '保持缺失'}
                onChange={(v) =>
                  setChoices((cs) =>
                    cs.map((c, j) => (j === i ? { ...c, relinkTo: v || undefined } : c)),
                  )
                }
                options={[
                  { value: '', label: isPaste ? '跳过该面板' : '保持缺失' },
                  ...options,
                ]}
                ariaLabel={`为 ${m.name} 选择替代素材`}
              />
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs leading-relaxed text-ink-3">
        重新链接会按新素材的尺寸与脚本重置图内修改（overrides 绑定在原脚本的元素上，
        跨素材搬运不可靠）；位置、大小、层级与成组关系保留。
      </p>
    </Dialog>
  )
}
