import { useCallback, useEffect, useRef, useState } from 'react'

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
//   ...&q=thrush&sort=english&status=red&status=none&page=2
//                                     a rank card's species list: its name
//                                     filter, order, BC status filter (any
//                                     number) and page
//   ...&cell=<H3 id>&cell_q=thrush&cell_sort=english&cell_status=red
//       &cell_page=2                  a hexagon panel's species list, the
//                                     same options with cell_ in front, as
//                                     both lists can be open at once (the
//                                     panel covers the card)
//   ...&area=-120.6,50.55,-120.1,50.8&area_res=6&area_q=thrush&area_page=2
//                                     an area: the box drawn on the map
//                                     (west, south, east, north), the
//                                     resolution it was drawn at, and its
//                                     panel's species list's options with
//                                     area_ in front, as for a hexagon; it
//                                     stays until it is cleared, with a
//                                     hexagon's panel or a species' card on
//                                     top of it
//   ...&area_panel=closed             the area stays on the map with its
//                                     panel closed
//   ...&year_from=2000&year_to=2020   the year range of whichever map is
//                                     shown (either end can be left out for
//                                     an open end), on any species map
//                                     address, so a shared link or the back
//                                     button keeps the years
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

// Whether a query string is the plain species map: no species, rank,
// hexagon or area open.
export function isPlainMap(search) {
  return !readSpeciesKey(search) && !readRank(search) && !readCell(search) && !readArea(search)
}

// Whether two query strings ask for the same thing, whatever the order of
// their parameters and with or without the "?" ('' and '?' are the same).
export function sameAddress(a, b) {
  // A query string's parameters in name order, as text.
  const sorted = (search) => {
    const params = new URLSearchParams(search)
    params.sort()
    return params.toString()
  }
  return sorted(a) === sorted(b)
}

// A query string with its hexagon (cell and the cell_ list options) taken
// out, everything else kept.
function withoutCell(search) {
  const params = new URLSearchParams(search)
  for (const name of ['cell', `${CELL_LIST}q`, `${CELL_LIST}sort`, `${CELL_LIST}status`, `${CELL_LIST}page`]) {
    params.delete(name)
  }
  return params.toString()
}

// The area's place in the history. Every address entry that holds an area
// keeps it in its history state, so "Clear area" can take the area out of
// the history altogether (see clearAreaMove), and no earlier entry is left
// to bring it back when a panel's x or the back button returns to it:
//   areaDepth   how many entries this one comes after the first entry that
//               held the area (0 for that first entry);
//   areaBefore  the address of the entry just before that first one, when
//               the area was drawn in this visit; left out when the area
//               came with the page (a shared link), as the entry before it
//               then belongs to another page or site.
const AREA_HISTORY_KEYS = ['areaDepth', 'areaBefore']

// A history state without the area's place in it: null when nothing else is
// left.
function withoutAreaHistory(state) {
  if (!state) {
    return null
  }
  const rest = { ...state }
  for (const key of AREA_HISTORY_KEYS) {
    delete rest[key]
  }
  return Object.keys(rest).length > 0 ? rest : null
}

// The history state to give a new or replaced address entry (target), from
// the state asked for (state) and the current entry (currentSearch and
// currentState): the state asked for, with the area's place in the history
// (see AREA_HISTORY_KEYS) when target holds an area, else without it.
//   - The first entry with an area, added after an entry without one (a box
//     drawn): depth 0, and the entry before it is the current one.
//   - A replaced entry keeps the current entry's place; a new entry comes
//     one further along.
//   - An entry holding an area with no place recorded came with the page:
//     depth 0, with nothing before it to go back to.
export function withAreaHistory(state, { target, replace, currentSearch, currentState }) {
  const own = withoutAreaHistory(state)
  if (!readArea(target)) {
    return own
  }
  let place
  if (!readArea(currentSearch)) {
    place = replace ? { areaDepth: 0 } : { areaDepth: 0, areaBefore: currentSearch }
  } else {
    const depth = currentState?.areaDepth ?? 0
    place = { areaDepth: replace ? depth : depth + 1 }
    if (currentState?.areaBefore !== undefined) {
      place.areaBefore = currentState.areaBefore
    }
  }
  return { ...own, ...place }
}

