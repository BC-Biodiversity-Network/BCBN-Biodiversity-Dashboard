import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SearchBox, { ranksFirst } from './SearchBox'

const COL = 'Catalogue of Life, via GBIF'
const BCSEE = 'BC Species and Ecosystems Explorer (BCSEE)'

// One search result in the shape the API sends, with made-up values; a test
// changes only the fields it is about.
function makeResult(overrides = {}) {
  const result = {
    specieskey: 'AAAA',
    merged_from: null,
    col_name: 'Turdus migratorius',
    col_authorship: 'Linnaeus, 1766',
    name_source: COL,
    english_name: 'American Robin',
    english_name_source: BCSEE,
    record_count: 964801,
    kingdom: 'Animalia',
    class: 'Aves',
    family: 'Turdidae',
    match: { field: 'english_name', text: 'American Robin', kind: 'word_start',
             bcsee_entry_scientific_label: null },
    ...overrides,
  }
  result.display_names = overrides.display_names ?? [
    { text: result.col_name, field: 'col_name', source: COL },
    ...(result.english_name
      ? [{ text: result.english_name, field: 'english_name', source: BCSEE }]
      : []),
  ]
  return result
}

const ROBIN = makeResult()
const LOCUST = makeResult({
  specieskey: 'BBBB', col_name: 'Robinia pseudoacacia', col_authorship: 'L.',
  english_name: 'black locust', record_count: 635, family: 'Fabaceae',
  match: { field: 'col_name', text: 'Robinia pseudoacacia', kind: 'word_start',
           bcsee_entry_scientific_label: null },
})
const MOTH = makeResult({
  specieskey: 'CCCC', col_name: 'Paranthrene robiniae', col_authorship: '(Edwards, 1880)',
  english_name: 'Western Poplar Clearwing Moth', record_count: 42, family: 'Sesiidae',
})

