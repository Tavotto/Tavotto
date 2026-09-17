/**
 * `import-graph.mjs` 的类型声明——只为 `src/importArchitecture*.test.ts` 能过 `tsc -b`
 * （`tsconfig.app.json` 没开 allowJs）。实现在 .mjs 里；改了导出记得同步这里。
 */
export type EdgeKind = 'static' | 'dynamic' | 'type'

export interface Edge {
  src: string
  dst: string
  kind: EdgeKind
  line: number
}

export interface Graph {
  nodes: Set<string>
  edges: Edge[]
  externals: Map<string, Set<string>>
  assets: { from: string; target: string; line: number }[]
  unresolved: { from: string; spec: string; line: number }[]
  unknownDynamic: { from: string; line: number; call: string }[]
}

export interface Cycle {
  members: string[]
  edges: string[][]
}

export interface LayerViolation {
  from: string
  to: string
  rule: string
  line: number
  kind: EdgeKind
}

export const BASELINE: string
export const LAYERS: Record<string, (node: string) => boolean>
export const LAYER_RULES: [string, string][]
export const OBSERVED_RULES: [string, string][]
export const DEFAULT_FOCUS: string[]

export function productionFiles(): string[]
export function buildGraph(): Graph
export function runtimeEdges(graph: Graph): Edge[]
export function stronglyConnectedComponents(adj: Map<string, Set<string>>): Set<string>[]
export function cycles(graph: Graph): Cycle[]
export function layerOf(node: string): string | null
export function layerViolations(graph: Graph, rules?: [string, string][]): LayerViolation[]
export function fan(graph: Graph, node: string): { ins: string[]; outs: string[] }
export function report(graph: Graph, focus?: string[]): string
