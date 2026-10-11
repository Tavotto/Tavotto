import type { Manifest, ManifestElement } from '@/lib/api'
import type { Rect4 } from '@/lib/axesLayout'
import type { PanelObject } from '@/types/document'

export const PATCH_BOX: Rect4 = [0.2, 0.25, 0.4, 0.25]
export const PATCH_GID = 'axes_0.patches_0'
export const PATCH_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><g id="axes_0"><g id="axes_0.patches_0"><path d="M40 25 H120 V50 H40 Z"/></g><g id="axes_0.texts_0"><text x="60" y="35">Step A</text></g><g id="axes_0.texts_1.arrow"><path d="M120 37.5 L150 37.5"/></g></g></svg>'

export function resizeElement(box: Rect4 = PATCH_BOX): ManifestElement {
  return {
    gid: PATCH_GID, role: 'patch', label: '形状 1', bbox: box, shape_bounds: box,
    shape_min_size: [0.02, 0.03], anchor: [box[0], box[1] + box[3]],
    draggable: true, drag_prop: 'pos_frac',
    editable: [
      { prop: 'size_frac', type: 'pair', value: [box[2], box[3]] },
      { prop: 'facecolor', type: 'color', value: '#ffffff' },
      { prop: 'linewidth', type: 'number', value: 1.5 },
      { prop: 'visible', type: 'bool', value: true },
    ],
    geometry: { kind: 'path', fill: true, stroke: true, paths: [{ closed: true, points: [
      [box[0], box[1]], [box[0] + box[2], box[1]],
      [box[0] + box[2], box[1] + box[3]], [box[0], box[1] + box[3]],
    ] }] },
  }
}

export function resizeManifest(element = resizeElement()): Manifest {
  return {
    stem: 'Flow', size_mm: [200, 100], elements: [
      { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
      { gid: 'axes_0', role: 'axes', label: '子图 1', bbox: [0.1, 0.1, 0.8, 0.8],
        editable: [{ prop: 'position', type: 'rect', value: [0.1, 0.1, 0.8, 0.8] }],
        draggable: true, resizable: true },
      element,
      { gid: 'axes_0.texts_0', role: 'text', label: 'Step A', bbox: [0.3, 0.3, 0.1, 0.05],
        anchor: [0.3, 0.35], draggable: true, drag_prop: 'pos_frac',
        editable: [{ prop: 'fontsize', type: 'number', value: 12 }] },
      { gid: 'axes_0.texts_1.arrow', role: 'arrow_patch', label: 'Connector', bbox: [0.6, 0.375, 0.15, 0],
        editable: [], draggable: true, arrow_endpoints: [[0.6, 0.375], [0.75, 0.375]] },
    ],
  }
}

export function resizePanel(extra: Partial<PanelObject> = {}): PanelObject {
  return {
    id: 'p1', type: 'panel', fileId: 'Flow.pdf', fileKind: 'pdf', nativeW: 200, nativeH: 100,
    x: 0, y: 0, w: 200, h: 100, script: 'flow.py', overrides: [], ...extra,
  }
}
