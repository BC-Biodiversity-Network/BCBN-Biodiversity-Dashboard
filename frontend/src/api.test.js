import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError, MIN_SEARCH_CHARACTERS, fetchCell, fetchDataset, fetchDatasets, fetchRank, fetchRankHexes, searchSpecies,
  searchWords, searchableLength,
} from './api'

// Each case is a search and the number of letters and digits the API counts
// in it. The numbers were taken from the API itself (the search_norm macro in
// backend/api/search.py), so this checks the page and the API agree on what
// is too short.
const API_COUNTS = [
  ['robin', 5],
  ['r', 1],
  ['', 0],
  ['   ', 0],
  [' r ', 1],
  ['--', 0],
  ['x', 0],
  ['X', 0],
  ['Dahlia × pinnata', 13],
  ['Dahlia x pinnata', 13],
  ['×', 0],
  ["Scouler's willow", 14],
  ['Douglas-fir', 10],
  ['59PMB', 5],
  ['8KCY', 4],
  ['é', 1],
  ['ée', 2],
  ['Culex (Culex) pipiens', 17],
  ['a b', 2],
  ['x y', 1],
  ['Sp. 2', 3],
  ['  -- ab --  ', 2],
]

describe('searchWords', () => {
  // Each text with the words the API searches for it (search_norm in
  // backend/api/search.py, checked in DuckDB) and the text as typed.
  it.each([
    ['salmon wetland', [['salmon', 'salmon'], ['wetland', 'wetland']]],
    ['  Douglas-fir, O’Brien’s  ', [['douglas', 'Douglas'], ['fir', 'fir'], ['obriens', 'O’Brien’s']]],
    ["salmon' 'eggs", [['salmon', 'salmon'], ['eggs', 'eggs']]],
    ['École école ECOLE', [['ecole', 'École']]],
    ['ﬁsh x² Æsop', [['sh', 'sh'], ['sop', 'sop']]],
    ['a x x x b', [['a', 'a'], ['x', 'x'], ['b', 'b']]],
    ['x salmon x', [['salmon', 'salmon']]],
    [' - ', []],
  ])('splits %j as the API does', (text, words) => {
    expect(searchWords(text).map(({ word, typed }) => [word, typed])).toEqual(words)
  })

  it('keeps a combining accent with its letter', () => {
    expect(searchWords('cafe\u0301 bar')).toEqual([{ word: 'cafe', typed: 'cafe\u0301' }, { word: 'bar', typed: 'bar' }])
  })
})

describe('searchableLength', () => {
  it.each(API_COUNTS)('counts %j as %i, like the API', (text, count) => {
    expect(searchableLength(text)).toBe(count)
  })

  it('agrees with the API on which searches are too short', () => {
    // 'x y' has only one letter that counts (a lone x is a hybrid sign).
    expect(searchableLength('x y')).toBeLessThan(MIN_SEARCH_CHARACTERS)
    expect(searchableLength('59PMB')).toBeGreaterThanOrEqual(MIN_SEARCH_CHARACTERS)
  })
})

// A pretend answer from fetch: just the parts api.js reads. body is what
// json() gives back; notJson makes json() fail, like an HTML error page.
function fakeResponse(status, body, { notJson = false } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (notJson) {
        throw new SyntaxError('Unexpected token <')
      }
      return body
    },
  }
}

