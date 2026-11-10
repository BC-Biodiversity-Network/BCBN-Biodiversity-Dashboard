import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { gbifCellSearchUrl } from './gbifLinks'
import HexagonPanel, { hexagonSizeText, totalsText } from './HexagonPanel'
import { NO_LIST } from './route'
import { chartSummary, fillYears } from './YearBars'

const CELL = '872800b30ffffff'

// A pretend answer from /api/hexes/{cell}, with fields changed as given.
function answer(fields = {}) {
  return {
    cell: CELL, res: 7, year_from: null, year_to: null, years_counted: null, records: 6, species: 2,
    records_no_year_left_out: 0, records_outside_years: 0, q: null, matching: 2, offset: 0, limit: 50,
    english_name_source: 'BC Species and Ecosystems Explorer (BCSEE)',
    results: [
      { specieskey: '59PMB', col_name: 'Turdus migratorius', name_source: 'Catalogue of Life, via GBIF',
        english_name: 'American Robin', statuses: ['Yellow'], records: 5 },
      { specieskey: 'CCCC', col_name: 'Bombus (Pyrobombus) vosnesenskii', name_source: 'Catalogue of Life, via GBIF',
        english_name: null, statuses: [], records: 1 },
    ],
    species_asked_for: null, sensitive_max_res: null,
    status: [], sort: 'most_records', status_counts: { red: 0, blue: 0, yellow: 1, exotic: 0, none: 1 },
    ...fields,
  }
}

// A pretend fetch that answers with answerFor(url) and records the URLs.
function stubCell(answerFor) {
  const fetchMock = vi.fn(async (url) => ({ ok: true, status: 200, json: async () => answerFor(url) }))
  vi.stubGlobal('fetch', fetchMock)
  return () => fetchMock.mock.calls.map(([url]) => url)
}

// The list options the page gave the panel last, as the address would hold
// them (see Controlled).
let lists = []

// The panel with its list options held as the page holds them in the
// address: each change is kept, and recorded in lists.
function Controlled({ list: first = NO_LIST, ...props }) {
  const [list, setList] = useState(first)
  return (
    <HexagonPanel {...props} list={list} onListChange={(next) => {
      lists.push(next)
      setList(next)
    }} />
  )
}

