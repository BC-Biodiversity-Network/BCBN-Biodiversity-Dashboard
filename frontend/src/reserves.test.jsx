import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AreaPanel from './AreaPanel'
import HexagonPanel, { ReserveReminder } from './HexagonPanel'
import IndigenousDataPage from './IndigenousDataPage'
import {
  REMINDER_MAX_NAMES, RESERVES_SHOWN_BY_DEFAULT, labelPointOfRing, reminderNamesText, reserveLabelPoints, reserveOutlines,
} from './reserves'
import {
  INDIGENOUS_PAGE_SEARCH, NO_LIST, mapSearch, readReservesShown, readView, withReserves, withYears,
} from './route'
import SiteFooter from './SiteFooter'

// The reserve reminder in the hexagon and area panels, the reserve layer's
// setting in the address, the Indigenous data page, and the footer's credit.
// Every reserve name here is made up.

const CELL = '872800b30ffffff'
const AREA = { box: { west: -120.6, south: 50.55, east: -120.1, north: 50.8 }, res: 6 }

// Made-up names using the letters the real ones do.
const SEVEN = ['ALPHA 1', 'K’IT TSAḎẔE', 'WǪCHIIGII NANÉɁ', 'Made-up Creek 2', 'GAT TAH KWĄ', 'SIXTH 6', 'SEVENTH 7']

// The words of the reminder before and after the names.
const BEFORE = 'This area includes First Nations reserve land ('
const AFTER = '). If these data were collected on a reserve, check where they came from and whether the Nation '
  + 'agreed to the work.'

// A pretend /api/hexes/{cell} answer (all species), with reserve_names.
function cellAnswer(names, fields = {}) {
  return {
    cell: CELL, res: 7, year_from: null, year_to: null, years_counted: null, records: 6, species: 1,
    records_no_year_left_out: 0, records_outside_years: 0, q: null, matching: 1, offset: 0, limit: 50,
    english_name_source: 'BC Species and Ecosystems Explorer (BCSEE)',
    results: [{ specieskey: '59PMB', col_name: 'Turdus migratorius', name_source: 'Catalogue of Life, via GBIF',
      english_name: 'American Robin', statuses: [], records: 6 }],
    species_asked_for: null, sensitive_max_res: null, status: [], sort: 'most_records',
    status_counts: { red: 0, blue: 0, yellow: 0, exotic: 0, none: 1 }, reserve_names: names,
    ...fields,
  }
}

// The species asked for on a species map's panel, with records here.
const ASKED = {
  specieskey: '59PMB', requested_key: '59PMB', merged_from: null, col_name: 'Turdus migratorius',
  english_name: 'American Robin', records: 6, records_per_year: [{ year: 2020, records: 6 }], first_year: 2020,
  last_year: 2020, records_no_year: 0, gbif_keys: ['59PMB'],
}

// A pretend /api/area answer, with reserve_names.
function areaAnswer(names) {
  return {
    ...cellAnswer(names), box: AREA.box, res: 6, box_area_km2: 979.4, minimum_area_km2: 144.5, hexagon_count: 45,
    rank: null,
  }
}

// A pretend fetch answering every request with body.
function stubAnswer(body) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => body })))
}

// The reminder on the page, or null.
function reminder() {
  return document.querySelector('.reserve-reminder')
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the names in the reminder', () => {
  it('lists up to 5 names exactly as given, then how many more', () => {
    expect(REMINDER_MAX_NAMES).toBe(5)
    expect(reminderNamesText(['ONE 1'])).toBe('ONE 1')
    expect(reminderNamesText(SEVEN.slice(0, 5))).toBe('ALPHA 1, K’IT TSAḎẔE, WǪCHIIGII NANÉɁ, Made-up Creek 2, GAT TAH KWĄ')
    expect(reminderNamesText(SEVEN.slice(0, 6))).toBe(
      'ALPHA 1, K’IT TSAḎẔE, WǪCHIIGII NANÉɁ, Made-up Creek 2, GAT TAH KWĄ, and 1 more')
    expect(reminderNamesText(SEVEN)).toBe(
      'ALPHA 1, K’IT TSAḎẔE, WǪCHIIGII NANÉɁ, Made-up Creek 2, GAT TAH KWĄ, and 2 more')
    const many = Array.from({ length: 1205 }, (_, index) => `R ${index}`)
    expect(reminderNamesText(many)).toBe('R 0, R 1, R 2, R 3, R 4, and 1,200 more')
  })
})