// Replaces fetch for one test with one that gives this answer, and returns
// the pretend fetch so the test can look at its calls.
function stubFetch(answer) {
  const fetchMock = vi.fn(async () => answer)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

// Replaces fetch for one test with one that fails with this error, the way
// the real fetch fails when there is no answer at all.
function stubFetchFailure(error) {
  vi.stubGlobal('fetch', vi.fn(async () => {
    throw error
  }))
}

// Runs a search and returns the error it fails with.
async function searchError() {
  try {
    await searchSpecies('robin')
  } catch (error) {
    return error
  }
  throw new Error('the search did not fail')
}

describe('searchSpecies', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('asks /api/search with the query and the limit', async () => {
    const fetchMock = stubFetch(fakeResponse(200, { results: [] }))
    await searchSpecies('Dahlia × pinnata', { limit: 5 })
    const url = fetchMock.mock.calls[0][0]
    expect(url.startsWith('/api/search?')).toBe(true)
    const params = new URLSearchParams(url.split('?')[1])
    expect(params.get('q')).toBe('Dahlia × pinnata')
    expect(params.get('limit')).toBe('5')
    expect(params.has('rank_limit')).toBe(false)
  })

  it('asks for the ranks too when given how many', async () => {
    const fetchMock = stubFetch(fakeResponse(200, { results: [], ranks: [] }))
    await searchSpecies('robin', { limit: 20, rankLimit: 5 })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/search?q=robin&limit=20&rank_limit=5')
  })

  it('returns the answer when the API answers', async () => {
    stubFetch(fakeResponse(200, { query: 'robin', results: [{ specieskey: '59PMB' }] }))
    expect((await searchSpecies('robin')).results[0].specieskey).toBe('59PMB')
  })

  it('calls it "unreachable" when fetch itself fails (no API, no network)', async () => {
    stubFetchFailure(new TypeError('Failed to fetch'))
    const error = await searchError()
    expect(error).toBeInstanceOf(ApiError)
    expect(error.kind).toBe('unreachable')
  })

  it.each([502, 503, 504])(
    'calls a %i "unreachable" (the dev server answers that when the API is down)',
    async (status) => {
      stubFetch(fakeResponse(status, null, { notJson: true }))
      expect((await searchError()).kind).toBe('unreachable')
    },
  )

  it('calls an error page that is not JSON "unreachable"', async () => {
    stubFetch(fakeResponse(404, null, { notJson: true }))
    expect((await searchError()).kind).toBe('unreachable')
  })

  it('calls a 4xx from the API "rejected" and keeps its message', async () => {
    stubFetch(fakeResponse(400, { detail: "Search needs at least 2 letters or digits; got 'r'." }))
    const error = await searchError()
    expect(error.kind).toBe('rejected')
    expect(error.status).toBe(400)
    expect(error.message).toBe("Search needs at least 2 letters or digits; got 'r'.")
  })

  it('calls a 5xx from the API "failed"', async () => {
    stubFetch(fakeResponse(500, { detail: 'Internal Server Error' }))
    const error = await searchError()
    expect(error.kind).toBe('failed')
    expect(error.status).toBe(500)
  })

  it('calls a 200 answer that is not JSON "failed"', async () => {
    stubFetch(fakeResponse(200, null, { notJson: true }))
    expect((await searchError()).kind).toBe('failed')
  })

  it('passes a cancelled request through untouched, so the page can ignore it', async () => {
    const aborted = new DOMException('The operation was aborted.', 'AbortError')
    stubFetchFailure(aborted)
    const error = await searchError()
    expect(error).toBe(aborted)
    expect(error).not.toBeInstanceOf(ApiError)
  })
})

describe('the dataset requests', () => {
  // Records the address each request goes to, and answers with an empty object.
  function recordUrls() {
    const urls = []
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      urls.push(url)
      return { ok: true, status: 200, json: async () => ({}) }
    }))
    return urls
  }

  it('asks for BC data only, unless the wider datasets are wanted, with every filter value', async () => {
    const urls = recordUrls()
    await fetchDatasets({ q: 'eagle', catalogues: ['a', 'b'], wider: false, page: 2 })
    await fetchDatasets({ q: '', catalogues: [], wider: true, page: 1 })
    expect(urls[0]).toBe('/api/datasets?page=2&page_size=20&q=eagle&catalogue=a&catalogue=b' +
      '&frontend_group=bc_data')
    expect(urls[1]).toBe('/api/datasets?page=1&page_size=20&frontend_group=bc_data&frontend_group=pending')
  })

  it('sends the sort and every filter, and relevance only with a search', async () => {
    const urls = recordUrls()
    await fetchDatasets({ q: 'eagle', sort: 'title', catalogues: [], yearFrom: 2010, yearTo: null,
      organism: ' Oncorhynchus ', access: ['open', 'restricted'], mappable: true, species: '49JFH', wider: false,
      page: 1 })
    await fetchDatasets({ q: '', sort: 'relevance', catalogues: [], access: [], species: '', wider: false, page: 1 })
    expect(urls[0]).toBe('/api/datasets?page=1&page_size=20&q=eagle&sort=title&year_from=2010' +
      '&organism=Oncorhynchus&access=open&access=restricted&mappable=true&species=49JFH&frontend_group=bc_data')
    expect(urls[1]).toBe('/api/datasets?page=1&page_size=20&frontend_group=bc_data')
  })

  it('keeps the slashes of an id, which the API reads as part of it', async () => {
    const urls = recordUrls()
    await fetchDataset('oai:datadryad.org:doi:10.5061/dryad.x y')
    expect(urls[0]).toBe('/api/datasets/oai%3Adatadryad.org%3Adoi%3A10.5061/dryad.x%20y')
  })
})