// The panel for CELL with these props.
function show(props = {}) {
  lists = []
  return render(<Controlled cell={CELL} years={null} species={null} onClose={vi.fn()} onOpenSpecies={vi.fn()}
    {...props} />)
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the hexagon panel', () => {
  it('gives the hexagon\'s size, its records and species, and its species with links, chips and records', async () => {
    stubCell(() => answer())
    const { container } = show()
    await screen.findByText('6 records of 2 species, all years')
    expect(container.querySelector('.hexagon-size').textContent).toMatch(/^Hexagon of about \d+\.\d km²$/)
    const rows = [...container.querySelectorAll('.hexagon-row')]
    expect(rows.map((row) => row.textContent)).toEqual([
      'Turdus migratorius American RobinYellow5', 'Bombus (Pyrobombus) vosnesenskii1'])
    expect(rows[0].querySelector('a').getAttribute('href')).toBe('?species=59PMB')
    expect(rows[0].querySelector('.status-chip').className).toContain('status-yellow')
    expect(container.querySelector('.hexagon-sources').textContent).toMatch(/Catalogue of Life.*BCSEE/)
    expect(container.querySelector('.hexagon-lead')).toBeNull()
  })

  it('opens a species from the list in place of following its link, and closes with the x', async () => {
    stubCell(() => answer())
    const onOpenSpecies = vi.fn()
    const onClose = vi.fn()
    show({ onOpenSpecies, onClose })
    fireEvent.click(await screen.findByText('Bombus (Pyrobombus) vosnesenskii'))
    expect(onOpenSpecies).toHaveBeenCalledWith('CCCC')
    fireEvent.click(screen.getByRole('button', { name: 'Close the list of species in this hexagon' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('asks for the years shown, and says how many records with no date are not counted', async () => {
    const urls = stubCell(() => answer({ years_counted: [2018, 2020], records: 3, species: 1,
      records_no_year_left_out: 2, matching: 1 }))
    show({ years: { from: 2018, to: 2020 } })
    await screen.findByText('3 records of 1 species, 2018 to 2020')
    expect(screen.getByText('2 records with no date are not counted.')).toBeTruthy()
    expect(urls()[0]).toBe(`/api/hexes/${CELL}?offset=0&limit=50&year_from=2018&year_to=2020`)
  })

  it('filters by name once typing pauses, and only with 2 letters or digits', async () => {
    const urls = stubCell((url) => (url.includes('q=robin')
      ? answer({ q: 'robin', matching: 1, results: answer().results.slice(0, 1) })
      : answer()))
    const { container } = show()
    await screen.findByText('6 records of 2 species, all years')
    const box = screen.getByRole('searchbox', { name: 'Filter the species in this hexagon by name' })
    fireEvent.change(box, { target: { value: 'r' } })
    expect(screen.getByText('Type at least 2 letters or digits to filter.')).toBeTruthy()
    fireEvent.change(box, { target: { value: 'robin' } })
    await waitFor(() => expect(container.querySelectorAll('.hexagon-row')).toHaveLength(1))
    expect(urls().filter((url) => url.includes('q='))).toEqual([`/api/hexes/${CELL}?offset=0&limit=50&q=robin`])
  })

  it('pages through a long list, 50 at a time', async () => {
    const many = Array.from({ length: 50 }, (_, i) => ({ specieskey: `K${i}`, col_name: `Species ${i}`,
      name_source: 'Catalogue of Life, via GBIF', english_name: null, statuses: [], records: 1 }))
    const urls = stubCell((url) => answer({ records: 120, species: 120, matching: 120, results: many,
      offset: Number(new URLSearchParams(url.split('?')[1]).get('offset')) }))
    show()
    await screen.findByText('1 to 50 of 120 species')
    expect(screen.getByRole('button', { name: 'Previous page' }).disabled).toBe(true)
    expect(screen.getAllByRole('button', { name: /^Page \d$/ }).map((button) => button.textContent))
      .toEqual(['1', '2', '3'])
    fireEvent.click(screen.getByRole('button', { name: 'Page 3' }))
    await screen.findByText('101 to 120 of 120 species')
    expect(urls()[1]).toBe(`/api/hexes/${CELL}?offset=100&limit=50`)
    expect(lists).toEqual([{ ...NO_LIST, page: 3 }])
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }))
    await screen.findByText('51 to 100 of 120 species')
  })

  it('sorts, and filters by BC status with how many species each status would show', async () => {
    const urls = stubCell(() => answer())
    const { container } = show()
    await screen.findByText('6 records of 2 species, all years')
    const boxes = [...container.querySelectorAll('.species-list-box')]
    expect(boxes.map((box) => box.textContent)).toEqual(['Red 0', 'Blue 0', 'Yellow 1', 'Exotic 0', 'No status 1'])
    expect(container.querySelector('.species-list-note').textContent)
      .toBe('A species counts if it, or any subspecies or population of it, has the status.')
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort' }), { target: { value: 'english' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /No status/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /Red/ }))
    await waitFor(() => expect(urls()).toContain(
      `/api/hexes/${CELL}?offset=0&limit=50&status=red&status=none&sort=english`))
    // The statuses stay in their usual order, whatever order they are ticked in.
    expect(lists.at(-1)).toEqual({ q: '', sort: 'english', status: ['red', 'none'], page: 1 })
    expect(screen.getByRole('checkbox', { name: /Red/ }).checked).toBe(true)
    fireEvent.click(screen.getByRole('checkbox', { name: /Red/ }))
    expect(lists.at(-1).status).toEqual(['none'])
  })

  it('asks for the list the address gives, and shows its filter in the box', async () => {
    const urls = stubCell(() => answer({ matching: 120, offset: 50 }))
    show({ list: { q: 'robin', sort: 'scientific', status: ['red', 'none'], page: 2 } })
    await screen.findByText('51 to 100 of 120 species that match')
    expect(urls()[0]).toBe(`/api/hexes/${CELL}?offset=50&limit=50&q=robin&status=red&status=none&sort=scientific`)
    expect(screen.getByRole('searchbox').value).toBe('robin')
    expect(screen.getByRole('combobox', { name: 'Sort' }).value).toBe('scientific')
  })

  it('says when no species passes the filters, and how many do', async () => {
    stubCell(() => answer({ matching: 0, results: [] }))
    show({ list: { ...NO_LIST, q: 'eagle', status: ['red'] } })
    expect(await screen.findByText('No species here match “eagle” with the statuses chosen.')).toBeTruthy()
    cleanup()
    stubCell(() => answer({ matching: 2 }))
    show({ list: { ...NO_LIST, status: ['yellow'] } })
    expect(await screen.findByText('2 species match.')).toBeTruthy()
  })

  it('goes back to the first page when the page asked for is past the end', async () => {
    stubCell(() => answer({ matching: 2 }))
    show({ list: { ...NO_LIST, page: 4 } })
    await waitFor(() => expect(lists).toEqual([NO_LIST]))
  })

  it('says when the API cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    }))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    show()
    await screen.findByText('This hexagon is not available: the API could not be reached.')
  })
})

