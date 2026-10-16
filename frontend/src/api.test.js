import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, MIN_SEARCH_CHARACTERS, searchSpecies, searchableLength } from './api'

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
