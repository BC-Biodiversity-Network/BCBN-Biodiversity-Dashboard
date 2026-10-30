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
    access: [{ value: 'open', count: 2 }, { value: 'restricted', count: 0 }],
    published: { years: [{ year: 2020, count: 1 }, { year: 2021, count: 1 }], no_date: 0 },
    location: [{ value: 'mappable', count: 1 }],
  },
}

// Opens the Filters panel.
function openFilters() {
  fireEvent.click(screen.getByRole('button', { name: /^Filters/ }))
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
    openFilters()
    fireEvent.click(screen.getByRole('checkbox', { name: /^BC Data Catalogue/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
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

  it('lists the catalogues with datasets in the Filters panel by name, with no counts', async () => {
    const answer = structuredClone(LIST)
    answer.options.catalogue = [
      { value: 'zenodo.org', name: 'Zenodo', count: 9 },
      { value: 'catalogue.data.gov.bc.ca', name: 'BC Data Catalogue', count: 1 },
      { value: 'canwin', name: 'Canadian Watershed Information Network (CanWIN)', count: 5 },
      { value: 'datadryad.org', name: 'Dryad', count: 0 },
    ]
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => answer })))
    await showPage('?view=datasets')
    expect(screen.queryByText('Source catalogue')).toBeNull()
    openFilters()
    const panel = screen.getByRole('form', { name: 'Filters' })
    const catalogues = [...panel.querySelector('.datasets-filter-options').querySelectorAll('label')]
    expect(catalogues.map((label) => label.textContent)).toEqual(
      ['BC Data Catalogue', 'Canadian Watershed Information Network (CanWIN)', 'Zenodo'])
    // No counts anywhere in the panel; the result count stays outside it.
    expect(panel.querySelector('.datasets-count')).toBeNull()
    expect([...panel.querySelectorAll('label')].map((l) => l.textContent).join(' ')).not.toMatch(/\d/)
    expect(screen.getByText('2 datasets')).toBeTruthy()
    expect(screen.getByText(/These datasets were published from 2020 to 2021/)).toBeTruthy()
  })

  it('offers relevance only with a search, and keeps the default sort out of the address', async () => {
    stubList()
    let navigate = await showPage('?view=datasets')
    const sort = screen.getByRole('combobox', { name: 'Sort by' })
    expect([...sort.options].map((o) => o.textContent)).toEqual(['Newest first', 'Oldest first', 'Title A-Z'])
    expect(sort.value).toBe('newest')
    fireEvent.change(sort, { target: { value: 'title' } })
    expect(navigate).toHaveBeenLastCalledWith('?view=datasets&sort=title')
    cleanup()
    navigate = await showPage('?view=datasets&q=eagle&sort=oldest&page=3')
    const searched = screen.getByRole('combobox', { name: 'Sort by' })
    expect([...searched.options].map((o) => o.value)).toEqual(['relevance', 'newest', 'oldest', 'title'])
    fireEvent.change(searched, { target: { value: 'relevance' } })
    expect(navigate).toHaveBeenLastCalledWith('?view=datasets&q=eagle')
  })

  it('counts the filters in use and clears them all, keeping the search and sort', async () => {
    stubList()
    const navigate = await showPage('?view=datasets&q=eagle&sort=title&catalogue=a&catalogue=b&organism=salmon' +
      '&mappable=1')
    expect(screen.getByRole('button', { name: 'Filters (3)' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(navigate).toHaveBeenLastCalledWith('?view=datasets&q=eagle&sort=title')
  })

  it('keeps every change in the panel pending, then applies them all at once and closes', async () => {
    const urls = stubList()
    const navigate = await showPage('?view=datasets&sort=title&page=2')
    openFilters()
    expect(screen.getByText(/When the record was published, not when the data were collected/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'Apply' })).toHaveLength(1)

    fireEvent.click(screen.getByRole('checkbox', { name: /^BC Data Catalogue/ }))
    fireEvent.change(screen.getByRole('textbox', { name: 'From' }), { target: { value: '2010' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Organism name' }), { target: { value: 'salmon' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Open access' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Only datasets that can be shown on the map' }))
    // Nothing is applied or fetched yet; the counts and the button still show the applied filters.
    expect(navigate).not.toHaveBeenCalled()
    expect(urls).toHaveLength(1)
    expect(screen.getByRole('checkbox', { name: 'Open access' }).checked).toBe(true)
    expect(screen.getByRole('button', { name: 'Filters' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenLastCalledWith('?view=datasets&sort=title&catalogue=catalogue.data.gov.bc.ca' +
      '&year_from=2010&organism=salmon&access=open&mappable=1')
    expect(screen.queryByRole('form', { name: 'Filters' })).toBeNull()
  })

  it.each([
    ['Cancel', () => fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))],
    ['the Filters button', () => fireEvent.click(screen.getByRole('button', { name: /^Filters/ }))],
    ['Escape', () => fireEvent.keyDown(document, { key: 'Escape' })],
  ])('closing the panel with %s drops the pending changes', async (way, closePanel) => {
    stubList()
    const navigate = await showPage('?view=datasets&access=restricted')
    openFilters()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Open access' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Organism name' }), { target: { value: 'salmon' } })
    act(closePanel)
    expect(screen.queryByRole('form', { name: 'Filters' })).toBeNull()
    expect(navigate).not.toHaveBeenCalled()
    openFilters()
    expect(screen.getByRole('checkbox', { name: 'Open access' }).checked).toBe(false)
    expect(screen.getByRole('checkbox', { name: 'Restricted access' }).checked).toBe(true)
    expect(screen.getByRole('textbox', { name: 'Organism name' }).value).toBe('')
  })

  it('applies everything when Enter is pressed in the Published or Organisms box', async () => {
    stubList()
    const navigate = await showPage('?view=datasets')
    openFilters()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Open access' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'to' }), { target: { value: '2020' } })
    fireEvent.submit(screen.getByRole('textbox', { name: 'to' }))
    expect(navigate).toHaveBeenLastCalledWith('?view=datasets&year_to=2020&access=open')
    cleanup()
    const again = await showPage('?view=datasets')
    openFilters()
    fireEvent.change(screen.getByRole('textbox', { name: 'Organism name' }), { target: { value: 'Oncorhynchus' } })
    fireEvent.submit(screen.getByRole('textbox', { name: 'Organism name' }))
    expect(again).toHaveBeenLastCalledWith('?view=datasets&organism=Oncorhynchus')
  })

  it('keeps the panel open and applies nothing while a year or organism is wrong', async () => {
    stubList()
    const navigate = await showPage('?view=datasets')
    openFilters()
    fireEvent.change(screen.getByRole('textbox', { name: 'From' }), { target: { value: '2021' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'to' }), { target: { value: '2015' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Organism name' }), { target: { value: 'e' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(screen.getByText('The first year must not be after the second.')).toBeTruthy()
    expect(screen.getByText('Type at least 2 letters or digits.')).toBeTruthy()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('closes without a new address when nothing changed', async () => {
    stubList()
    const navigate = await showPage('?view=datasets&access=open')
    openFilters()
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(navigate).not.toHaveBeenCalled()
    expect(screen.queryByRole('form', { name: 'Filters' })).toBeNull()
  })

  it('clears the applied filters at once with "Clear all", closing the panel', async () => {
    stubList()
    const navigate = await showPage('?view=datasets&access=open&mappable=1')
    openFilters()
    fireEvent.click(screen.getByRole('checkbox', { name: /^BC Data Catalogue/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(navigate).toHaveBeenLastCalledWith('?view=datasets')
    expect(screen.queryByRole('form', { name: 'Filters' })).toBeNull()
  })

  it('shows the organism names of each result while the organism filter is in use', async () => {
    stubList()
    LIST.results[0].organisms = ['bald eagle', 'Haliaeetus leucocephalus']
    await showPage('?view=datasets&organism=haliaeetus')
    expect(screen.getByText('Organisms: bald eagle, Haliaeetus leucocephalus')).toBeTruthy()
    delete LIST.results[0].organisms
  })

  it('shows the species filter as a chip naming the species, which removes it', async () => {
    const urls = stubList()
    LIST.filters = { species: { specieskey: '49JFH', requested_key: '49JFH', merged_from: null,
      col_name: 'Oncorhynchus nerka', english_name: 'Sockeye Salmon' } }
    const navigate = await showPage('?view=datasets&species=49JFH&q=lake')
    delete LIST.filters
    expect(urls[0]).toContain('species=49JFH')
    const chip = screen.getByText(/^Species:/)
    expect(chip.textContent).toBe('Species: Sockeye Salmon (Oncorhynchus nerka)')
    expect(chip.querySelector('i').textContent).toBe('Oncorhynchus nerka')
    // The species is not one of the Filters panel's filters.
    expect(screen.getByRole('button', { name: 'Filters' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Remove the species filter (Sockeye Salmon (Oncorhynchus nerka))' }))
    expect(navigate).toHaveBeenLastCalledWith('?view=datasets&q=lake')
  })

  it('shows the key until the answer names the species, and "Clear all" clears the species too', async () => {
    stubList()
    const navigate = await showPage('?view=datasets&species=4LVR5')
    expect(screen.getByText(/^Species:/).textContent).toBe('Species: 4LVR5')
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(navigate).toHaveBeenLastCalledWith('?view=datasets')
  })

  it('says the Organisms filter matches names as written, and where the linked species are', async () => {
    stubList()
    await showPage('?view=datasets')
    openFilters()
    expect(screen.getByText('Matches the names as written in each record. To see the datasets linked to a species, ' +
      'open its card on the species map.')).toBeTruthy()
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
