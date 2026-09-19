/**
 * 图例项「断开」是一次结构性动作（#414）：`binding = custom` 连同此刻的五条示意线
 * 样式**一次 commit** 写进文档——一条撤销、一次渲染。引擎侧脱开的项 = 脚本原样 +
 * 文档里的 handle_*，「定格此刻」全靠这五条 override；少写一条，重开后那一维就退回
 * 脚本原样。「恢复跟随」把它们全部删掉，两条动作互为逆。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { EditableField, ManifestElement } from '@/lib/api'
import { detachLegendEntry, restoreLegendEntryFollow } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { emptyProject, type PanelObject } from '@/types/document'

const renderSpy = vi.fn()

const panel = (overrides: unknown[] = []): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 10,
    y: 20,
    w: 100,
    h: 80,
    fileId: 'f1.pdf',
    fileKind: 'pdf',
    nativeW: 200,
    nativeH: 160,
    script: 'fig.py',
    overrides,
  }) as unknown as PanelObject

const f = (prop: string, type: EditableField['type'], value: unknown): EditableField =>
  ({ prop, type, value, group: '图例项' }) as EditableField

/** 一个跟随中的图例项，示意线是 Line2D：五条样式字段齐全 */
const entry: ManifestElement = {
  gid: 'axes_0.legend.texts_0',
  role: 'legend_text',
  label: '图例项 “sin”',
  bbox: [0.65, 0.12, 0.2, 0.04],
  draggable: false,
  editable: [
    f('text', 'text', 'sin'),
    f('binding', 'enum', 'follow_source'),
    f('handle_color', 'color', '#00ff00'),
    f('handle_linestyle', 'enum', '--'),
    f('handle_linewidth', 'number', 2.5),
    f('handle_marker', 'enum', 'o'),
    f('handle_markersize', 'number', 4),
    f('visible', 'bool', true),
  ],
  legend_entry: { index: 0, source_gid: 'axes_0.lines_0', binding_default: 'follow_source' },
}

const s = () => useDocumentStore.getState()
const livePanel = () => s().doc.objects.find((o) => o.id === 'p1') as PanelObject
const ov = (prop: string) =>
  livePanel().overrides.find((o) => o.gid === entry.gid && o.prop === prop)?.value

beforeEach(async () => {
  localStorage.clear()
  renderSpy.mockReset()
  useRenderStore.getState().clear()
  useRenderStore.setState({ render: renderSpy })
  await s().switchDocument(emptyProject(), 'd_detach')
  s().commit(literal('加'), (d) => {
    d.objects.push(panel([{ gid: 'axes_0.lines_0', prop: 'color', value: '#00ff00' }]))
  })
})

describe('detachLegendEntry', () => {
  it('一次 commit 写下 binding=custom 与此刻的五条样式，撤销一次全部回去', () => {
    const before = s().past.length
    detachLegendEntry('p1', entry)
    expect(ov('binding')).toBe('custom')
    expect(ov('handle_color')).toBe('#00ff00')
    expect(ov('handle_linestyle')).toBe('--')
    expect(ov('handle_linewidth')).toBe(2.5)
    expect(ov('handle_marker')).toBe('o')
    expect(ov('handle_markersize')).toBe(4)
    expect(s().past.length).toBe(before + 1)
    expect(renderSpy).toHaveBeenCalledTimes(1)
    // 源的 override 不受影响
    expect(livePanel().overrides.find((o) => o.gid === 'axes_0.lines_0')?.value).toBe('#00ff00')

    s().undo()
    expect(livePanel().overrides.filter((o) => o.gid === entry.gid)).toEqual([])
  })

  it('恢复跟随把断开写下的六条全部删掉：两条动作互为逆', () => {
    detachLegendEntry('p1', entry)
    expect(livePanel().overrides.filter((o) => o.gid === entry.gid)).toHaveLength(6)
    restoreLegendEntryFollow('p1', entry)
    expect(livePanel().overrides.filter((o) => o.gid === entry.gid)).toEqual([])
  })
})
