import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useOverviewHexes, useSpeciesHexes } from './speciesHexes'

// A pretend API answer for one species map.
function hexes(specieskey, res, cells, occurrences) {
  return {
    specieskey, requested_key: specieskey, merged_from: null, res, year_from: null, year_to: null,
    years_counted: null, hexagon_count: cells.length,
    records_shown: occurrences.reduce((a, b) => a + b, 0), records_total: 0,
    records_no_year_left_out: 0, records_outside_years: 0, cells, occurrences,
  }
}

// A pretend API answer for one species map for a year range.
function hexesForYears(specieskey, res, yearsCounted, cells, occurrences, noYearLeftOut) {
  return {
    ...hexes(specieskey, res, cells, occurrences),
    year_from: yearsCounted[0], year_to: yearsCounted[1], years_counted: yearsCounted,
    records_no_year_left_out: noYearLeftOut,
  }
}

// A pretend API answer for the overview map for a year range.
function overviewAnswer(res, yearsCounted, cells, occurrences, species, noYearLeftOut = 0) {
  return {
    res, year_from: yearsCounted[0], year_to: yearsCounted[1], years_counted: yearsCounted,
    hexagon_count: cells.length, records_shown: occurrences.reduce((a, b) => a + b, 0),
    records_total: 100, records_no_year_left_out: noYearLeftOut, records_outside_years: 0,
    cells, occurrences, distinct_species: species,
  }
}

// A pretend fetch whose answers the test hands out one by one, in any
// order. It ignores cancelling, like a slow server whose answer still
// arrives, so the tests show the page itself ignores stale answers.
function controlledFetch() {
  const pending = []
  const fetchMock = vi.fn((url) => new Promise((resolve) => pending.push({ url, resolve })))
  vi.stubGlobal('fetch', fetchMock)
  // Sends the answer to the request whose URL contains part.
  async function answer(part, body, status = 200) {
    const request = pending.find((p) => p.url.includes(part))
    await act(async () => {
      request.resolve({ ok: status < 300, status, json: async () => body })
    })
  }
  return { fetchMock, answer, urls: () => fetchMock.mock.calls.map(([url]) => url) }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useSpeciesHexes', () => {
  it('asks for nothing without a species', () => {
    const { fetchMock } = controlledFetch()
    const { result } = renderHook(() => useSpeciesHexes(null, null, 4))
    expect(result.current).toEqual({ data: null, loading: false, error: null })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('asks for each resolution only when needed, and keeps the answers', async () => {
    const api = controlledFetch()
    const { result, rerender } = renderHook(({ res }) => useSpeciesHexes('59PMB', null, res), { initialProps: { res: 4 } })
    expect(api.urls()).toEqual(['/api/species/59PMB/hexes?res=4'])
    expect(result.current.loading).toBe(true)
    await api.answer('res=4', hexes('59PMB', 4, ['a4'], [7]))
    expect(result.current.data.rows).toEqual([{ h3Cell: 'a4', occurrences: 7 }])
    expect(result.current.data.recordsShown).toBe(7)

    // A new tier: asked for once; the coarser map stays up while it loads.
    rerender({ res: 5 })
    expect(api.urls()).toEqual(['/api/species/59PMB/hexes?res=4', '/api/species/59PMB/hexes?res=5'])
    expect(result.current.loading).toBe(true)
    expect(result.current.data.resolution).toBe(4)
    await api.answer('res=5', hexes('59PMB', 5, ['a5', 'b5'], [3, 4]))
    expect(result.current.data.resolution).toBe(5)

    // Back to a tier already here: no new request.
    rerender({ res: 4 })
    expect(api.urls()).toHaveLength(2)
    expect(result.current).toMatchObject({ loading: false, data: { resolution: 4 } })
  })

  it('never lets a slow answer for an earlier species replace the current one', async () => {
    const api = controlledFetch()
    const { result, rerender } = renderHook(({ key }) => useSpeciesHexes(key, null, 4), { initialProps: { key: 'AAAA' } })
    rerender({ key: 'BBBB' })
    await api.answer('BBBB', hexes('BBBB', 4, ['b'], [2]))
    // The answer for AAAA arrives late: the map stays on BBBB.
    await api.answer('AAAA', hexes('AAAA', 4, ['a'], [9]))
    expect(result.current.data.specieskey).toBe('BBBB')
    // But it was kept: going back to AAAA needs no new request.
    rerender({ key: 'AAAA' })
    expect(result.current.data.specieskey).toBe('AAAA')
    expect(api.urls()).toHaveLength(2)
  })

  it('works with an old merged key: the API answers with the kept key', async () => {
    const api = controlledFetch()
    const { result } = renderHook(() => useSpeciesHexes('VM8MB', null, 4))
    expect(api.urls()).toEqual(['/api/species/VM8MB/hexes?res=4'])
    await api.answer('VM8MB', { ...hexes('79DVG', 4, ['c'], [5]), requested_key: 'VM8MB', merged_from: 'VM8MB' })
    expect(result.current.data.specieskey).toBe('79DVG')
  })

  it('reports a failed request, and asks again when the tier is revisited', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const api = controlledFetch()
    const { result, rerender } = renderHook(({ res }) => useSpeciesHexes('59PMB', null, res), { initialProps: { res: 4 } })
    await api.answer('res=4', { detail: 'Internal Server Error' }, 500)
    expect(result.current.loading).toBe(false)
    expect(result.current.error.kind).toBe('failed')
    rerender({ res: 5 })
    rerender({ res: 4 })
    await waitFor(() => expect(api.urls().filter((url) => url.endsWith('res=4'))).toHaveLength(2))
  })
})