// How "Clear area" moves through the history, so that no entry is left
// holding the area: it goes back past every entry that holds it, then shows
// target (the current address without the area) there. Returns { back,
// search, replace, state } for navigate (see useLocationSearch):
//   - area drawn in this visit, and target is the address from before it:
//     only go back to that entry (search null), as if the area was never
//     drawn;
//   - area drawn in this visit, target different (a hexagon's panel or a
//     rank opened since): go back to the entry before the area, then add
//     target as a new entry after it. Its state marks it as a hexagon
//     click's entry when the entry before is the same map without the
//     hexagon (so the panel's x goes back to it), or as a rank's entry
//     opened from there when the current entry was one;
//   - area that came with the page: go back to the first entry holding it
//     and put target in its place (in place, with no going back, when that
//     is the current entry, keeping the entry's other marks).
// currentState is the current entry's history state.
export function clearAreaMove(target, currentState) {
  const depth = currentState?.areaDepth ?? 0
  const before = currentState?.areaBefore
  if (before === undefined) {
    return { back: depth, search: target, replace: true, state: depth === 0 ? withoutAreaHistory(currentState) : null }
  }
  if (sameAddress(before, target)) {
    return { back: depth + 1, search: null, replace: false, state: null }
  }
  let state = null
  if (readCell(target) && sameAddress(before, withoutCell(target))) {
    state = PANEL_FROM_CLICK
  } else if (readRank(target) && !readCell(target) && currentState?.rankOpenedFrom !== undefined) {
    state = { rankOpenedFrom: before }
  }
  return { back: depth + 1, search: target, replace: false, state }
}

// How a new box drawn while an area exists (target, the address with the
// new area) moves through the history: like "Clear area" (see
// clearAreaMove), it goes back past every entry that holds the old area, so
// neither Back nor a panel's x can bring the old box back, then the new box
// takes the old one's place. Returns { back, search, replace, state } for
// navigate:
//   - old area drawn in this visit: go back to the entry before it, then add
//     target as a new entry after it, as the first entry of the new area
//     (so Back returns to what was there before either box);
//   - old area that came with the page: go back to the first entry holding
//     it and put target in its place (in place when that is the current
//     entry, keeping its other marks).
// currentState is the current entry's history state.
export function newAreaMove(target, currentState) {
  const depth = currentState?.areaDepth ?? 0
  const before = currentState?.areaBefore
  if (before === undefined) {
    const own = depth === 0 ? withoutAreaHistory(currentState) : null
    return { back: depth, search: target, replace: true, state: { ...own, areaDepth: 0 } }
  }
  return { back: depth + 1, search: target, replace: false, state: { areaDepth: 0, areaBefore: before } }
}

// The resolutions an area can be drawn at (those of the maps).
const AREA_RESOLUTIONS = [4, 5, 6, 7]

// Whether the area's panel is open (the default) or closed with the area
// kept on the map (area_panel=closed), from a query string.
export function readAreaPanelOpen(search) {
  return new URLSearchParams(search).get('area_panel') !== 'closed'
}

// The area on the species map, from a query string
// (area=west,south,east,north&area_res=6): { box: { west, south, east,
// north }, res }, or null for none, or for a box or resolution that cannot
// be one (an edge that is not a number or out of range, west not less than
// east, south not less than north, a resolution not 4 to 7). Whether the box
// is big enough is checked when it is drawn, and by the API.
export function readArea(search) {
  const params = new URLSearchParams(search)
  const edges = (params.get('area') ?? '').split(',').map((text) => (text.trim() === '' ? NaN : Number(text)))
  const res = Number(params.get('area_res'))
  if (edges.length !== 4 || edges.some((edge) => !Number.isFinite(edge)) || !AREA_RESOLUTIONS.includes(res)) {
    return null
  }
  const [west, south, east, north] = edges
  const inRange = west >= -180 && east <= 180 && south >= -90 && north <= 90
  return inRange && west < east && south < north ? { box: { west, south, east, north }, res } : null
}

// The ranks a rank page can be about, highest first (the same list as
// RANKS in backend/api/db.py).
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

// The orders a species list (a rank card's, a hexagon panel's) can be in,
// the default first, and its BC status filter's options: the same values as
// SORTS and STATUS_OPTIONS in backend/api/species_list.py.
export const LIST_SORTS = ['most_records', 'fewest_records', 'scientific', 'english']
export const LIST_STATUSES = ['red', 'blue', 'yellow', 'exotic', 'none']

// A species list with no filter, in the default order, on its first page.
export const NO_LIST = { q: '', sort: LIST_SORTS[0], status: [], page: 1 }

// The address prefix of a hexagon panel's list options (see the top of this
// file); a rank card's have none.
export const CELL_LIST = 'cell_'

// The address prefix of an area panel's list options.
export const AREA_LIST = 'area_'

// A species list's options from a query string, their names starting with
// prefix: { q, sort, status, page }, each its default when missing or not
// valid; status in LIST_STATUSES order, without repeats.
export function readList(search, prefix = '') {
  const params = new URLSearchParams(search)
  const sort = params.get(`${prefix}sort`)
  const asked = params.getAll(`${prefix}status`)
  const page = Number.parseInt(params.get(`${prefix}page`) ?? '', 10)
  return {
    q: (params.get(`${prefix}q`) ?? '').trim(),
    sort: LIST_SORTS.includes(sort) ? sort : NO_LIST.sort,
    status: LIST_STATUSES.filter((status) => asked.includes(status)),
    page: Number.isInteger(page) && page > 1 ? page : 1,
  }
}

