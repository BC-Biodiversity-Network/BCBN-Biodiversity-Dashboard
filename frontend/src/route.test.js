import { describe, expect, it } from 'vitest'

import { NO_FILTERS, activeFilterCount, datasetsSearch, readDatasetsState, readView, shownSort } from './route'

// The state of a plain Datasets page.
const PLAIN = {
  q: '', sort: '', catalogues: [], yearFrom: null, yearTo: null, organism: '', access: [], mappable: false,
  wider: false, page: 1, dataset: null,
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
      wider: true, page: 3, dataset: 'oai:datadryad.org:doi:10.5061/dryad.x',
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

  it('counts each filter in use once, and clears them all', () => {
    expect(activeFilterCount(PLAIN)).toBe(0)
    const busy = { ...PLAIN, catalogues: ['a', 'b'], yearTo: 2020, organism: 'salmon', access: ['open'], mappable: true }
    expect(activeFilterCount(busy)).toBe(5)
    expect(activeFilterCount({ ...busy, ...NO_FILTERS })).toBe(0)
  })
})