describe('the panel\'s words', () => {
  it('says the size from the hexagon\'s own area, and the totals in the singular when there is one', () => {
    expect(hexagonSizeText('8428c9bffffffff')).toMatch(/^Hexagon of about \d,\d\d\d km²$/)
    expect(hexagonSizeText('8428c9bffffffff')).not.toMatch(/resolution/i)
    expect(totalsText(answer({ records: 1, species: 1 }))).toBe('1 record of 1 species, all years')
    expect(totalsText(answer({ records: 1234, species: 3, years_counted: [2020, 2020] }))).toBe(
      '1,234 records of 3 species, 2020')
  })
})

// The robin as the species asked for, with fields changed as given.
function robin(fields = {}) {
  return {
    specieskey: '59PMB', requested_key: '59PMB', merged_from: null, col_name: 'Turdus migratorius',
    name_source: 'Catalogue of Life, via GBIF', english_name: 'American Robin', statuses: ['Yellow'], records: 5,
    records_per_year: [{ year: 2019, records: 2 }, { year: 2021, records: 3 }], first_year: 2019, last_year: 2021,
    records_no_year: 1, gbif_keys: ['59PMB', 'OLDKEY'],
    ...fields,
  }
}

// The panel on the robin's map.
function showOnRobinMap(props = {}) {
  return show({ species: { key: '59PMB', name: 'Turdus migratorius', id: 1 }, onShowAllSpecies: vi.fn(), ...props })
}

describe('the hexagon panel on a species map', () => {
  it('is about that species: its records, first and last year, a chart by year and no list of species', async () => {
    const urls = stubCell(() => answer({ species_asked_for: robin(), results: answer().results.slice(0, 1) }))
    const { container } = showOnRobinMap()
    await screen.findByText(/First record 2019, last 2021\./)
    expect(container.querySelector('h2').textContent).toBe('Turdus migratorius in this hexagon')
    expect(container.querySelector('.hexagon-totals').textContent.replace(/\s+/g, ' ')).toBe(
      '5 records in this hexagon, all years.')
    expect(urls()[0]).toBe(`/api/hexes/${CELL}?offset=0&limit=1&species=59PMB`)
    expect(container.querySelector('.hexagon-row')).toBeNull()
    expect(container.querySelector('.hexagon-filter')).toBeNull()
    const svg = container.querySelector('.year-bars svg')
    expect(svg.getAttribute('aria-label')).toBe('Records per year in this hexagon, 2019 to 2021. Most in 2021: 3 records.')
    expect([...container.querySelectorAll('.year-bars-table tbody tr')].map((row) => row.textContent)).toEqual(
      ['20192', '20213'])
    expect(screen.getByText('1 of these records has no date, so it is not in the chart.')).toBeTruthy()
  })

  it('links to the same records on GBIF in a new tab, with every merged key, and says the counts may differ', async () => {
    stubCell(() => answer({ species_asked_for: robin() }))
    showOnRobinMap()
    const link = await screen.findByRole('link', { name: 'See these 5 records on GBIF' })
    expect(link.getAttribute('href')).toBe(gbifCellSearchUrl({ keys: ['59PMB', 'OLDKEY'], cell: CELL }))
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    expect(screen.getByText(/GBIF shows its current data, without the cleaning of our snapshot/)).toBeTruthy()
  })

  it('counts and links for the chosen years, and says records with no date are not counted', async () => {
    const urls = stubCell(() => answer({ years_counted: [2019, 2020], species_asked_for: robin({
      records: 2, records_per_year: [{ year: 2019, records: 2 }], first_year: 2019, last_year: 2019 }) }))
    const { container } = showOnRobinMap({ years: { from: 2019, to: 2020 } })
    await screen.findByText('All from 2019.')
    expect(container.querySelector('.hexagon-totals').textContent.replace(/\s+/g, ' ')).toBe(
      '2 records in this hexagon, 2019 to 2020.')
    expect(screen.getByText('1 record with no date is not counted.')).toBeTruthy()
    const link = screen.getByRole('link', { name: 'See these 2 records on GBIF' })
    expect(new URL(link.getAttribute('href')).searchParams.get('year')).toBe('2019,2020')
    expect(urls()[0]).toContain('year_from=2019&year_to=2020')
    // The chart runs across the whole range asked for.
    expect(container.querySelector('.year-bars svg').getAttribute('aria-label')).toMatch(/^Records per year in this hexagon, 2019 to 2020\./)
  })

  it('says plainly when the species has no records here, with no chart and no GBIF link', async () => {
    stubCell(() => answer({ species_asked_for: robin({ col_name: 'Salix scouleriana', records: 0, records_per_year: [],
      first_year: null, last_year: null, records_no_year: 0 }) }))
    const { container } = show({ species: { key: 'BBBB', name: null, id: 2 }, onShowAllSpecies: vi.fn() })
    await screen.findByText('Show all 2 species in this hexagon')
    expect(container.querySelector('.hexagon-totals').textContent).toBe('No records of Salix scouleriana in this hexagon.')
    expect(container.querySelector('.year-bars')).toBeNull()
    expect(screen.queryByRole('link', { name: /on GBIF/ })).toBeNull()
  })

  it('shows nothing about the species\' records when the sensitive rule hides it here', async () => {
    stubCell(() => answer({ species_asked_for: robin({ records: null, records_per_year: null, first_year: null,
      last_year: null, records_no_year: null, gbif_keys: null }) }))
    const { container } = showOnRobinMap()
    await screen.findByText('Records of this species are not shown for this hexagon.')
    expect(container.querySelector('.year-bars')).toBeNull()
    expect(container.querySelector('.hexagon-years')).toBeNull()
    expect(screen.queryByRole('link', { name: /on GBIF/ })).toBeNull()
  })

  it('switches to every species in the hexagon with "Show all", in place of following the link', async () => {
    stubCell(() => answer({ species_asked_for: robin() }))
    const onShowAllSpecies = vi.fn()
    showOnRobinMap({ onShowAllSpecies })
    const link = await screen.findByRole('link', { name: 'Show all 2 species in this hexagon' })
    expect(link.getAttribute('href')).toBe(`?cell=${CELL}`)
    fireEvent.click(link)
    expect(onShowAllSpecies).toHaveBeenCalledTimes(1)
  })
})

