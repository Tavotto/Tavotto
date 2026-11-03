import { describe, expect, it } from 'vitest'
import { changeFigureFont, figureFontChoice, figurePrimaryValue } from './figureFontChoice'

describe('figure font choice', () => {
  it('preserves a previous font override when returning to automatic fallback', () => {
    const choice = changeFigureFont(['Arial', 'Helvetica'], 'fallback', 'Songti SC')
    expect(choice).toEqual({ primary: ['Arial', 'Helvetica'], fallback: 'Songti SC' })
    expect(figurePrimaryValue(choice, 'serif')).toBe('Arial')
    expect(changeFigureFont(choice, 'fallback', null)).toEqual(['Arial', 'Helvetica'])
  })

  it('keeps the script primary inherited until the user changes it', () => {
    const choice = changeFigureFont(undefined, 'fallback', 'Songti SC')
    expect(figureFontChoice(choice)).toEqual({ primary: null, fallback: 'Songti SC' })
    expect(figurePrimaryValue(choice, 'Times New Roman')).toBe('Times New Roman')
    expect(changeFigureFont(choice, 'fallback', null)).toBeNull()
  })
})