// A pretend answer from fetch, with the parts api.js reads.
function answer(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

// Two ranks in the shape the API sends: a genus a common word finds, and a
// family.
const ROBINIA = {
  rank: 'genus', name: 'Robinia', label: 'Genus Robinia (Fabaceae)', species_count: 2, record_count: 640,
  path: [{ rank: 'kingdom', name: 'Plantae' }, { rank: 'phylum', name: 'Tracheophyta' },
    { rank: 'class', name: 'Magnoliopsida' }, { rank: 'order', name: 'Fabales' }, { rank: 'family', name: 'Fabaceae' },
    { rank: 'genus', name: 'Robinia' }],
  match: { kind: 'word_start' },
}
const TURDIDAE = {
  rank: 'family', name: 'Turdidae', label: 'Family Turdidae (Passeriformes)', species_count: 1, record_count: 1,
  path: [{ rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Chordata' }, { rank: 'class', name: 'Aves' },
    { rank: 'order', name: 'Passeriformes' }, { rank: 'family', name: 'Turdidae' }],
  match: { kind: 'exact' },
}

// Replaces fetch with one that answers every search with these results
// (and these ranks, if any).
function stubSearch(results, ranks = []) {
  const fetchMock = vi.fn(async (url) => answer(200, {
    query: new URLSearchParams(url.split('?')[1]).get('q'), results, ranks,
  }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

// The query (q) of each request the pretend fetch was sent.
function queriesSent(fetchMock) {
  return fetchMock.mock.calls.map(([url]) => new URLSearchParams(url.split('?')[1]).get('q'))
}

// Moves the pretend clock on and lets every answer that is due arrive and
// show on the page.
async function wait(ms) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

// Puts the search box on the page and returns its text field and the
// functions it calls when a species or a rank is picked.
function renderBox() {
  const onSelect = vi.fn()
  const onSelectRank = vi.fn()
  render(<SearchBox onSelect={onSelect} onSelectRank={onSelectRank} />)
  return { input: screen.getByRole('combobox'), onSelect, onSelectRank }
}

// The group headings in the open list, in order.
function groupHeadings() {
  return [...document.querySelectorAll('.search-group')].map((heading) => heading.textContent)
}

// Types text into the search field (replacing what was there).
function type(input, text) {
  fireEvent.change(input, { target: { value: text } })
}

// The text of each result in the open list, in order.
function listedTexts() {
  return screen.queryAllByRole('option').map((option) => option.textContent)
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('when the search box asks the API', () => {
  it('sends nothing below 2 letters or digits, and says so', async () => {
    const fetchMock = stubSearch([ROBIN])
    const { input } = renderBox()
    for (const text of ['r', ' - ', 'x', 'é']) {
      type(input, text)
      await wait(1000)
      expect(screen.getByText('Type at least 2 letters or digits to search.')).toBeTruthy()
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('waits 250 ms after the last key press, then sends one request', async () => {
    const fetchMock = stubSearch([ROBIN])
    const { input } = renderBox()
    for (const text of ['ro', 'rob', 'robi', 'robin']) {
      type(input, text)
      await wait(100)
    }
    // 100 ms after the last key press: nothing sent yet.
    expect(fetchMock).not.toHaveBeenCalled()
    await wait(149)
    expect(fetchMock).not.toHaveBeenCalled()
    await wait(1)
    expect(queriesSent(fetchMock)).toEqual(['robin'])
  })

  it('never lets a slow answer to an older search replace a newer one', async () => {
    // The answer to "rob" is held back until the test lets it go; it does
    // not stop when cancelled, to show the page ignores it anyway.
    let releaseOld
    const fetchMock = vi.fn((url) => {
      const query = new URLSearchParams(url.split('?')[1]).get('q')
      if (query === 'rob') {
        return new Promise((resolve) => {
          releaseOld = () => resolve(answer(200, { query, results: [MOTH] }))
        })
      }
      return Promise.resolve(answer(200, { query, results: [ROBIN] }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const { input } = renderBox()

    type(input, 'rob')
    await wait(250)
    type(input, 'robin')
    await wait(250)
    expect(queriesSent(fetchMock)).toEqual(['rob', 'robin'])
    expect(listedTexts()[0]).toContain('Turdus migratorius')

    releaseOld()
    await wait(0)
    expect(listedTexts()).toHaveLength(1)
    expect(listedTexts()[0]).toContain('Turdus migratorius')
    expect(listedTexts()[0]).not.toContain('Paranthrene')
  })
})

describe('keyboard use', () => {
  // Searches "robin" and waits for the three results.
  async function searchRobin() {
    stubSearch([ROBIN, LOCUST, MOTH])
    const box = renderBox()
    type(box.input, 'robin')
    await wait(250)
    return box
  }

  // Which result is highlighted, by its position in the list.
  function highlighted() {
    return screen.getAllByRole('option').findIndex(
      (option) => option.getAttribute('aria-selected') === 'true')
  }

  it('highlights the first result, and the arrow keys move through them', async () => {
    const { input } = await searchRobin()
    expect(highlighted()).toBe(0)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(highlighted()).toBe(2)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(highlighted()).toBe(2)
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(highlighted()).toBe(1)
  })

  it('Enter picks the highlighted species and closes the list', async () => {
    const { input, onSelect } = await searchRobin()
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSelect).toHaveBeenCalledWith(LOCUST)
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('Escape closes the list and keeps the typed text', async () => {
    const { input } = await searchRobin()
    // fireEvent returns false when the page stopped the browser's own
    // reaction, which for a search field would be to empty it.
    expect(fireEvent.keyDown(input, { key: 'Escape' })).toBe(false)
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(input.value).toBe('robin')
    // The down arrow opens the list again.
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(screen.getByRole('listbox')).toBeTruthy()
  })
})

describe('what each result shows', () => {
  it('shows the scientific name first, the common name labelled as from BCSEE, and the family and records', async () => {
    stubSearch([ROBIN])
    const { input } = renderBox()
    type(input, 'robin')
    await wait(250)
    const text = listedTexts()[0]
    expect(text.indexOf('Turdus migratorius')).toBe(0)
    expect(text).toContain('Linnaeus, 1766')
    expect(text).toContain('American RobinCommon name from BCSEE')
    expect(text).toContain('Turdidae · 964,801 records')
  })

  it('labels a BCSEE entry name as such, never as the species common name', async () => {
    const fiddleneck = makeResult({
      specieskey: 'HHHH', col_name: 'Amsinckia menziesii', col_authorship: '(Lehm.) A.Nelson & J.F.Macbr.',
      english_name: null, english_name_source: null, family: 'Boraginaceae', record_count: 144,
      match: { field: 'bcsee_entry_english_name', text: 'small-flowered fiddleneck', kind: 'exact',
               bcsee_entry_scientific_label: 'Amsinckia menziesii' },
      display_names: [
        { text: 'Amsinckia menziesii', field: 'col_name', source: COL },
        { text: 'small-flowered fiddleneck', field: 'bcsee_entry_english_name', source: BCSEE,
          bcsee_entry_scientific_label: 'Amsinckia menziesii' },
      ],
    })
    stubSearch([fiddleneck])
    const { input } = renderBox()
    type(input, 'small-flowered fiddleneck')
    await wait(250)
    const text = listedTexts()[0]
    expect(text).toContain('small-flowered fiddleneckCommon name of a BCSEE entry')
    expect(text).not.toContain('Common name from BCSEE')
  })

  it("labels a merged key's old name as an earlier name, and keeps BCSEE as the common-name source", async () => {
    const hawk = makeResult({
      specieskey: 'CVW5J', col_name: 'Astur cooperii', col_authorship: '(Bonaparte, 1828)',
      english_name: "Cooper's Hawk", family: 'Accipitridae', record_count: 104703,
      match: { field: 'merged_name', text: 'Accipiter cooperii', kind: 'exact',
               bcsee_entry_scientific_label: null },
      display_names: [
        { text: 'Astur cooperii', field: 'col_name', source: COL },
        { text: "Cooper's Hawk", field: 'english_name', source: BCSEE },
        { text: 'Accipiter cooperii', field: 'merged_name', source: COL },
      ],
    })
    stubSearch([hawk])
    const { input } = renderBox()
    type(input, 'Accipiter cooperii')
    await wait(250)
    expect(listedTexts()).toHaveLength(1)
    expect(listedTexts()[0]).toContain('Accipiter cooperiiEarlier name (Catalogue of Life synonym)')
    expect(document.querySelector('.search-sources').textContent)
      .toContain(`Common names and BCSEE names from ${BCSEE}.`)
  })

  it('tells apart two species with the same scientific name by authorship and records', async () => {
    const first = makeResult({ specieskey: 'L1', col_name: 'Lomatium dissectum',
      col_authorship: '(Nutt. ex Torr. & A.Gray) Mathias & Constance', record_count: 356,
      english_name: 'fern-leaved desert-parsley', family: 'Apiaceae' })
    const second = makeResult({ specieskey: 'L2', col_name: 'Lomatium dissectum',
      col_authorship: '(Nutt.) Mathias & Constance', record_count: 2,
      english_name: null, family: 'Apiaceae' })
    stubSearch([first, second])
    const { input } = renderBox()
    type(input, 'Lomatium dissectum')
    await wait(250)
    const [a, b] = listedTexts()
    expect(a).toContain('(Nutt. ex Torr. & A.Gray) Mathias & Constance')
    expect(a).toContain('356 records')
    expect(b).toContain('(Nutt.) Mathias & Constance')
    expect(b).toContain('2 records')
  })

  it('shows no conservation status, even if the answer carried one', async () => {
    const withStatus = makeResult({ statuses: ['Red'], bcsee: { state: 'listed', statuses: ['Red'] } })
    stubSearch([withStatus])
    const { input } = renderBox()
    type(input, 'robin')
    await wait(250)
    const panel = screen.getByRole('listbox').parentElement.textContent
    expect(panel).not.toMatch(/\bRed\b|Blue|Yellow|Exotic|status/i)
  })

  it('says "common name" everywhere, never "English name"', async () => {
    stubSearch([ROBIN])
    const { input } = renderBox()
    expect(input.getAttribute('placeholder')).toBe('Search species (scientific or common name)')
    type(input, 'robin')
    await wait(250)
    const panel = screen.getByRole('listbox').parentElement.textContent
    expect(panel).not.toMatch(/English/)
    expect(panel).toContain('Scientific names from Catalogue of Life, via GBIF.')
  })
})

describe('ranks in the list', () => {
  it('asks for 5 ranks, and lists them after the species under their own heading', async () => {
    const fetchMock = stubSearch([ROBIN, LOCUST], [ROBINIA, TURDIDAE])
    const { input } = renderBox()
    type(input, 'robin')
    await wait(250)
    expect(new URLSearchParams(fetchMock.mock.calls[0][0].split('?')[1]).get('rank_limit')).toBe('5')
    expect(groupHeadings()).toEqual(['Species', 'Genera, families and higher ranks'])
    const texts = listedTexts()
    expect(texts).toHaveLength(4)
    expect(texts[0]).toContain('Turdus migratorius')
    expect(texts[1]).toContain('Robinia pseudoacacia')
    expect(texts[2]).toBe('Genus Robinia (Fabaceae)2 species · 640 records')
    expect(texts[3]).toBe('Family Turdidae (Passeriformes)1 species · 1 record')
    // The headings are not options, and the genus is in italics.
    const options = screen.getAllByRole('option')
    expect(options[2].previousElementSibling.textContent).toBe('Genera, families and higher ranks')
    expect(options[2].querySelector('i').textContent).toBe('Robinia')
    expect(options[3].querySelector('i')).toBeNull()
    expect(document.querySelector('.search-sources').textContent).toContain('Ranks from the GBIF classification.')
  })

  it('shows no headings when only species are found', async () => {
    stubSearch([ROBIN, LOCUST])
    const { input } = renderBox()
    type(input, 'robin')
    await wait(250)
    expect(groupHeadings()).toEqual([])
    expect(document.querySelector('.search-sources').textContent).not.toContain('Ranks')
  })

  it('shows the ranks alone, under their heading, when no species is found', async () => {
    stubSearch([], [TURDIDAE])
    const { input } = renderBox()
    type(input, 'turdidae')
    await wait(250)
    expect(groupHeadings()).toEqual(['Genera, families and higher ranks'])
    expect(listedTexts()).toEqual(['Family Turdidae (Passeriformes)1 species · 1 record'])
  })

  it('moves from the species to the ranks with the arrow keys, and picks a rank with Enter or a click', async () => {
    stubSearch([ROBIN], [ROBINIA, TURDIDAE])
    const { input, onSelect, onSelectRank } = renderBox()
    type(input, 'robin')
    await wait(250)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(screen.getAllByRole('option')[1].getAttribute('aria-selected')).toBe('true')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSelectRank).toHaveBeenCalledWith({ rank: 'genus', name: 'Robinia', path: ROBINIA.path })
    expect(onSelect).not.toHaveBeenCalled()
    expect(screen.queryByRole('listbox')).toBeNull()

    fireEvent.focus(input)
    fireEvent.click(screen.getAllByRole('option')[2])
    expect(onSelectRank).toHaveBeenLastCalledWith({ rank: 'family', name: 'Turdidae', path: TURDIDAE.path })
  })

  it('lists the ranks first when a rank\'s name is the query, ignoring case, so Enter opens that rank', async () => {
    const TURDUS = {
      rank: 'genus', name: 'Turdus', label: 'Genus Turdus (Turdidae)', species_count: 5, record_count: 965555,
      path: [...TURDIDAE.path, { rank: 'genus', name: 'Turdus' }], match: { kind: 'exact' },
    }
    stubSearch([ROBIN], [TURDUS, TURDIDAE])
    const { input, onSelect, onSelectRank } = renderBox()
    type(input, ' turdus ')
    await wait(250)
    expect(groupHeadings()).toEqual(['Genera, families and higher ranks', 'Species'])
    const texts = listedTexts()
    expect(texts[0]).toBe('Genus Turdus (Turdidae)5 species · 965,555 records')
    expect(texts[1]).toBe('Family Turdidae (Passeriformes)1 species · 1 record')
    expect(texts[2]).toContain('Turdus migratorius')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSelectRank).toHaveBeenCalledWith({ rank: 'genus', name: 'Turdus', path: TURDUS.path })
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('keeps the species first when no rank\'s name is the query, even when a rank starts with it', async () => {
    stubSearch([ROBIN, LOCUST], [ROBINIA])
    const { input, onSelect } = renderBox()
    type(input, 'Robin')
    await wait(250)
    expect(groupHeadings()).toEqual(['Species', 'Genera, families and higher ranks'])
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSelect).toHaveBeenCalledWith(ROBIN)
  })

  it('decides the order from the rank names alone', () => {
    const named = (...names) => names.map((name) => ({ name }))
    expect(ranksFirst(named('Aves'), 'aves')).toBe(true)
    expect(ranksFirst(named('Aves'), '  AVES ')).toBe(true)
    expect(ranksFirst(named('Turdidae', 'Turdus'), 'Turdus')).toBe(true)
    expect(ranksFirst(named('Robinia'), 'robin')).toBe(false)
    expect(ranksFirst(named('Turdidae'), 'turd')).toBe(false)
    expect(ranksFirst([], 'aves')).toBe(false)
  })

  it('works with an API that sends no ranks', async () => {
    const fetchMock = vi.fn(async () => answer(200, { query: 'robin', results: [ROBIN] }))
    vi.stubGlobal('fetch', fetchMock)
    const { input } = renderBox()
    type(input, 'robin')
    await wait(250)
    expect(listedTexts()).toHaveLength(1)
  })
})

describe('messages', () => {
  it('says when nothing is found', async () => {
    stubSearch([])
    const { input } = renderBox()
    type(input, 'zzqqxx')
    await wait(250)
    expect(screen.getByText(
      'No species found for "zzqqxx". Only species recorded in BC can be found.')).toBeTruthy()
  })

  it('says when the API cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    }))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { input } = renderBox()
    type(input, 'robin')
    await wait(250)
    expect(screen.getByText(
      'Species search is not available: the API could not be reached.')).toBeTruthy()
  })
})
