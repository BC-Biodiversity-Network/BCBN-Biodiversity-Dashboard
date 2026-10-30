// Everything the front end needs to talk to the species API (backend/api/).
//
// The API's address is set here, and only here. By default it is "/api" on the
// same site as the page: in development the Vite dev server passes /api on to
// the local API server (see vite.config.js), and in production the site can
// serve the API under /api too. If the API ends up somewhere else, set
// VITE_API_BASE when building, for example
//   VITE_API_BASE=https://api.example.org/api npm run build
// and nothing else needs to change.
export const API_BASE = import.meta.env.VITE_API_BASE || '/api'

// The shortest search the API accepts: 2 letters or digits. The same rule as
// MIN_QUERY_LENGTH in backend/api/search.py.
export const MIN_SEARCH_CHARACTERS = 2

// How a request to the API went wrong, so the page can say something useful:
//   'unreachable'  the API did not answer (not running, or no network)
//   'rejected'     the API answered that the request was wrong (a 4xx)
//   'failed'       the API answered with an error of its own (a 5xx)
export class ApiError extends Error {
  constructor(kind, message, status = null) {
    super(message)
    this.kind = kind
    this.status = status
  }
}

// Counts the letters and digits in a search the way the API does: accents
// removed, apostrophes dropped, everything that is not a letter or digit
// treated as a gap, and a lone "x" (a hybrid sign) ignored. So "x", " - " and
// "é" are all too short, exactly as the API decides.
export function searchableLength(text) {
  const words = text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/['’‘`]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .split(' ')
    .filter((word) => word !== '' && word !== 'x')

  return words.join('').length
}

// Fetches one API path and returns its JSON. Turns every way it can fail into
// an ApiError. signal lets the caller cancel the request (an AbortError is
// passed through untouched, so the caller can ignore it).
async function fetchJson(path, signal) {
  let response
  try {
    response = await fetch(`${API_BASE}${path}`, { signal })
  } catch (error) {
    if (error.name === 'AbortError') {
      throw error
    }
    throw new ApiError('unreachable', 'The species API could not be reached.')
  }

  // When the API server is not running, the Vite dev server answers in its
  // place with a gateway error, so those count as "could not be reached" too.
  if ([502, 503, 504].includes(response.status)) {
    throw new ApiError('unreachable', 'The species API could not be reached.', response.status)
  }

  let body = null
  try {
    body = await response.json()
  } catch {
    // Not JSON: an error page from something other than the API.
    throw new ApiError(
      response.ok ? 'failed' : 'unreachable',
      'The species API sent an answer that could not be read.',
      response.status,
    )
  }

  if (!response.ok) {
    const detail = typeof body?.detail === 'string' ? body.detail : `Error ${response.status}`
    throw new ApiError(response.status < 500 ? 'rejected' : 'failed', detail, response.status)
  }

  return body
}

// Searches the species. Returns the API's answer: { query, normalised_query,
// results }, best match first.
export function searchSpecies(query, { limit = 20, signal } = {}) {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  return fetchJson(`/search?${params}`, signal)
}

// Fetches one species card. key can be an old merged key: the API then sends
// the kept key's card with merged_from set. A key with no card fails with an
// ApiError of kind 'rejected' and status 404.
export function fetchSpecies(key, { signal } = {}) {
  return fetchJson(`/species/${encodeURIComponent(key)}`, signal)
}

// Fetches what the data is and where it comes from (/api/meta), including
// the GBIF download to cite.
export function fetchMeta({ signal } = {}) {
  return fetchJson('/meta', signal)
}

// The query string for a map at one resolution and, if years is set, a
// range of years. years is null for all years, or { from, to } where either
// may be null for an open end (the API fills it in from the data).
function mapParams(resolution, years) {
  const params = new URLSearchParams({ res: String(resolution) })
  if (years?.from != null) {
    params.set('year_from', String(years.from))
  }
  if (years?.to != null) {
    params.set('year_to', String(years.to))
  }
  return params
}

// Fetches the species map at one resolution (/api/species/{key}/hexes),
// for a range of years if years is set (see mapParams). key can be an old
// merged key: the API then sends the kept key's map.
export function fetchSpeciesHexes(key, resolution, { signal, years = null } = {}) {
  return fetchJson(`/species/${encodeURIComponent(key)}/hexes?${mapParams(resolution, years)}`, signal)
}

// Fetches one page of Lunaris datasets (/api/datasets). state is the
// Datasets page's state (see route.js): q, sort, the filters (catalogues,
// yearFrom, yearTo, organism, access, mappable, species), wider (also datasets that
// may include BC, the pending group) and page. Relevance is only sent with
// a search. The page does not filter by the location classes, so
// final_class is never sent. Returns the API's answer: { total, pages,
// sort, results, options, ... }.
export function fetchDatasets(state, { pageSize = 20, signal } = {}) {
  const params = new URLSearchParams({ page: String(state.page ?? 1), page_size: String(pageSize) })
  if (state.q?.trim()) {
    params.set('q', state.q.trim())
  }
  if (state.sort && (state.sort !== 'relevance' || state.q?.trim())) {
    params.set('sort', state.sort)
  }
  for (const catalogue of state.catalogues ?? []) {
    params.append('catalogue', catalogue)
  }
  if (state.yearFrom != null) {
    params.set('year_from', String(state.yearFrom))
  }
  if (state.yearTo != null) {
    params.set('year_to', String(state.yearTo))
  }
  if (state.organism?.trim()) {
    params.set('organism', state.organism.trim())
  }
  for (const access of state.access ?? []) {
    params.append('access', access)
  }
  if (state.mappable) {
    params.set('mappable', 'true')
  }
  if (state.species) {
    params.set('species', state.species)
  }
  params.append('frontend_group', 'bc_data')
  if (state.wider) {
    params.append('frontend_group', 'pending')
  }
  return fetchJson(`/datasets?${params}`, signal)
}

// Fetches one Lunaris dataset with every field (/api/datasets/{id}). The id
// of another copy gives the copy shown, with requested_copy set. Ids can hold
// slashes (Dryad DOIs), which the API reads as part of the id, so each part
// of the id is encoded on its own and the slashes are kept.
export function fetchDataset(id, { signal } = {}) {
  return fetchJson(`/datasets/${id.split('/').map(encodeURIComponent).join('/')}`, signal)
}

// Fetches the overview map (records and species per hexagon) at one
// resolution for a range of years (/api/overview/hexes). With all years the
// page uses the shipped overview files instead (see App.jsx).
export function fetchOverviewHexes(resolution, { signal, years = null } = {}) {
  return fetchJson(`/overview/hexes?${mapParams(resolution, years)}`, signal)
}
