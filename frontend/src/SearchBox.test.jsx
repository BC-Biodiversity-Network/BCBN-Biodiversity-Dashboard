import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SearchBox from './SearchBox'
import SelectedSpecies from './SelectedSpecies'

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

// Replaces fetch with one that answers every search with these results.
function stubSearch(results) {
  const fetchMock = vi.fn(async (url) => answer(200, {
    query: new URLSearchParams(url.split('?')[1]).get('q'), results,
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
// function it calls when a species is picked.
function renderBox() {
  const onSelect = vi.fn()
  render(<SearchBox onSelect={onSelect} />)
  return { input: screen.getByRole('combobox'), onSelect }
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
  it('shows the scientific name first, the English name labelled as BCSEE, and the family and records', async () => {
    stubSearch([ROBIN])
    const { input } = renderBox()
    type(input, 'robin')
    await wait(250)
    const text = listedTexts()[0]
    expect(text.indexOf('Turdus migratorius')).toBe(0)
    expect(text).toContain('Linnaeus, 1766')
    expect(text).toContain('American RobinEnglish name, BCSEE')
    expect(text).toContain('Turdidae · 964,801 records')
  })

  it('labels a BCSEE entry name as such, never as the species English name', async () => {
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
    expect(text).toContain('small-flowered fiddleneckBCSEE entry name')
    expect(text).not.toContain('English name, BCSEE')
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

describe('the selected species placeholder', () => {
  it('says which old key was typed when it was merged', () => {
    render(<SelectedSpecies
      species={makeResult({ specieskey: '79DVG', col_name: 'Salix scouleriana', merged_from: 'VM8MB' })}
      onClear={() => {}} />)
    expect(screen.getByText('key 79DVG')).toBeTruthy()
    expect(screen.getByText(
      'You searched for VM8MB, an old key that was merged into 79DVG.')).toBeTruthy()
  })
})
