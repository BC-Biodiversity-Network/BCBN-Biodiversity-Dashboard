import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// The page with its map swapped for a stand-in that records what it is
// given (MapLibre cannot draw in the pretend browser page).
const seen = vi.hoisted(() => ({ map: null }))
vi.mock('./DatasetMap', () => ({
  // A stand-in map that only records its props.
  default: function DatasetMap(props) {
    seen.map = props
    return null
  },
}))

// Imported after the stand-in above, which vitest puts in place first.
import DatasetsPage from './DatasetsPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

// An answer of /api/datasets: one BC dataset and one that may include BC.
const LIST = {
  total: 2,
  pages: 1,
  results: [{
    id: 'oai:catalogue.data.gov.bc.ca:eagle-nests', title: 'Bald Eagle nest survey', issued: '2020-05-01',
    source_name: 'BC Data Catalogue', final_class: 'bc_only', frontend_group: 'bc_data',
    abstract_preview: 'Nest locations.',
    licences: [{ name: 'Open Government Licence - British Columbia', uri: null }],
  }, {
    id: 'oai:datadryad.org:doi:10.5061/dryad.golden', title: 'Golden eagle tracking worldwide',
    issued: '2021-11-30', source_name: 'Dryad', final_class: 'global', frontend_group: 'pending',
    abstract_preview: 'GPS tracks.', licences: [],
  }],
  options: {
    catalogue: [{ value: 'catalogue.data.gov.bc.ca', name: 'BC Data Catalogue', count: 1 },
      { value: 'datadryad.org', name: 'Dryad', count: 0 }],
    final_class: [{ value: 'bc_only', count: 1 }, { value: 'global', count: 0 }],
    frontend_group: [{ value: 'bc_data', count: 1 }, { value: 'pending', count: 2 }],
  },
}

// Answers every request with LIST, recording the addresses asked for.
function stubList() {
  const urls = []
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    urls.push(url)
    return { ok: true, status: 200, json: async () => LIST }
  }))
  return urls
}

// Renders the page for an address, with a navigate that only records.
async function showPage(search) {
  const navigate = vi.fn()
  render(<DatasetsPage search={search} navigate={navigate} />)
  await act(async () => {})
  return navigate
}

describe('the Datasets page', () => {
  it('lists the datasets with their count, date and licence, and no class', async () => {
    stubList()
    await showPage('?view=datasets&q=eagle')
    expect(screen.getByText('2 datasets')).toBeTruthy()
    expect(screen.getByText('BC Data Catalogue · Issued 1 May 2020 · ' +
      'Open Government Licence - British Columbia')).toBeTruthy()
    expect(screen.queryByText(/BC only|Global/)).toBeNull()
    expect(screen.queryByText('Class')).toBeNull()
    expect(screen.getByRole('searchbox').value).toBe('eagle')
    // Only catalogues with datasets are offered; the map shows nothing yet.
    expect(screen.queryByText('Dryad')).toBeNull()
    expect(seen.map.location).toBeNull()
  })

  it('asks for BC data only by default, and the switch adds the datasets that may include BC', async () => {
    const urls = stubList()
    const navigate = await showPage('?view=datasets&q=eagle')
    expect(urls[0]).toContain('frontend_group=bc_data')
    expect(urls[0]).not.toContain('pending')
    fireEvent.click(screen.getByLabelText('Also show datasets that may include BC (national and global)'))
    expect(navigate).toHaveBeenCalledWith('?view=datasets&q=eagle&wider=1')
  })

  it('puts a search, a filter and an opened dataset in the address', async () => {
    stubList()
    const navigate = await showPage('?view=datasets&page=2')
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'salmon' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(navigate).toHaveBeenLastCalledWith('?view=datasets&q=salmon')
    fireEvent.click(screen.getByRole('checkbox', { name: /^BC Data Catalogue/ }))
    expect(navigate).toHaveBeenLastCalledWith('?view=datasets&catalogue=catalogue.data.gov.bc.ca')
    fireEvent.click(screen.getByRole('link', { name: 'Bald Eagle nest survey' }))
    expect(navigate).toHaveBeenLastCalledWith(
      '?view=datasets&page=2&dataset=oai%3Acatalogue.data.gov.bc.ca%3Aeagle-nests')
  })

  it('tags only the datasets that may include BC', async () => {
    stubList()
    await showPage('?view=datasets&q=eagle&wider=1')
    const tags = screen.getAllByText('May include BC (national or global data)')
    expect(tags).toHaveLength(1)
    expect(tags[0].closest('li').textContent).toContain('Golden eagle tracking worldwide')
  })

  it('lists every catalogue with datasets, most first, with its name and count', async () => {
    stubList()
    await showPage('?view=datasets')
    expect(screen.getByText('Source catalogue (1)')).toBeTruthy()
    expect(screen.getByRole('checkbox', { name: 'BC Data Catalogue 1' })).toBeTruthy()
  })

  it('does not send a search that is too short', async () => {
    stubList()
    const navigate = await showPage('?view=datasets')
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'e' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(navigate).not.toHaveBeenCalled()
    expect(screen.getByText('Type at least 2 letters or digits.')).toBeTruthy()
  })

  it('says so when the API does not answer', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    await showPage('?view=datasets')
    expect(screen.getByText(/the site's API did not answer/)).toBeTruthy()
  })
})