// Adds a species list's options to a query string, their names starting
// with prefix, leaving the defaults out.
function addList(params, list, prefix) {
  if (!list) {
    return
  }
  if (list.q) {
    params.set(`${prefix}q`, list.q)
  }
  if (list.sort && list.sort !== NO_LIST.sort) {
    params.set(`${prefix}sort`, list.sort)
  }
  for (const status of list.status ?? []) {
    params.append(`${prefix}status`, status)
  }
  if (list.page > 1) {
    params.set(`${prefix}page`, String(list.page))
  }
}

// A list's order and status filter, with no name filter, on its first page:
// what another list of the same kind starts with, so clicking hexagon after
// hexagon keeps the order and the statuses chosen.
export function keptListChoices(list) {
  return { ...NO_LIST, sort: list.sort, status: list.status }
}

// The year range of the species map, from a query string: null for all
// years, or { from, to } with either end null for an open end (the year
// boxes' own shape, see yearRange.js). A value that is not a year from 1000
// to 9999 counts as left out.
export function readMapYears(search) {
  const params = new URLSearchParams(search)
  const from = readYear(params.get('year_from'))
  const to = readYear(params.get('year_to'))
  return from === null && to === null ? null : { from, to }
}

// Adds a year range ({ from, to }, or null for all years) to a query
// string's parameters, leaving out an open end.
function addYears(params, years) {
  if (years?.from != null) {
    params.set('year_from', String(years.from))
  }
  if (years?.to != null) {
    params.set('year_to', String(years.to))
  }
}

// A species map query string with its year range replaced by years (null
// for all years), everything else kept as it is.
export function withYears(search, years) {
  const params = new URLSearchParams(search)
  params.delete('year_from')
  params.delete('year_to')
  addYears(params, years)
  const text = params.toString()
  return text ? `?${text}` : '?'
}

// The query string for the species map: with a species' card open (species,
// a key), or a rank's page (rank, { rank, path }), never both, a hexagon's
// panel open (cell), and an area (area, { box, res }, see readArea), each
// null for none; an area and a hexagon can be there together (the hexagon's
// panel on top). rankList, cellList and areaList are the options of the
// rank card's, the hexagon panel's and the area panel's species lists (see
// readList), left out when null or the defaults; areaPanelOpen false keeps
// the area with its panel closed. years is the year range of the map (null
// for all years). The commas between the area's edges are written as
// commas, so the address stays readable.
export function mapSearch({
  species = null, rank = null, cell = null, area = null, rankList = null, cellList = null, areaList = null,
  areaPanelOpen = true, years = null,
} = {}) {
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
    addList(params, rankList, '')
  }
  if (cell) {
    params.set('cell', cell)
    addList(params, cellList, CELL_LIST)
  }
  if (area) {
    const { west, south, east, north } = area.box
    params.set('area', [west, south, east, north].join(','))
    params.set('area_res', String(area.res))
    addList(params, areaList, AREA_LIST)
    if (!areaPanelOpen) {
      params.set('area_panel', 'closed')
    }
  }
  addYears(params, years)
  const text = params.toString().replace(/%2C/gi, ',')
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
// one; null unless given). With { back: n }, it first goes back n entries
// (as pressing Back n times) and then adds or replaces the entry there; a new
// entry drops the entries that were ahead of it, so Forward cannot return to
// them. newSearch null with back only goes back. The back and forward buttons
// update search too.
export function useLocationSearch() {
  const [search, setSearch] = useState(() => window.location.search)
  // The entry to add or replace once going back has finished, or null.
  const pending = useRef(null)

  // Adds or replaces the history entry for newSearch, with state, and shows it.
  const write = useCallback((newSearch, replace, state) => {
    const url = `${window.location.pathname}${newSearch === '?' ? '' : newSearch}`
    if (replace) {
      window.history.replaceState(state, '', url)
    } else {
      window.history.pushState(state, '', url)
    }
    setSearch(window.location.search)
  }, [])

  useEffect(() => {
    // Called when the address changes by going back or forward: the back
    // and forward buttons, or navigate's own going back, after which the
    // entry it asked for is written.
    function onPopState() {
      const move = pending.current
      pending.current = null
      if (move) {
        write(move.newSearch, move.replace, move.state)
      } else {
        setSearch(window.location.search)
      }
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [write])

  const navigate = useCallback((newSearch, { replace = false, state = null, back = 0 } = {}) => {
    if (back > 0) {
      // Going back is finished only when the browser says so (popstate).
      pending.current = newSearch === null ? null : { newSearch, replace, state }
      window.history.go(-back)
    } else if (newSearch !== null) {
      write(newSearch, replace, state)
    }
  }, [write])

  return [search, navigate]
}
