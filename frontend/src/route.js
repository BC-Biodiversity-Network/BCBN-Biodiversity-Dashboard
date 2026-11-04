import { useCallback, useEffect, useState } from 'react'

// Which page is shown, and the Datasets page's state, kept in the address
// bar's query string so a link can be shared and the back button works.
//
//   (no query)                        the species map, as the site always was
//   ?species=<key>                    the species map with that species'
//                                     card open
//   ?cell=<H3 id>                     the species map with that hexagon's
//                                     panel open (the species in it)
//   ?species=<key>&cell=<H3 id>       that species' map with the hexagon's
//                                     panel open, about that species
//   ?rank=genus&kingdom=Animalia&phylum=Chordata&class=Aves
//       &order=Passeriformes&family=Turdidae&genus=Turdus
//                                     a rank's page: its card and its map (the
//                                     overview restricted to its species);
//                                     with &cell=<H3 id>, its hexagon panel
//   ?view=datasets&q=eagle&sort=oldest&catalogue=catalogue.data.gov.bc.ca
//       &year_from=2010&year_to=2020&organism=salmon&access=open&mappable=1
//       &species=<key>&wider=1&page=2&dataset=<id>  the Datasets page
//
// catalogue and access can be given more than once; sort is left out for
// the default order. On the Datasets page, species keeps the datasets that
// mention that species (it is not part of the Filters panel). The query string is used rather
// than paths such as /datasets so the built site works on any static host,
// with no rewrite rules.

// Which page a query string asks for: 'datasets' or 'species'.
export function readView(search) {
  return new URLSearchParams(search).get('view') === 'datasets' ? 'datasets' : 'species'
}

// The species whose card is open on the species map, from a query string:
// its key, or null for none.
export function readSpeciesKey(search) {
  return new URLSearchParams(search).get('species') || null
}

// An H3 hexagon id at resolution 4 to 7, as the map draws them: "8", the
// resolution, then 13 more hexadecimal digits (the same rule as CELL_ID in
// backend/api/cells.py).
const CELL_ID = /^8[4-7][0-9a-f]{13}$/

// The hexagon whose panel is open on the species map, from a query string:
// its id in lower case, or null for none (or for something that is not a
// hexagon id).
export function readCell(search) {
  const cell = (new URLSearchParams(search).get('cell') || '').trim().toLowerCase()
  return CELL_ID.test(cell) ? cell : null
}

// The history state of an address entry added by a click on a hexagon, so
// the panel's x knows it can go back instead of leaving a dead entry (see
// App.jsx).
export const PANEL_FROM_CLICK = { panelFromClick: true }

// Whether a query string is the plain species map: no species, rank or
// hexagon open.
export function isPlainMap(search) {
  return !readSpeciesKey(search) && !readRank(search) && !readCell(search)
}

// The ranks a rank page can be about, highest first (the same list as
// RANKS in backend/api/ranks.py).
export const RANKS = ['kingdom', 'phylum', 'class', 'order', 'family', 'genus']

// The rank whose page is open on the species map, from a query string:
// { rank, name, path }, where path is [{ rank, name }] from the kingdom down
// to the rank (name null for a missing rank), or null for none. A rank is
// named by its full path, one parameter per rank, as the API takes it
// (?rank=genus&kingdom=Animalia&...&genus=Turdus). An address that also
// names a species, names no rank, or gives no name for the rank itself
// opens no rank.
export function readRank(search) {
  const params = new URLSearchParams(search)
  const rank = params.get('rank')
  if (!RANKS.includes(rank) || params.get('species')) {
    return null
  }
  const path = RANKS.slice(0, RANKS.indexOf(rank) + 1).map((name) => ({ rank: name, name: params.get(name) || null }))
  const own = path[path.length - 1].name
  return own ? { rank, name: own, path } : null
}

// A short text that is the same for the same rank path and different for
// any other, to compare ranks and to file their maps under.
export function rankKey(rankRef) {
  return rankRef ? rankRef.path.map((step) => step.name ?? '').join('|') + `|${rankRef.rank}` : null
}

// The query string for the species map: with a species' card open (species,
// a key), or a rank's page (rank, { rank, path }), never both, and a
// hexagon's panel open (cell); each null for none.
export function mapSearch({ species = null, rank = null, cell = null } = {}) {
  const params = new URLSearchParams()
  if (species) {
    params.set('species', species)
  } else if (rank) {
    params.set('rank', rank.rank)
    for (const step of rank.path) {
      if (step.name) {
        params.set(step.rank, step.name)
      }
    }
  }
  if (cell) {
    params.set('cell', cell)
  }
  const text = params.toString()
  return text ? `?${text}` : '?'
}

