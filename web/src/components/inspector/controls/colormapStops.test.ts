import { expect, test } from 'vitest'

import { colormapGradient } from './colormapStops'

test('a native binned guide preserves its unequal widths and requested direction', () => {
  const facts = {
    name: '',
    custom: true,
    discrete: true,
    stops: ['#153456', '#ABCDEF', '#923121'],
    boundaries: [0, 0.125, 0.75, 1],
  }
  expect(colormapGradient('', facts, 'to left')).toBe(
    'linear-gradient(to left, #153456 0.00%, #153456 12.50%, #ABCDEF 12.50%, #ABCDEF 75.00%, #923121 75.00%, #923121 100.00%)',
  )
})
