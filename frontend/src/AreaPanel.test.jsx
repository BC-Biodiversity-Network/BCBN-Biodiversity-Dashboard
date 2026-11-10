import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AreaPanel, { WHOLE_HEXAGONS_NOTE, areaSizeText } from './AreaPanel'
import { AREA_GBIF_NOTE, GBIF_COUNT_NOTE, gbifBoxSearchUrl } from './gbifLinks'
import { NO_LIST } from './route'

// A box around Kamloops at resolution 6 (45 hexagons), and the genus Turdus.
const AREA = { box: { west: -120.6, south: 50.55, east: -120.1, north: 50.8 }, res: 6 }
const TURDUS = { rank: 'genus', name: 'Turdus', path: [
  { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Chordata' }, { rank: 'class', name: 'Aves' },
  { rank: 'order', name: 'Passeriformes' }, { rank: 'family', name: 'Turdidae' }, { rank: 'genus', name: 'Turdus' },
] }

// A pretend answer from /api/area, with fields changed as given.
function answer(fields = {}) {
  return {
    box: AREA.box, res: 6, box_area_km2: 979.4, minimum_area_km2: 144.5, hexagon_count: 45,
    year_from: null, year_to: null, years_counted: null, records: 312234, species: 2,
    records_no_year_left_out: 0, records_outside_years: 0, q: null, status: [], sort: 'most_records',
    matching: 2, status_counts: { red: 0, blue: 0, yellow: 1, exotic: 0, none: 1 }, offset: 0, limit: 50,
    english_name_source: 'BC Species and Ecosystems Explorer (BCSEE)',
    results: [
      { specieskey: '59PMB', col_name: 'Turdus migratorius', name_source: 'Catalogue of Life, via GBIF',
        english_name: 'American Robin', statuses: ['Yellow'], records: 5000, gbif_link: 'https://www.gbif.org/x' },
      { specieskey: 'CCCC', col_name: 'Bombus (Pyrobombus) vosnesenskii', name_source: 'Catalogue of Life, via GBIF',
        english_name: null, statuses: [], records: 10, gbif_link: 'https://www.gbif.org/y' },
    ],
    rank: null, sensitive_max_res: null,
    ...fields,
  }
}

// A pretend fetch answering with answerFor(url) (status 200, or the status
// it gives), recording the addresses asked for.
function stubArea(answerFor) {
  const fetchMock = vi.fn(async (url) => {
    const { status = 200, body } = answerFor(url)
    return { ok: status < 300, status, json: async () => body }
  })
  vi.stubGlobal('fetch', fetchMock)
  return () => fetchMock.mock.calls.map(([url]) => url)
}

// The panel with its list options held as the page holds them in the address.
function Controlled({ list: first = NO_LIST, ...props }) {
  const [list, setList] = useState(first)
  return <AreaPanel {...props} list={list} onListChange={setList} />
}

// The panel for AREA with these props.
function show(props = {}) {
  const onClose = vi.fn()
  render(<Controlled area={AREA} cellCount={45} years={null} onClose={onClose} onOpenSpecies={vi.fn()} {...props} />)
  return onClose
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the area panel', () => {
  it('names the area, its size and its hexagons, and says whole hexagons count', async () => {
    stubArea(() => ({ body: answer() }))
    show()
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Species in this area')
    expect(screen.getByText(areaSizeText(AREA, 45))).toBeTruthy()
    expect(areaSizeText(AREA, 45)).toBe('A box of about 979 km² touching 45 hexagons of about 36 km² each.')
    expect(screen.getByText(WHOLE_HEXAGONS_NOTE)).toBeTruthy()
    await screen.findByText('312,234 records of 2 species, all years')
  })

  it('lists the species with the hexagon panel\'s controls, and asks for the box and the page', async () => {
    const urls = stubArea(() => ({ body: answer() }))
    show({ years: { from: 2000, to: null }, list: { q: 'robin', sort: 'english', status: ['yellow'], page: 1 } })
    await screen.findByText('Turdus migratorius')
    expect(screen.getByRole('searchbox')).toBeTruthy()
    expect(screen.getByRole('combobox')).toBeTruthy()
    expect(urls()[0]).toBe('/api/area?offset=0&limit=50&west=-120.6&south=50.55&east=-120.1&north=50.8&res=6'
      + '&year_from=2000&q=robin&status=yellow&sort=english')
  })

  it('downloads the whole list, never one page of it', async () => {
    stubArea(() => ({ body: answer({ offset: 50, matching: 60 }) }))
    show({ list: { q: 'robin', sort: 'english', status: ['yellow'], page: 2 } })
    const link = await screen.findByRole('link', { name: 'Download the species in this area as a CSV file' })
    expect(link.getAttribute('href')).toBe('/api/export/area?west=-120.6&south=50.55&east=-120.1&north=50.8&res=6'
      + '&q=robin&status=yellow&sort=english')
  })

  it('links to the box\'s records on GBIF for the years, with the two notes', async () => {
    stubArea(() => ({ body: answer({ years_counted: [2000, 2026] }) }))
    show({ years: { from: 2000, to: null } })
    const link = await screen.findByRole('link', { name: 'See these records on GBIF' })
    expect(link.getAttribute('href')).toBe(gbifBoxSearchUrl({ box: AREA.box, years: [2000, 2026] }))
    expect(link.getAttribute('target')).toBe('_blank')
    expect(screen.getByText(AREA_GBIF_NOTE)).toBeTruthy()
    expect(screen.getByText(GBIF_COUNT_NOTE)).toBeTruthy()
    expect(screen.queryByText(/GBIF shows every species in the box/)).toBeNull()
  })

  it('on a rank map, lists only the rank\'s species and says GBIF shows them all', async () => {
    const urls = stubArea(() => ({ body: answer({ rank: { rank: 'genus', name: 'Turdus', path: TURDUS.path } }) }))
    show({ rank: TURDUS })
    await screen.findByText('Turdus migratorius')
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Genus Turdus in this area')
    expect(urls()[0]).toContain('&rank=genus&kingdom=Animalia&phylum=Chordata&class=Aves&order=Passeriformes'
      + '&family=Turdidae&genus=Turdus')
    expect(screen.getByText('GBIF shows every species in the box, not only this genus.')).toBeTruthy()
  })

  it('says how many records with no date a year range left out', async () => {
    stubArea(() => ({ body: answer({ years_counted: [2000, 2020], records_no_year_left_out: 21 }) }))
    show({ years: { from: 2000, to: 2020 } })
    await screen.findByText('21 records with no date are not counted.')
  })

  it('shows the API\'s own message for a box it refused', async () => {
    stubArea(() => ({ status: 400, body: { detail: 'The box is about 9.9 km², smaller than the minimum of 144.5 km².' } }))
    show()
    await screen.findByText('The box is about 9.9 km², smaller than the minimum of 144.5 km².')
    expect(screen.queryByRole('link', { name: /CSV/ })).toBeNull()
  })

  it('warns in the console when the API counts other hexagons than the map', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    stubArea(() => ({ body: answer({ hexagon_count: 44 }) }))
    show()
    await screen.findByText('Turdus migratorius')
    await waitFor(() => expect(warn).toHaveBeenCalled())
    expect(warn.mock.calls[0][0]).toContain('the API counted 44 hexagons')
    expect(warn.mock.calls[0][0]).toContain('the map outlines 45')
  })

  it('does not warn when the counts agree', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    stubArea(() => ({ body: answer() }))
    show()
    await screen.findByText('Turdus migratorius')
    expect(warn).not.toHaveBeenCalled()
  })

  it('closes with its x', async () => {
    stubArea(() => ({ body: answer() }))
    const onClose = show()
    fireEvent.click(screen.getByRole('button', { name: 'Close the list of species in this area' }))
    expect(onClose).toHaveBeenCalled()
  })
})
