/**
 * 「问题在界面上怎么说」的看护（ADR 0030）。
 *
 * 这一层的全部价值就是**不把内部标识说给用户听**，外加把「当前值 / 要求」
 * 从描述符里取对。所以用例只量这两件事，外加双语。
 */
import { describe, expect, it, afterEach } from 'vitest'
import { setLocale } from '@/i18n'
import { SEVERITY_ICON, issueTitle, issueValues, severityLabel, subjectName, technicalDetailLines } from './validationText'
import { SEVERITIES } from './profile'
import type { ValidationIssue } from './validation'

const issue = (over: Partial<ValidationIssue> = {}): ValidationIssue => ({
  issueId: 'f',
  ruleCode: 'font-too-small',
  severity: 'warn',
  context: 'document',
  objectRef: { documentId: 'd', canvasId: 'c', objectId: 'p1', gid: 'axes_0.xlabel' },
  subject: { kind: 'element', elementLabel: 'X 轴标题', elementRole: 'axis_label' },
  propertyPath: 'fontsize',
  message: { key: 'preflight.fontTooSmall', ns: 'errors', values: { effective: '7.50', min: '8' } },
  technicalDetails: { effective_pt: 7.5, min_pt: 8 },
  fixKind: 'safe_auto',
  ...over,
})

afterEach(() => setLocale('zh-CN'))

describe('主语说人话', () => {
  it('图内元素用引擎给的标签，不是 gid', () => {
    expect(subjectName(issue())).toBe('X 轴标题')
  })

  it('拿不到标签就退到角色名——任何一档都不吐 gid', () => {
    const s = subjectName(issue({ subject: { kind: 'element', elementRole: 'legend' } }))
    expect(s).not.toContain('axes_0')
    expect(s).toBeTruthy()
  })

  it('连角色都没有时说面板名，再没有才说一句通用的', () => {
    expect(subjectName(issue({ subject: { kind: 'element', objectName: '图 1' } }))).toBe('图 1')
    expect(subjectName(issue({ subject: { kind: 'element' } }))).toBe('图内元素')
  })

  it('页面级问题的主语是整张画布', () => {
    expect(subjectName(issue({ subject: { kind: 'page' } }))).toBe('整张画布')
  })

  it('画布对象按类型说话', () => {
    expect(subjectName(issue({ subject: { kind: 'object', objectType: 'arrow' } }))).toBe('箭头')
    expect(subjectName(issue({ subject: { kind: 'object', objectType: 'text' } }))).toBe('标注文字')
  })
})

describe('当前值 → 要求', () => {
  it('字号带单位，要求那一侧说清是「不低于」还是「大于」', () => {
    expect(issueValues(issue())).toEqual({ current: '7.50pt', expected: '≥ 8pt' })
    const floor = issue({
      ruleCode: 'font-below-absolute-floor',
      message: {
        key: 'preflight.fontBelowFloor',
        ns: 'errors',
        values: { effective: '8.00', floor: '8' },
      },
    })
    // 绝对下限**不含等号**：这句话必须说成"大于"，说成"≥"就是在骗人
    expect(issueValues(floor).expected).toBe('大于 8pt')
  })

  it('画布标注那两条用的是 size 参数，同一条规则两种主语', () => {
    const t = issue({
      message: { key: 'preflight.textTooSmall', ns: 'errors', values: { size: '5', min: '8' } },
    })
    expect(issueValues(t).current).toBe('5pt')
  })

  it('没登记的规则不硬编数字，两侧都回 null', () => {
    expect(issueValues(issue({ ruleCode: 'overlap', message: { key: 'preflight.overlap', ns: 'errors' } }))).toEqual({
      current: null,
      expected: null,
    })
  })
})

describe('短标题与技术详情', () => {
  it('短标题按 rule code 查；没登记的退回完整成文，而不是显示 code', () => {
    expect(issueTitle(issue())).toBe('字号偏小')
    const unknown = issueTitle(issue({ ruleCode: 'zzz-unknown' }))
    expect(unknown).not.toContain('zzz-unknown')
    expect(unknown).toContain('7.50')
  })

  it('gid / 对象 id / 属性名只出现在技术详情里', () => {
    const lines = technicalDetailLines(issue())
    expect(lines.join('\n')).toContain('axes_0.xlabel')
    expect(lines.join('\n')).toContain('p1')
    expect(lines.join('\n')).toContain('fontsize')
    // 短标题与主语那两句一个都不许带
    expect(issueTitle(issue())).not.toContain('axes_0')
    expect(subjectName(issue())).not.toContain('axes_0')
  })
})

describe('等级', () => {
  it('四个等级各有图标与文字标签——颜色不是唯一表达', () => {
    for (const s of SEVERITIES) {
      expect(SEVERITY_ICON[s]).toBeTruthy()
      expect(severityLabel(s)).toBeTruthy()
    }
  })

  it('切语言之后跟着换（存的是 key，不是翻好的字符串）', () => {
    expect(issueTitle(issue())).toBe('字号偏小')
    setLocale('en-US')
    expect(issueTitle(issue())).toBe('Font too small')
    expect(subjectName(issue({ subject: { kind: 'page' } }))).toBe('The whole canvas')
  })
})
