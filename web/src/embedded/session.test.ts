/**
 * 内嵌会话种子层（MCP 画布与浏览器 playground 共用的那一份）。
 * MCP 侧的完整行为在 `mcp/session.test.ts`；这里盯**通用层自身**的契约，
 * 保证第二个消费方（playground）看到的行为与 MCP 完全一致。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { msg } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { embeddedFileIdFor, prepareEmbeddedSvg, seedEmbeddedSession } from './session'

const manifest = {
  stem: 'FigP',
  size_mm: [100, 75],
  elements: [],
} as unknown as Manifest

beforeEach(() => {
  useRenderStore.getState().clear()
  useUiStore.getState().setElementPanel(null)
})

describe('seedEmbeddedSession', () => {
  it('灌进既有 stores：面板 / 素材 / 渲染态 / 编辑态，一件不多', () => {
    const { panelId, fileId } = seedEmbeddedSession(
      {
        stem: 'FigP',
        project: '/workspace',
        script: 'figp.py',
        cost: 'light',
        manifest,
        svg: '<svg width="1pt" height="1pt"><g/></svg>',
        renderRevision: 3,
      },
      msg('history.playgroundOpenFigure', undefined, 'workspace'),
    )
    expect(fileId).toBe(embeddedFileIdFor('FigP'))

    const doc = useDocumentStore.getState().doc
    const panel = doc.objects[0] as PanelObject
    expect(panel.id).toBe(panelId)
    expect([doc.page.w, doc.page.h]).toEqual([100, 75])
    expect(panel.overrides).toEqual([])
    expect(useAssetStore.getState().byId[fileId].script).toBe('figp.py')

    const entry = useRenderStore.getState().byKey[renderKey(fileId, [])]
    expect(entry.status).toBe('ready')
    expect(entry.rev).toBe(3)
    expect(entry.svg).toContain('preserveAspectRatio="none"')
    expect(useRenderStore.getState().tracked[fileId]).toBe(true)
    expect(useUiStore.getState().elementPanelId).toBe(panelId)
  })

  it('打开动作不进撤销栈', () => {
    seedEmbeddedSession(
      { stem: 'FigP', project: '/w', script: 's.py', manifest, svg: null },
      msg('history.playgroundOpenFigure', undefined, 'workspace'),
    )
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(useDocumentStore.getState().canUndo()).toBe(false)
  })
})

describe('prepareEmbeddedSvg', () => {
  it('剥掉 pt 尺寸并铺满面板框（与 renderStore 那份同一口径）', () => {
    const out = prepareEmbeddedSvg('<svg width="80pt" height="60pt" viewBox="0 0 80 60"><g/></svg>')
    expect(out).not.toContain('width="80pt"')
    expect(out).toContain('preserveAspectRatio="none"')
    expect(out).toContain('viewBox="0 0 80 60"')
  })
})