describe('a year range', () => {
  const Y2000_2020 = { from: 2000, to: 2020 }
  const Y2010_2010 = { from: 2010, to: 2010 }

  it('is sent to the API, only with the ends that are set', async () => {
    const api = controlledFetch()
    const { rerender } = renderHook(({ years }) => useSpeciesHexes('59PMB', years, 4), { initialProps: { years: Y2000_2020 } })
    rerender({ years: { from: 2000, to: null } })
    expect(api.urls()).toEqual([
      '/api/species/59PMB/hexes?res=4&year_from=2000&year_to=2020',
      '/api/species/59PMB/hexes?res=4&year_from=2000',
    ])
  })

  it("keeps each range's answer, and keeps the last map up while a new range loads", async () => {
    const api = controlledFetch()
    const { result, rerender } = renderHook(({ years }) => useSpeciesHexes('59PMB', years, 4), { initialProps: { years: null } })
    await api.answer('res=4', hexes('59PMB', 4, ['a'], [9]))
    rerender({ years: Y2000_2020 })
    // Still the all-years map, marked as loading.
    expect(result.current).toMatchObject({ loading: true, data: { yearsCounted: null, recordsShown: 9 } })
    await api.answer('year_to=2020', hexesForYears('59PMB', 4, [2000, 2020], ['a'], [4], 1))
    expect(result.current.data).toMatchObject({ yearsCounted: [2000, 2020], recordsShown: 4, recordsNoYearLeftOut: 1 })
    // Back to all years: already here, no new request.
    rerender({ years: null })
    expect(result.current.data.recordsShown).toBe(9)
    expect(api.urls()).toHaveLength(2)
  })

  it('never lets a slow answer for an earlier range replace the current one', async () => {
    const api = controlledFetch()
    const { result, rerender } = renderHook(({ years }) => useSpeciesHexes('59PMB', years, 4), { initialProps: { years: Y2000_2020 } })
    rerender({ years: Y2010_2010 })
    await api.answer('year_from=2010', hexesForYears('59PMB', 4, [2010, 2010], ['b'], [2], 0))
    await api.answer('year_from=2000', hexesForYears('59PMB', 4, [2000, 2020], ['a'], [8], 0))
    expect(result.current.data.yearsCounted).toEqual([2010, 2010])
    rerender({ years: Y2000_2020 })
    expect(result.current.data.yearsCounted).toEqual([2000, 2020])
    expect(api.urls()).toHaveLength(2)
  })
})

describe('useOverviewHexes', () => {
  it('asks for nothing with all years: the shipped files are used then', () => {
    const { fetchMock } = controlledFetch()
    const { result } = renderHook(() => useOverviewHexes(null, 4))
    expect(result.current).toEqual({ data: null, loading: false, error: null })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('asks the API for a range, with records and species per hexagon', async () => {
    const api = controlledFetch()
    const { result, rerender } = renderHook(({ res }) => useOverviewHexes({ from: 2000, to: 2020 }, res), { initialProps: { res: 4 } })
    expect(api.urls()).toEqual(['/api/overview/hexes?res=4&year_from=2000&year_to=2020'])
    await api.answer('res=4', overviewAnswer(4, [2000, 2020], ['c1', 'c2'], [10, 3], [4, 1], 37966))
    expect(result.current.data).toMatchObject({
      resolution: 4,
      rows: [{ h3Cell: 'c1', occurrences: 10, distinctSpecies: 4 }, { h3Cell: 'c2', occurrences: 3, distinctSpecies: 1 }],
      recordsShown: 13, hexagonCount: 2, yearsCounted: [2000, 2020], recordsNoYearLeftOut: 37966,
    })
    // A new tier: asked for once, the coarser map stays up meanwhile.
    rerender({ res: 5 })
    expect(api.urls()[1]).toBe('/api/overview/hexes?res=5&year_from=2000&year_to=2020')
    expect(result.current).toMatchObject({ loading: true, data: { resolution: 4 } })
  })
})