describe('the hexagon panel on a rank map', () => {
  const TURDUS = { rank: 'genus', name: 'Turdus', path: [
    { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Chordata' }, { rank: 'class', name: 'Aves' },
    { rank: 'order', name: 'Passeriformes' }, { rank: 'family', name: 'Turdidae' }, { rank: 'genus', name: 'Turdus' },
  ] }

  it('lists only the rank\'s species, asked for with the rank\'s path, under a title naming the rank', async () => {
    const urls = stubCell(() => answer({ records: 5, species: 1, matching: 1, results: answer().results.slice(0, 1) }))
    const onOpenSpecies = vi.fn()
    const { container } = show({ rank: TURDUS, onOpenSpecies })
    await screen.findByText('5 records of 1 species, all years')
    expect(urls()[0]).toBe(`/api/hexes/${CELL}?offset=0&limit=50&rank=genus&kingdom=Animalia&phylum=Chordata`
      + '&class=Aves&order=Passeriformes&family=Turdidae&genus=Turdus')
    const title = container.querySelector('.hexagon-title')
    expect(title.textContent).toBe('Genus Turdus in this hexagon')
    expect(title.querySelector('i').textContent).toBe('Turdus')
    fireEvent.click(screen.getByText('Turdus migratorius'))
    expect(onOpenSpecies).toHaveBeenCalledWith('59PMB')
  })

  it('links to every species in the hexagon, in place of following the link', async () => {
    stubCell(() => answer())
    const onShowAllSpecies = vi.fn()
    show({ rank: TURDUS, onShowAllSpecies })
    const link = await screen.findByText('Show all species in this hexagon')
    expect(link.getAttribute('href')).toBe(`?cell=${CELL}`)
    fireEvent.click(link)
    expect(onShowAllSpecies).toHaveBeenCalled()
  })

  it('says plainly when none of the rank\'s species is in the hexagon', async () => {
    stubCell(() => answer({ records: 0, species: 0, matching: 0, results: [], years_counted: [2018, 2020] }))
    show({ rank: TURDUS, years: { from: 2018, to: 2020 } })
    expect(await screen.findByText('No species of this genus in this hexagon for these years.')).toBeTruthy()
    expect(screen.queryByRole('searchbox')).toBeNull()
  })

  it('has no link to every species on the overview', async () => {
    stubCell(() => answer())
    show()
    await screen.findByText('6 records of 2 species, all years')
    expect(screen.queryByText('Show all species in this hexagon')).toBeNull()
  })
})

describe('the chart of records per year', () => {
  it('fills the years with none between the first and last, or across a range asked for', () => {
    const perYear = [{ year: 2019, records: 2 }, { year: 2021, records: 3 }]
    expect(fillYears(perYear)).toEqual([{ year: 2019, records: 2 }, { year: 2020, records: 0 }, { year: 2021, records: 3 }])
    expect(fillYears(perYear, [2018, 2021]).map((item) => item.year)).toEqual([2018, 2019, 2020, 2021])
    expect(fillYears([])).toEqual([])
    expect(chartSummary(fillYears([{ year: 2020, records: 1 }]))).toBe(
      'Records per year in this hexagon, 2020. Most in 2020: 1 record.')
  })

  it('shows a year\'s records when its column is hovered', async () => {
    stubCell(() => answer({ species_asked_for: robin() }))
    const { container } = showOnRobinMap()
    await waitFor(() => expect(container.querySelector('.year-bars')).not.toBeNull())
    const columns = [...container.querySelectorAll('.year-bars rect[fill="transparent"]')]
    expect(columns).toHaveLength(3)
    fireEvent.mouseEnter(columns[1])
    expect(container.querySelector('.year-bars-readout').textContent).toBe('2020: 0 records')
    fireEvent.mouseEnter(columns[2])
    expect(container.querySelector('.year-bars-readout').textContent).toBe('2021: 3 records')
  })
})

describe("the hexagon panel's Download CSV button", () => {
  // The panel's download link.
  function downloadLink() {
    return screen.queryByRole('link', { name: 'Download the species in this hexagon as a CSV file' })
  }

  // The genus Turdus, as App gives it for a rank map.
  const TURDUS = { rank: 'genus', name: 'Turdus', path: [
    { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Chordata' }, { rank: 'class', name: 'Aves' },
    { rank: 'order', name: 'Passeriformes' }, { rank: 'family', name: 'Turdidae' }, { rank: 'genus', name: 'Turdus' },
  ] }

  it('downloads the whole list on the overview, with its years and cell_ options, never one page of it', async () => {
    stubCell(() => answer({ offset: 50, matching: 60 }))
    show({ years: { from: 2000, to: null }, list: { q: 'robin', sort: 'english', status: ['yellow'], page: 2 } })
    await screen.findByText(/records of 2 species/)
    expect(downloadLink().getAttribute('href'))
      .toBe(`/api/export/hexes/${CELL}?year_from=2000&q=robin&status=yellow&sort=english`)
    expect(screen.queryByText(/lines of notes|read\.csv/)).toBe(null)
  })

  it('downloads only the rank\'s species on a rank map', async () => {
    stubCell(() => answer({ rank: { rank: 'genus', name: 'Turdus', path: TURDUS.path } }))
    show({ rank: TURDUS })
    await screen.findByText(/records of 2 species/)
    expect(downloadLink().getAttribute('href')).toBe(`/api/export/hexes/${CELL}?rank=genus&kingdom=Animalia`
      + '&phylum=Chordata&class=Aves&order=Passeriformes&family=Turdidae&genus=Turdus')
  })

  it('has no button on a species map, whose panel is about one species', async () => {
    stubCell(() => answer({ species_asked_for: {
      specieskey: '59PMB', requested_key: '59PMB', merged_from: null, col_name: 'Turdus migratorius',
      name_source: 'Catalogue of Life, via GBIF', english_name: 'American Robin', statuses: ['Yellow'],
      records: 5, records_per_year: [{ year: 2020, records: 3 }], first_year: 2020, last_year: 2020,
      records_no_year: 2, gbif_keys: ['59PMB'],
    } }))
    show({ species: { key: '59PMB', name: 'Turdus migratorius' } })
    await screen.findByText(/See these 5 records on GBIF/)
    expect(screen.queryByText('Download CSV')).toBe(null)
  })

  it('is greyed out while a new list loads and when nothing matches, and gone after an error', async () => {
    // The first answer arrives; the next one never does.
    let calls = 0
    vi.stubGlobal('fetch', vi.fn(() => {
      calls += 1
      return calls === 1 ? Promise.resolve({ ok: true, status: 200, json: async () => answer() }) : new Promise(() => {})
    }))
    show()
    await screen.findByText(/records of 2 species/)
    expect(downloadLink().getAttribute('aria-disabled')).toBe(null)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'scientific' } })
    await waitFor(() => expect(downloadLink().getAttribute('aria-disabled')).toBe('true'))
    cleanup()

    stubCell(() => answer({ q: 'zz', matching: 0, results: [] }))
    show({ list: { ...NO_LIST, q: 'zz' } })
    await screen.findByText(/records of 2 species/)
    expect(downloadLink().getAttribute('aria-disabled')).toBe('true')
    cleanup()

    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500, json: async () => ({ detail: 'broken' }) })))
    show()
    await screen.findByText(/could not be loaded/)
    expect(downloadLink()).toBe(null)
  })
})
