import { describe, expect, it } from 'vitest'

import { datasetsSearch, readDatasetsState, readView } from './route'

describe('the page state in the address', () => {
  it('opens the species map unless the address asks for the datasets', () => {
    expect(readView('')).toBe('species')
    expect(readView('?view=datasets&q=eagle')).toBe('datasets')
    expect(readView('?view=other')).toBe('species')
  })

  it('reads the defaults from a plain address', () => {
    expect(readDatasetsState('?view=datasets')).toEqual(
      { q: '', catalogues: [], wider: false, page: 1, dataset: null })
  })

  it('writes and reads back every value, with repeated filters and an id with a slash', () => {
    const state = {
      q: 'bald eagle', catalogues: ['catalogue.data.gov.bc.ca', 'datadryad.org'], wider: true, page: 3,
      dataset: 'oai:datadryad.org:doi:10.5061/dryad.x',
    }
    const search = datasetsSearch(state)
    expect(search).toContain('catalogue=catalogue.data.gov.bc.ca&catalogue=datadryad.org')
    expect(readDatasetsState(search)).toEqual(state)
  })

  it('leaves defaults out of the address and ignores an odd page', () => {
    expect(datasetsSearch({ q: ' ', catalogues: [], wider: false, page: 1, dataset: null }))
      .toBe('?view=datasets')
    expect(readDatasetsState('?view=datasets&page=-2').page).toBe(1)
    // The page has no class filter, so an old link's class is ignored.
    expect(readDatasetsState('?view=datasets&class=bc_only')).not.toHaveProperty('classes')
  })
})
