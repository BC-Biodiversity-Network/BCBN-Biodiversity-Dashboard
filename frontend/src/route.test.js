import { describe, expect, it } from 'vitest'

import {
  NO_FILTERS, activeFilterCount, datasetsSearch, readDatasetsState, readSpeciesKey, readView, shownSort,
  CELL_LIST, NO_LIST, PANEL_FROM_CLICK, clearAreaMove, isPlainMap, keptListChoices, mapSearch, rankKey, readCell,
  readList, readMapYears, readRank, sameAddress, speciesDatasetsSearch, speciesMapSearch, withAreaHistory, withYears,
} from './route'

// The state of a plain Datasets page.
const PLAIN = {
  q: '', sort: '', catalogues: [], yearFrom: null, yearTo: null, organism: '', access: [], mappable: false,
  species: '', wider: false, page: 1, dataset: null,
}

describe('the page state in the address', () => {
  it('opens the species map unless the address asks for the datasets', () => {
    expect(readView('')).toBe('species')
    expect(readView('?view=datasets&q=eagle')).toBe('datasets')
    expect(readView('?view=other')).toBe('species')
  })

  it('reads the defaults from a plain address', () => {
    expect(readDatasetsState('?view=datasets')).toEqual(PLAIN)
  })

  it('writes and reads back every value, with repeated filters and an id with a slash', () => {
    const state = {
      q: 'bald eagle', sort: 'oldest', catalogues: ['catalogue.data.gov.bc.ca', 'datadryad.org'],
      yearFrom: 2010, yearTo: 2020, organism: 'Haliaeetus', access: ['open', 'restricted'], mappable: true,
      species: '49JFH', wider: true, page: 3, dataset: 'oai:datadryad.org:doi:10.5061/dryad.x',
    }
    const search = datasetsSearch(state)
    expect(search).toContain('catalogue=catalogue.data.gov.bc.ca&catalogue=datadryad.org')
    expect(readDatasetsState(search)).toEqual(state)
  })

  it('leaves defaults out of the address and ignores an odd page', () => {
    expect(datasetsSearch({ ...PLAIN, q: ' ', organism: ' ' })).toBe('?view=datasets')
    expect(readDatasetsState('?view=datasets&page=-2').page).toBe(1)
    expect(readDatasetsState('?view=datasets&sort=random&year_from=20x&year_to=12').sort).toBe('')
    expect(readDatasetsState('?view=datasets&year_from=20x&year_to=12')).toMatchObject({ yearFrom: null, yearTo: null })
    // The page has no class filter, so an old link's class is ignored.
    expect(readDatasetsState('?view=datasets&class=bc_only')).not.toHaveProperty('classes')
  })

  it('shows relevance only with a search, and newest by default without one', () => {
    expect(shownSort(PLAIN)).toBe('newest')
    expect(shownSort({ ...PLAIN, q: 'eagle' })).toBe('relevance')
    expect(shownSort({ ...PLAIN, sort: 'relevance' })).toBe('newest')
    expect(shownSort({ ...PLAIN, q: 'eagle', sort: 'title' })).toBe('title')
  })

  it('links a species to its datasets and to its card', () => {
    expect(speciesDatasetsSearch('49JFH')).toBe('?view=datasets&species=49JFH')
    expect(readDatasetsState(speciesDatasetsSearch('49JFH'))).toEqual({ ...PLAIN, species: '49JFH' })
    expect(speciesMapSearch('49JFH')).toBe('?species=49JFH')
    expect(readSpeciesKey(speciesMapSearch('49JFH'))).toBe('49JFH')
    expect(readView(speciesMapSearch('49JFH'))).toBe('species')
    expect(speciesMapSearch(null)).toBe('?')
    expect(readSpeciesKey('')).toBeNull()
  })

  it('puts the open hexagon in the address, with or without a species', () => {
    expect(speciesMapSearch(null, '872800b30ffffff')).toBe('?cell=872800b30ffffff')
    expect(speciesMapSearch('59PMB', '872800b30ffffff')).toBe('?species=59PMB&cell=872800b30ffffff')
    expect(readCell('?species=59PMB&cell=872800b30ffffff')).toBe('872800b30ffffff')
    expect(readSpeciesKey('?species=59PMB&cell=872800b30ffffff')).toBe('59PMB')
    expect(readView('?cell=872800b30ffffff')).toBe('species')
    expect(readCell('?cell=872800B30FFFFFF')).toBe('872800b30ffffff')
    for (const bad of ['', '?cell=', '?cell=abc', '?cell=882800b30ffffff', '?cell=832800bffffffff']) {
      expect(readCell(bad)).toBeNull()
    }
  })

  it('puts a rank in the address as its full path, a missing rank left out', () => {
    const turdus = readRank('?rank=genus&kingdom=Animalia&phylum=Chordata&class=Aves&order=Passeriformes'
      + '&family=Turdidae&genus=Turdus')
    expect(turdus).toEqual({ rank: 'genus', name: 'Turdus', path: [
      { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Chordata' }, { rank: 'class', name: 'Aves' },
      { rank: 'order', name: 'Passeriformes' }, { rank: 'family', name: 'Turdidae' }, { rank: 'genus', name: 'Turdus' },
    ] })
    expect(mapSearch({ rank: turdus })).toBe('?rank=genus&kingdom=Animalia&phylum=Chordata&class=Aves'
      + '&order=Passeriformes&family=Turdidae&genus=Turdus')
    expect(readRank(mapSearch({ rank: turdus, cell: '872800b30ffffff' }))).toEqual(turdus)
    expect(readCell(mapSearch({ rank: turdus, cell: '872800b30ffffff' }))).toBe('872800b30ffffff')
    // A bee family GBIF files with no order: the order is left out, and read
    // back as missing.
    const apidae = readRank('?rank=family&kingdom=Animalia&phylum=Arthropoda&class=Insecta&family=Apidae')
    expect(apidae.path[3]).toEqual({ rank: 'order', name: null })
    expect(mapSearch({ rank: apidae })).toBe('?rank=family&kingdom=Animalia&phylum=Arthropoda&class=Insecta'
      + '&family=Apidae')
    // Ranks below the rank are ignored; a species wins over a rank.
    expect(readRank('?rank=kingdom&kingdom=Plantae&genus=Turdus')).toEqual({ rank: 'kingdom', name: 'Plantae',
      path: [{ rank: 'kingdom', name: 'Plantae' }] })
    expect(mapSearch({ species: '59PMB', rank: turdus })).toBe('?species=59PMB')
    for (const bad of ['', '?rank=genus', '?rank=species&genus=Turdus', '?rank=genus&genus=',
      '?rank=genus&genus=Turdus&species=59PMB']) {
      expect(readRank(bad)).toBeNull()
    }
  })

  it('tells two ranks of the same name apart by their path', () => {
    const plant = readRank('?rank=genus&kingdom=Plantae&phylum=Tracheophyta&class=Magnoliopsida&order=Lamiales'
      + '&family=Lamiaceae&genus=Prunella')
    const bird = readRank('?rank=genus&kingdom=Animalia&phylum=Chordata&class=Aves&order=Passeriformes'
      + '&family=Prunellidae&genus=Prunella')
    expect(rankKey(plant)).not.toBe(rankKey(bird))
    expect(rankKey(plant)).toBe(rankKey(readRank(mapSearch({ rank: plant, cell: '872800b30ffffff' }))))
    expect(rankKey(null)).toBeNull()
    // A family named like an order is not the order.
    const missing = readRank('?rank=family&kingdom=A&phylum=B&class=C&family=D')
    const named = readRank('?rank=family&kingdom=A&phylum=B&class=C&order=D')
    expect(named).toBeNull()
    expect(rankKey(missing)).not.toBe(rankKey(readRank('?rank=order&kingdom=A&phylum=B&class=C&order=D')))
  })

  it('keeps the options of a rank card\'s and a hexagon panel\'s species lists, the defaults left out', () => {
    const turdus = readRank('?rank=genus&kingdom=Animalia&genus=Turdus')
    const search = mapSearch({
      rank: turdus, rankList: { q: 'thrush', sort: 'english', status: ['red', 'none'], page: 2 },
      cell: '872800b30ffffff', cellList: { q: '', sort: 'most_records', status: ['blue'], page: 1 },
    })
    expect(search).toBe('?rank=genus&kingdom=Animalia&genus=Turdus&q=thrush&sort=english&status=red&status=none'
      + '&page=2&cell=872800b30ffffff&cell_status=blue')
    expect(readList(search)).toEqual({ q: 'thrush', sort: 'english', status: ['red', 'none'], page: 2 })
    expect(readList(search, CELL_LIST)).toEqual({ ...NO_LIST, status: ['blue'] })
    expect(readRank(search)).toEqual(turdus)
    expect(mapSearch({ rank: turdus, rankList: NO_LIST })).toBe('?rank=genus&kingdom=Animalia&genus=Turdus')
    // A list goes with its rank or its hexagon only.
    expect(mapSearch({ rankList: { ...NO_LIST, q: 'x' }, cellList: { ...NO_LIST, q: 'y' } })).toBe('?')
  })

  it('reads a list\'s options leniently: unknown values are the defaults, statuses in their usual order', () => {
    expect(readList('')).toEqual(NO_LIST)
    expect(readList('?sort=newest&status=purple&status=none&status=red&status=red&page=0&q=%20bee%20'))
      .toEqual({ q: 'bee', sort: 'most_records', status: ['red', 'none'], page: 1 })
    expect(readList('?cell_page=x&cell_sort=scientific', CELL_LIST)).toEqual({ ...NO_LIST, sort: 'scientific' })
  })

  it('carries a list\'s order and statuses to the next one, without its name filter or page', () => {
    expect(keptListChoices({ q: 'bee', sort: 'english', status: ['red'], page: 3 }))
      .toEqual({ q: '', sort: 'english', status: ['red'], page: 1 })
  })

  it('keeps the map\'s year range in the address, an open end left out', () => {
    const turdus = readRank('?rank=genus&kingdom=Animalia&genus=Turdus')
    const search = mapSearch({ rank: turdus, cell: '872800b30ffffff', years: { from: 2000, to: 2020 } })
    expect(search).toBe('?rank=genus&kingdom=Animalia&genus=Turdus&cell=872800b30ffffff&year_from=2000&year_to=2020')
    expect(readMapYears(search)).toEqual({ from: 2000, to: 2020 })
    expect(readRank(search)).toEqual(turdus)
    expect(mapSearch({ years: { from: null, to: 1999 } })).toBe('?year_to=1999')
    expect(readMapYears('?year_to=1999')).toEqual({ from: null, to: 1999 })
    expect(readMapYears('')).toBeNull()
    expect(readMapYears('?year_from=abc&year_to=')).toBeNull()
    // A year range alone is still the plain map.
    expect(isPlainMap('?year_from=2000')).toBe(true)
  })

  it('changes the year range of an address, keeping everything else', () => {
    const search = '?rank=genus&kingdom=Animalia&genus=Turdus&q=thrush&year_from=1990'
    expect(withYears(search, { from: 2000, to: 2020 })).toBe(
      '?rank=genus&kingdom=Animalia&genus=Turdus&q=thrush&year_from=2000&year_to=2020')
    expect(withYears(search, null)).toBe('?rank=genus&kingdom=Animalia&genus=Turdus&q=thrush')
    expect(withYears('?year_from=1990', null)).toBe('?')
  })

  it('knows the plain map: no species, rank or hexagon', () => {
    expect(isPlainMap('')).toBe(true)
    expect(isPlainMap('?')).toBe(true)
    expect(isPlainMap('?species=59PMB')).toBe(false)
    expect(isPlainMap('?cell=872800b30ffffff')).toBe(false)
    expect(isPlainMap('?rank=kingdom&kingdom=Plantae')).toBe(false)
  })

  it('counts each filter in use once, and clears them all', () => {
    expect(activeFilterCount(PLAIN)).toBe(0)
    // The species filter is a chip, not part of the Filters panel.
    expect(activeFilterCount({ ...PLAIN, species: '49JFH' })).toBe(0)
    const busy = { ...PLAIN, catalogues: ['a', 'b'], yearTo: 2020, organism: 'salmon', access: ['open'], mappable: true }
    expect(activeFilterCount(busy)).toBe(5)
    expect(activeFilterCount({ ...busy, ...NO_FILTERS })).toBe(0)
  })
})

describe("an area's place in the history", () => {
  const AREA = '?area=-120.6,50.55,-120.1,50.8&area_res=6'
  const CELL = '872800b30ffffff'

  it('compares addresses whatever the order of their parameters', () => {
    expect(sameAddress('', '?')).toBe(true)
    expect(sameAddress('?year_to=2020&year_from=2000', '?year_from=2000&year_to=2020')).toBe(true)
    expect(sameAddress('?cell=a', '')).toBe(false)
  })

  it('starts at the first entry with an area and counts the entries after it', () => {
    const drawn = withAreaHistory(null, { target: AREA, replace: false, currentSearch: '', currentState: null })
    expect(drawn).toEqual({ areaDepth: 0, areaBefore: '' })
    const clicked = withAreaHistory(PANEL_FROM_CLICK,
      { target: `?cell=${CELL}&${AREA.slice(1)}`, replace: false, currentSearch: AREA, currentState: drawn })
    expect(clicked).toEqual({ panelFromClick: true, areaDepth: 1, areaBefore: '' })
    // A replaced entry keeps its place, even when no state is asked for.
    expect(withAreaHistory(null, { target: AREA, replace: true, currentSearch: AREA, currentState: clicked }))
      .toEqual({ areaDepth: 1, areaBefore: '' })
    // An area that came with the page has nothing before it.
    expect(withAreaHistory(null, { target: AREA, replace: false, currentSearch: AREA, currentState: null }))
      .toEqual({ areaDepth: 1 })
    // An address without an area has no place, and keeps its own marks.
    expect(withAreaHistory(clicked, { target: `?cell=${CELL}`, replace: true, currentSearch: AREA, currentState: clicked }))
      .toEqual(PANEL_FROM_CLICK)
    expect(withAreaHistory({ areaDepth: 2 }, { target: '?', replace: true, currentSearch: AREA, currentState: null }))
      .toBeNull()
  })

  it('clears an area by going back past every entry that holds it', () => {
    // Drawn, then cleared straight away: only back.
    expect(clearAreaMove('?', { areaDepth: 0, areaBefore: '' }))
      .toEqual({ back: 1, search: null, replace: false, state: null })
    // Drawn, a hexagon clicked over it, then cleared: back to before the
    // area, then the hexagon as a click's entry after it.
    expect(clearAreaMove(`?cell=${CELL}`, { panelFromClick: true, areaDepth: 1, areaBefore: '' }))
      .toEqual({ back: 2, search: `?cell=${CELL}`, replace: false, state: PANEL_FROM_CLICK })
    // The same, when the entry before the area showed another hexagon: the
    // x then closes the panel in place.
    expect(clearAreaMove(`?cell=${CELL}`, { panelFromClick: true, areaDepth: 1, areaBefore: '?cell=8728c9b40ffffff' }))
      .toEqual({ back: 2, search: `?cell=${CELL}`, replace: false, state: null })
    // A rank opened over the area: its entry remembers where it came from.
    expect(clearAreaMove('?rank=kingdom&kingdom=Plantae', { rankOpenedFrom: AREA, areaDepth: 1, areaBefore: '' }))
      .toEqual({ back: 2, search: '?rank=kingdom&kingdom=Plantae', replace: false, state: { rankOpenedFrom: '' } })
    // A shared area: in place on its own entry, keeping the entry's marks;
    // else back to that entry, replacing it.
    expect(clearAreaMove(`?cell=${CELL}`, { panelFromClick: true, areaDepth: 0 }))
      .toEqual({ back: 0, search: `?cell=${CELL}`, replace: true, state: PANEL_FROM_CLICK })
    expect(clearAreaMove(`?cell=${CELL}`, null)).toEqual({ back: 0, search: `?cell=${CELL}`, replace: true, state: null })
    expect(clearAreaMove(`?cell=${CELL}`, { panelFromClick: true, areaDepth: 1 }))
      .toEqual({ back: 1, search: `?cell=${CELL}`, replace: true, state: null })
  })
})
