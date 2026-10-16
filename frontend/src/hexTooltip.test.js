import { describe, expect, it } from 'vitest'

import { getTooltip } from './hexTooltip'

// One overview hexagon, as loadHexData.js reads it from the CSV.
const HEXAGON = { h3Cell: '8412a1dffffffff', occurrences: 12345, distinctSpecies: 678 }

describe('the overview hover box', () => {
  it('shows the records and species of the hexagon', () => {
    const { html } = getTooltip({ object: HEXAGON })
    expect(html).toContain('<strong>12,345</strong> records')
    expect(html).toContain('<strong>678</strong> species')
  })

  it('does not show the H3 cell id', () => {
    expect(getTooltip({ object: HEXAGON }).html).not.toContain('8412a1dffffffff')
  })

  it('shows nothing when the pointer is over no hexagon', () => {
    expect(getTooltip({ object: null })).toBeNull()
  })
})
