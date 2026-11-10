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
  // A stand-in card: a button that closes it, one that loads it, and one
  // that opens the datasets that mention it.
  default: function SpeciesCard({ requestedKey, onClose, onLoaded, onOpenDatasets }) {
    return (
      <>
        <button type="button" onClick={onClose}>Close the card</button>
        <button type="button" onClick={() => onLoaded({ requested_key: requestedKey,
          name: { col_name: 'Oncorhynchus nerka' } })}>Load the card</button>
        <button type="button" onClick={() => onOpenDatasets(requestedKey)}>Open its datasets</button>
      </>
    )
  },
}))
vi.mock('./loadHexData', () => ({
  // The overview files, empty.
  loadHexData: async () => [],
}))

// Imported after the stand-ins above, which vitest puts in place first.
import App from './App'
import { useLocationSearch } from './route'

// The page as the site shows it: the address read and changed through the
// browser's history.
function Page() {
  const [search, navigate] = useLocationSearch()
  return <App search={search} navigate={navigate} />
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the page', () => {
  it('starts on all years, takes its year bounds from /api/meta, and keeps a range in the address', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200, json: async () => ({ first_year: 1784, last_year: 2026 }),
    })))
    window.history.replaceState(null, '', '/')
    render(<Page />)
    await act(async () => {})
    expect(seen.map.years).toBeNull()
    expect(seen.map.yearBounds).toEqual({ firstYear: 1784, lastYear: 2026 })

    // A new range goes into the address, in place of the current entry.
    const before = window.history.length
    act(() => seen.map.onYearsChange({ from: 2000, to: 2020 }))
    expect(window.location.search).toBe('?year_from=2000&year_to=2020')
    expect(window.history.length).toBe(before)
    const firstYears = seen.map.years
    expect(firstYears).toEqual({ from: 2000, to: 2020 })

    // Opening and closing a card keeps the range, as the same object, so
    // the map does not load it again.
    act(() => screen.getByRole('button', { name: 'Pick the robin' }).click())
    expect(window.location.search).toBe('?species=59PMB&year_from=2000&year_to=2020')
    expect(seen.map.species.key).toBe('59PMB')
    expect(seen.map.years).toBe(firstYears)
    act(() => screen.getByRole('button', { name: 'Close the card' }).click())
    expect(window.location.search).toBe('?year_from=2000&year_to=2020')
    expect(seen.map.species).toBeNull()
    expect(seen.map.years).toBe(firstYears)

    // An open end, then all years again.
    act(() => seen.map.onYearsChange({ from: null, to: 1999 }))
    expect(window.location.search).toBe('?year_to=1999')
    expect(seen.map.years).toEqual({ from: null, to: 1999 })
    act(() => seen.map.onYearsChange(null))
    expect(window.location.search).toBe('')
    expect(seen.map.years).toBeNull()
  })

  it('opens a shared link with its year range', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })))
    render(<App search="?species=59PMB&year_from=2005" />)
    await act(async () => {})
    expect(seen.map.years).toEqual({ from: 2005, to: null })
    expect(seen.map.species.key).toBe('59PMB')
  })

  it('puts the open card in the address, in place of the current entry', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })))
    const navigate = vi.fn()
    render(<App search="" navigate={navigate} />)
    await act(async () => {})
    act(() => screen.getByRole('button', { name: 'Pick the robin' }).click())
    expect(navigate).toHaveBeenLastCalledWith('?species=59PMB', { replace: true })
    act(() => screen.getByRole('button', { name: 'Open its datasets' }).click())
    expect(navigate).toHaveBeenLastCalledWith('?view=datasets&species=59PMB')
    act(() => screen.getByRole('button', { name: 'Close the card' }).click())
    expect(navigate).toHaveBeenLastCalledWith('?', { replace: true })
  })

  it('opens the card the address names, with its name once the card arrives, and follows the back button', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })))
    const { rerender } = render(<App search="?species=49JFH" navigate={vi.fn()} />)
    await act(async () => {})
    expect(seen.map.species).toMatchObject({ key: '49JFH', name: null })
    act(() => screen.getByRole('button', { name: 'Load the card' }).click())
    expect(seen.map.species).toMatchObject({ key: '49JFH', name: 'Oncorhynchus nerka' })
    const first = seen.map.species.id
    rerender(<App search="?species=4LVR5" navigate={vi.fn()} />)
    expect(seen.map.species.key).toBe('4LVR5')
    expect(seen.map.species.id).not.toBe(first)
    rerender(<App search="" navigate={vi.fn()} />)
    expect(seen.map.species).toBeNull()
  })

  it('fetches the reserve layer once, hands it to the map, and keeps its switch in the address', async () => {
    const reserves = { available: true, source: { source_title: 'Test' }, outlines: { type: 'FeatureCollection', features: [] } }
    const fetchMock = vi.fn(async (url) => ({
      ok: true, status: 200, json: async () => (url.endsWith('/reserves') ? reserves : {}),
    }))
    vi.stubGlobal('fetch', fetchMock)
    window.history.replaceState(null, '', '/?year_from=2000')
    render(<Page />)
    await act(async () => {})
    expect(seen.map.reserves).toEqual(reserves)
    expect(seen.map.reservesShown).toBe(true)

    // Switching it off goes into the address in place, and every move keeps it.
    const before = window.history.length
    act(() => seen.map.onReservesShownChange(false))
    expect(window.location.search).toBe('?year_from=2000&reserves=off')
    expect(window.history.length).toBe(before)
    expect(seen.map.reservesShown).toBe(false)
    act(() => screen.getByRole('button', { name: 'Pick the robin' }).click())
    expect(window.location.search).toBe('?species=59PMB&year_from=2000&reserves=off')
    act(() => screen.getByRole('button', { name: 'Close the card' }).click())
    expect(window.location.search).toBe('?year_from=2000&reserves=off')
    act(() => seen.map.onCellClick('872800b30ffffff'))
    expect(window.location.search).toBe('?cell=872800b30ffffff&year_from=2000&reserves=off')
    act(() => seen.map.onReservesShownChange(true))
    expect(window.location.search).toBe('?cell=872800b30ffffff&year_from=2000')

    // Fetched only once, however the page moves.
    expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/reserves'))).toHaveLength(1)
    window.history.replaceState(null, '', '/')
  })
})