describe('the reserve reminder', () => {
  it('shows nothing without names', () => {
    for (const names of [[], null, undefined]) {
      render(<ReserveReminder names={names} onOpenPage={vi.fn()} />)
      expect(reminder()).toBeNull()
      cleanup()
    }
  })

  it('has its title, the names, the words, and a link to the Indigenous data page', () => {
    const onOpenPage = vi.fn()
    render(<ReserveReminder names={SEVEN} onOpenPage={onOpenPage} />)
    expect(screen.getByRole('heading', { name: 'First Nations reserve land' })).toBeTruthy()
    expect(reminder().querySelector('p').textContent).toBe(
      `${BEFORE}ALPHA 1, K’IT TSAḎẔE, WǪCHIIGII NANÉɁ, Made-up Creek 2, GAT TAH KWĄ, and 2 more${AFTER}`)
    const link = screen.getByRole('link', { name: 'Indigenous data' })
    expect(link.getAttribute('href')).toBe(INDIGENOUS_PAGE_SEARCH)
    fireEvent.click(link)
    expect(onOpenPage).toHaveBeenCalledTimes(1)
    // Not a pop-up or an alert.
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('the reminder in the panels', () => {
  it('shows under the all-species list when the hexagon touches a reserve, and opens the page', async () => {
    stubAnswer(cellAnswer(['ALPHA 1', 'Made-up Creek 2']))
    const onOpenIndigenousPage = vi.fn()
    const { container } = render(<HexagonPanel cell={CELL} years={null} species={null} list={NO_LIST}
      onClose={vi.fn()} onOpenSpecies={vi.fn()} onOpenIndigenousPage={onOpenIndigenousPage} />)
    await screen.findByText('Turdus migratorius')
    expect(reminder().textContent).toContain(`${BEFORE}ALPHA 1, Made-up Creek 2${AFTER}`)
    // Under the list.
    const list = container.querySelector('.hexagon-species')
    expect(list.compareDocumentPosition(reminder()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(screen.getByRole('link', { name: 'Indigenous data' }))
    expect(onOpenIndigenousPage).toHaveBeenCalledTimes(1)
  })

  it('does not show when the hexagon touches no reserve', async () => {
    stubAnswer(cellAnswer([]))
    render(<HexagonPanel cell={CELL} years={null} species={null} list={NO_LIST} onClose={vi.fn()}
      onOpenSpecies={vi.fn()} />)
    await screen.findByText('Turdus migratorius')
    expect(reminder()).toBeNull()
  })

  it('shows on a species map\'s panel, about one species', async () => {
    stubAnswer(cellAnswer(['ALPHA 1'], { species_asked_for: ASKED }))
    render(<HexagonPanel cell={CELL} years={null} species={{ key: '59PMB', name: 'Turdus migratorius', id: 1 }}
      onClose={vi.fn()} onOpenSpecies={vi.fn()} />)
    await screen.findByText(/records? in this/)
    expect(reminder().textContent).toContain(`${BEFORE}ALPHA 1${AFTER}`)
  })

  it('does not show on a species map\'s panel without a reserve', async () => {
    stubAnswer(cellAnswer([], { species_asked_for: ASKED }))
    render(<HexagonPanel cell={CELL} years={null} species={{ key: '59PMB', name: 'Turdus migratorius', id: 1 }}
      onClose={vi.fn()} onOpenSpecies={vi.fn()} />)
    await screen.findByText(/records? in this/)
    expect(reminder()).toBeNull()
  })

  it('shows under the area panel\'s list with every reserve any hexagon touches, shortened', async () => {
    stubAnswer(areaAnswer(SEVEN))
    const { container } = render(<AreaPanel area={AREA} cellCount={45} years={null} list={NO_LIST}
      onListChange={vi.fn()} onClose={vi.fn()} onOpenSpecies={vi.fn()} />)
    await screen.findByText('Turdus migratorius')
    expect(reminder().textContent).toContain('GAT TAH KWĄ, and 2 more)')
    const list = container.querySelector('.hexagon-species')
    expect(list.compareDocumentPosition(reminder()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('does not show in the area panel without a reserve', async () => {
    stubAnswer(areaAnswer([]))
    render(<AreaPanel area={AREA} cellCount={45} years={null} list={NO_LIST} onListChange={vi.fn()}
      onClose={vi.fn()} onOpenSpecies={vi.fn()} />)
    await screen.findByText('Turdus migratorius')
    expect(reminder()).toBeNull()
  })
})

describe('the reserve layer\'s setting in the address', () => {
  it('is on by default, from one constant, and reads reserves=off and reserves=on', () => {
    expect(RESERVES_SHOWN_BY_DEFAULT).toBe(true)
    expect(readReservesShown('')).toBe(true)
    expect(readReservesShown('?reserves=off')).toBe(false)
    expect(readReservesShown('?cell=872800b30ffffff&reserves=on')).toBe(true)
    expect(readReservesShown('?reserves=maybe')).toBe(true)
  })

  it('is only written when it is not the default, and kept by every map address', () => {
    expect(mapSearch({ reservesShown: true })).toBe('?')
    expect(mapSearch({ reservesShown: false })).toBe('?reserves=off')
    expect(mapSearch({ cell: CELL, years: { from: 2000, to: null }, reservesShown: false }))
      .toBe(`?cell=${CELL}&year_from=2000&reserves=off`)
  })

  it('is switched in place, keeping everything else, and survives a change of years', () => {
    const search = `?species=59PMB&cell=${CELL}&year_from=2000`
    const off = withReserves(search, false)
    expect(off).toBe(`?species=59PMB&cell=${CELL}&year_from=2000&reserves=off`)
    expect(withReserves(off, true)).toBe(search)
    expect(withYears(off, null)).toBe(`?species=59PMB&cell=${CELL}&reserves=off`)
    expect(withReserves('?area=-120.6,50.55,-120.1,50.8&area_res=6', false))
      .toBe('?area=-120.6,50.55,-120.1,50.8&area_res=6&reserves=off')
  })
})

describe('the Indigenous data page', () => {
  it('has its own address, its heading and one line', () => {
    expect(readView(INDIGENOUS_PAGE_SEARCH)).toBe('indigenous')
    expect(readView('?view=other')).toBe('species')
    const { container } = render(<IndigenousDataPage />)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Indigenous data on this site')
    expect([...container.querySelectorAll('p')].map((p) => p.textContent)).toEqual([
      'This page is being written with the BCBN steering committee. To be decided.'])
  })
})

describe('the footer\'s reserve credit', () => {
  it('names the source, says it is simplified and not for boundaries, and carries the licence statement', () => {
    render(<SiteFooter />)
    expect(screen.getByRole('contentinfo').textContent).toContain(
      'Reserve outlines: Aboriginal Lands of Canada Legislative Boundaries, Natural Resources Canada, simplified '
      + 'for display; not to be used for defining boundaries. Contains information licensed under the Open '
      + 'Government Licence – Canada.')
    expect(screen.getByRole('link', { name: 'Aboriginal Lands of Canada Legislative Boundaries' }).href).toBe(
      'https://open.canada.ca/data/en/dataset/522b07b9-78e2-4819-b736-ad9208eb1067')
    expect(screen.getByRole('link', { name: 'Open Government Licence – Canada' }).href).toBe(
      'https://open.canada.ca/en/open-government-licence-canada')
  })
})

describe('the outlines from the API answer', () => {
  it('are the answer\'s when available, else none', () => {
    const outlines = { type: 'FeatureCollection', features: [{ type: 'Feature' }] }
    expect(reserveOutlines({ available: true, outlines })).toBe(outlines)
    expect(reserveOutlines({ available: false, outlines: null }).features).toEqual([])
    expect(reserveOutlines(null).features).toEqual([])
  })
})

describe('the label points', () => {
  it('put one name per reserve, inside its largest part', () => {
    const small = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
    const big = [[10, 10], [14, 10], [14, 14], [10, 14], [10, 10]]
    const points = reserveLabelPoints({ type: 'FeatureCollection', features: [
      { type: 'Feature', properties: { alcode: '1', name: 'TWO PARTS 1' },
        geometry: { type: 'MultiPolygon', coordinates: [[small], [big]] } },
      { type: 'Feature', properties: { alcode: '2', name: 'ONE PART 2' },
        geometry: { type: 'Polygon', coordinates: [small] } },
    ] })
    expect(points.features.map((feature) => feature.properties)).toEqual([{ name: 'TWO PARTS 1' }, { name: 'ONE PART 2' }])
    expect(points.features[0].geometry.coordinates).toEqual([12, 12])
    expect(points.features[1].geometry.coordinates).toEqual([0.5, 0.5])
  })

  it('keep the name on a bent shape whose centre is outside it', () => {
    // A U shape: its centroid falls in the gap between the arms.
    const u = [[0, 0], [3, 0], [3, 3], [2, 3], [2, 1], [1, 1], [1, 3], [0, 3], [0, 0]]
    const [lng, lat] = labelPointOfRing(u)
    const insideLeftArm = lng > 0 && lng < 1
    const insideRightArm = lng > 2 && lng < 3
    const insideBase = lat < 1
    expect(insideLeftArm || insideRightArm || insideBase).toBe(true)
  })
})

