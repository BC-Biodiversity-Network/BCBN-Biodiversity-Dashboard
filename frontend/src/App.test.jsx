import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// The page with its parts swapped for stand-ins: the map records what it is
// given, the search box and the card are buttons, and the overview files
// load as empty.
const seen = vi.hoisted(() => ({ map: null }))

vi.mock('./HexMap', () => ({
  // A stand-in map that only records its props.
  default: function HexMap(props) {
    seen.map = props
    return null
  },
}))
vi.mock('./SearchBox', () => ({
  // A stand-in search box: one button that picks the robin.
  default: function SearchBox({ onSelect }) {
    return <button type="button" onClick={() => onSelect({ specieskey: '59PMB', col_name: 'Turdus migratorius' })}>Pick the robin</button>
  },
}))
vi.mock('./SpeciesCard', () => ({
  // A stand-in card: one button that closes it.
  default: function SpeciesCard({ onClose }) {
    return <button type="button" onClick={onClose}>Close the card</button>
  },
}))
vi.mock('./loadHexData', () => ({
  // The overview files, empty.
  loadHexData: async () => [],
}))

// Imported after the stand-ins above, which vitest puts in place first.
import App from './App'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the page', () => {
  it('starts on all years, takes its year bounds from /api/meta, and keeps a range when a card closes', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200, json: async () => ({ first_year: 1784, last_year: 2026 }),
    })))
    render(<App />)
    await act(async () => {})
    expect(seen.map.years).toBeNull()
    expect(seen.map.yearBounds).toEqual({ firstYear: 1784, lastYear: 2026 })

    act(() => seen.map.onYearsChange({ from: 2000, to: 2020 }))
    act(() => screen.getByRole('button', { name: 'Pick the robin' }).click())
    expect(seen.map.species.key).toBe('59PMB')
    expect(seen.map.years).toEqual({ from: 2000, to: 2020 })

    act(() => screen.getByRole('button', { name: 'Close the card' }).click())
    expect(seen.map.species).toBeNull()
    expect(seen.map.years).toEqual({ from: 2000, to: 2020 })
  })
})