// The query string for the species map with a species' card open (key, or
// null for none) and a hexagon's panel open (cell, or null for none).
export function speciesMapSearch(key, cell = null) {
  return mapSearch({ species: key, cell })
}

// The orders the list can be sorted in. Relevance only makes sense with a
// search, and is the default then; without one the default is newest.
export const SORTS = ['relevance', 'newest', 'oldest', 'title']

// Reads a year from the address: a whole number from 1000 to 9999, or null.
function readYear(text) {
  const year = Number.parseInt(text ?? '', 10)
  return Number.isInteger(year) && year >= 1000 && year <= 9999 ? year : null
}

// The Datasets page's state from a query string: { q, sort, catalogues,
// yearFrom, yearTo, organism, access, mappable, species, wider, page,
// dataset }. species is a species key, or '' for none.
// Missing or odd values fall back to the defaults (sort '' is the default
// order).
export function readDatasetsState(search) {
  const params = new URLSearchParams(search)
  const page = Number.parseInt(params.get('page') ?? '', 10)
  const sort = params.get('sort') ?? ''
  return {
    q: params.get('q') ?? '',
    sort: SORTS.includes(sort) ? sort : '',
    catalogues: params.getAll('catalogue'),
    yearFrom: readYear(params.get('year_from')),
    yearTo: readYear(params.get('year_to')),
    organism: params.get('organism') ?? '',
    access: params.getAll('access'),
    mappable: params.get('mappable') === '1',
    species: params.get('species') ?? '',
    wider: params.get('wider') === '1',
    page: Number.isFinite(page) && page > 1 ? page : 1,
    dataset: params.get('dataset') || null,
  }
}

// The query string for a Datasets page state, leaving out every value that
// is the default, so the plain page is just "?view=datasets".
export function datasetsSearch(state) {
  const params = new URLSearchParams({ view: 'datasets' })
  if (state.q?.trim()) {
    params.set('q', state.q.trim())
  }
  if (state.sort) {
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
    params.set('mappable', '1')
  }
  if (state.species) {
    params.set('species', state.species)
  }
  if (state.wider) {
    params.set('wider', '1')
  }
  if (state.page > 1) {
    params.set('page', String(state.page))
  }
  if (state.dataset) {
    params.set('dataset', state.dataset)
  }
  return `?${params}`
}

// The sort the list is shown in: the one asked for, unless it is relevance
// with no search (or none was asked for), when it is the default.
export function shownSort(state) {
  const searching = Boolean(state.q?.trim())
  if (!state.sort || (state.sort === 'relevance' && !searching)) {
    return searching ? 'relevance' : 'newest'
  }
  return state.sort
}

// How many filters of the Filters panel are in use: the catalogue, the
// published years, the organism, the access and the location each count
// once. The species filter is not in the panel, so it is not counted.
export function activeFilterCount(state) {
  return [state.catalogues?.length > 0, state.yearFrom != null || state.yearTo != null,
    Boolean(state.organism?.trim()), state.access?.length > 0, Boolean(state.mappable)].filter(Boolean).length
}

// The changes that clear every filter of the Filters panel.
export const NO_FILTERS = { catalogues: [], yearFrom: null, yearTo: null, organism: '', access: [], mappable: false }

// The address of the Datasets page listing the datasets that mention one
// species, with nothing else set.
export function speciesDatasetsSearch(key) {
  return datasetsSearch({ species: key, page: 1 })
}

// Keeps track of the address bar's query string. Returns [search, navigate]:
// navigate(newSearch) puts a new entry in the browser history (so back
// returns to the old one), or replaces the current one with { replace: true }.
// state is kept with the entry (window.history.state while it is the current
// one; null unless given). The back and forward buttons update search too.
export function useLocationSearch() {
  const [search, setSearch] = useState(() => window.location.search)

  useEffect(() => {
    // Called when the back or forward button changes the address.
    function onPopState() {
      setSearch(window.location.search)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const navigate = useCallback((newSearch, { replace = false, state = null } = {}) => {
    const url = `${window.location.pathname}${newSearch === '?' ? '' : newSearch}`
    if (replace) {
      window.history.replaceState(state, '', url)
    } else {
      window.history.pushState(state, '', url)
    }
    setSearch(window.location.search)
  }, [])

  return [search, navigate]
}
