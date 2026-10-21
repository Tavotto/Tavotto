import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { aiDataFiles, type AiDataFile } from '@/lib/api'
import { t as translate } from '@/i18n'
import { isDataFigureSession, useAiStore } from '@/store/aiStore'
import { Button } from '../ui/Button'
import { TextArea } from '../ui/Input'
import { Select } from '../ui/Select'

const ai = (key: string, values?: Record<string, unknown>) => translate(key, { ns: 'ai', ...(values ?? {}) })

/**
 * 「从数据出图」的小表单（TAF Phase 5c，开关后面）：项目内 CSV 下拉 + 一句话指令 + 生成 / 取消。
 * 住在助手面板的输入区上方（Reveal 展开、不是弹窗也不是横条）；提交后收起，结果是对话里的一轮（`startDataFigure`）。
 * 打开时才拉一次文件列表；列表只读，路径是项目相对 POSIX 路径。
 */
export function DataFigureForm({ onClose }: { onClose: () => void }) {
  useTranslation('ai')
  const [files, setFiles] = useState<AiDataFile[] | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [failed, setFailed] = useState(false)
  const [file, setFile] = useState('')
  const [instruction, setInstruction] = useState('')

  useEffect(() => {
    let alive = true
    aiDataFiles().then(
      (r) => {
        if (!alive) return
        setFiles(r.files)
        setTruncated(r.truncated)
        // 只有一份时直接选上：少一步
        if (r.files.length === 1) setFile(r.files[0].path)
      },
      () => {
        if (alive) setFailed(true)
      },
    )
    return () => {
      alive = false
    }
  }, [])

  // 上一次的结果还没确认（可能已经生成）：先去对话里查询，别在这里再提交一次（会建出 `_2`）
  const pending = useAiStore((s) => s.sessions.some((x) => isDataFigureSession(x) && x.unconfirmed === true))
  const canSubmit = !!file && !!instruction.trim() && !pending
  const submit = () => {
    if (!canSubmit) return
    void useAiStore.getState().startDataFigure(file, instruction.trim())
    onClose()
  }

  return (
    <div data-ai-data-figure-form className="mb-2 flex flex-col gap-2 rounded-lg bg-surface-hover p-3">
      <p className="type-title">{ai('dataFigure.title')}</p>
      {failed ? (
        <p data-ai-data-figure-note="failed" className="text-sm text-ink-2">
          {ai('dataFigure.loadFailed')}
        </p>
      ) : files === null ? null : files.length === 0 ? (
        <p data-ai-data-figure-note="empty" className="text-sm text-ink-2">
          {ai('dataFigure.empty')}
        </p>
      ) : (
        <>
          <div data-ai-data-figure-file>
            <Select
              value={file}
              onChange={setFile}
              ariaLabel={ai('dataFigure.fileLabel')}
              placeholder={ai('dataFigure.filePlaceholder')}
              options={files.map((f) => ({ value: f.path, label: f.path }))}
            />
          </div>
          {truncated && (
            <p data-ai-data-figure-note="truncated" className="text-xs text-ink-3">
              {ai('dataFigure.truncated')}
            </p>
          )}
          {pending && (
            <p data-ai-data-figure-note="unconfirmed" className="text-xs text-ink-3">
              {ai('dataFigure.unconfirmedHint')}
            </p>
          )}
          <TextArea
            data-ai-data-figure-instruction
            aria-label={ai('dataFigure.instructionLabel')}
            placeholder={ai('dataFigure.instructionPlaceholder')}
            value={instruction}
            rows={2}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault()
                submit()
              }
            }}
          />
        </>
      )}
      <div className="flex justify-end gap-1.5">
        <Button data-ai-data-figure-cancel size="sm" onClick={onClose}>
          {ai('dataFigure.cancel')}
        </Button>
        {files !== null && files.length > 0 && (
          <Button data-ai-data-figure-submit variant="primary" size="sm" disabled={!canSubmit} onClick={submit}>
            {ai('dataFigure.generate')}
          </Button>
        )}
      </div>
    </div>
  )
}