describe('fetchCell', () => {
  it('asks for one hexagon with the page, and the years, filter and species only when given', async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, json: async () => ({ cell: '872800b30ffffff' }) })
    await fetchCell('872800b30ffffff')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/hexes/872800b30ffffff?offset=0&limit=50')
    await fetchCell('872800b30ffffff', { years: { from: 2000, to: null }, q: ' robin ', species: '59PMB', offset: 50 })
    expect(fetchMock.mock.calls[1][0]).toBe(
      '/api/hexes/872800b30ffffff?offset=50&limit=50&year_from=2000&q=robin&species=59PMB')
  })

  it('asks for the species of a rank in one hexagon with the rank and its path', async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, json: async () => ({ cell: '872800b30ffffff' }) })
    await fetchCell('872800b30ffffff', { rank: APIDAE })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/hexes/872800b30ffffff?offset=0&limit=50&rank=family'
      + '&kingdom=Animalia&phylum=Arthropoda&class=Insecta&family=Apidae')
  })

  it('sends the BC statuses and the order, leaving the default order out', async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, json: async () => ({}) })
    await fetchCell('872800b30ffffff', { status: ['red', 'none'], sort: 'english' })
    await fetchCell('872800b30ffffff', { status: [], sort: 'most_records' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/hexes/872800b30ffffff?offset=0&limit=50&status=red&status=none'
      + '&sort=english')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/hexes/872800b30ffffff?offset=0&limit=50')
  })
})

// A bee family GBIF files with no order.
const APIDAE = { rank: 'family', name: 'Apidae', path: [
  { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Arthropoda' }, { rank: 'class', name: 'Insecta' },
  { rank: 'order', name: null }, { rank: 'family', name: 'Apidae' },
] }

describe('the rank requests', () => {
  it('asks for the card of a rank with its path, a missing rank left out, and the page', async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, json: async () => ({}) })
    await fetchRank(APIDAE)
    await fetchRank(APIDAE, { offset: 50, limit: 25 })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/ranks/family?kingdom=Animalia&phylum=Arthropoda&class=Insecta'
      + '&family=Apidae&offset=0&limit=50')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/ranks/family?kingdom=Animalia&phylum=Arthropoda&class=Insecta'
      + '&family=Apidae&offset=50&limit=25')
  })

  it('sends the year range of a card, leaving out an open end', async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, json: async () => ({}) })
    await fetchRank(APIDAE, { years: { from: 2000, to: 2020 } })
    await fetchRank(APIDAE, { years: { from: null, to: 1999 } })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/ranks/family?kingdom=Animalia&phylum=Arthropoda&class=Insecta'
      + '&family=Apidae&year_from=2000&year_to=2020&offset=0&limit=50')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/ranks/family?kingdom=Animalia&phylum=Arthropoda&class=Insecta'
      + '&family=Apidae&year_to=1999&offset=0&limit=50')
  })

  it('sends the card\'s list options: name filter, statuses and order', async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, json: async () => ({}) })
    await fetchRank(APIDAE, { list: { q: ' bee ', status: ['exotic'], sort: 'scientific', page: 2 }, offset: 50 })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/ranks/family?kingdom=Animalia&phylum=Arthropoda&class=Insecta'
      + '&family=Apidae&q=bee&status=exotic&sort=scientific&offset=50&limit=50')
  })

  it('asks for the map of a rank at one resolution, with the years only when given', async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, json: async () => ({}) })
    await fetchRankHexes(APIDAE, 6)
    await fetchRankHexes(APIDAE, 7, { years: { from: null, to: 1999 } })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/ranks/family/hexes?res=6&kingdom=Animalia&phylum=Arthropoda'
      + '&class=Insecta&family=Apidae')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/ranks/family/hexes?res=7&year_to=1999&kingdom=Animalia'
      + '&phylum=Arthropoda&class=Insecta&family=Apidae')
  })
})
