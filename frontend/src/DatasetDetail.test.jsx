import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import DatasetDetail from './DatasetDetail'
import { describeLocation } from './datasetText'

afterEach(cleanup)

// A dataset as /api/datasets/{id} gives it: the CanWIN Bald Eagles record,
// with its box matching BC's rectangle, and made-up licences and copies.
function eagles(fields = {}) {
  return {
    id: 'oai:canwin-datahub.ad.umanitoba.ca:1dc40efc',
    title: 'Bald Eagles - Coastal Resource Information Management System (CRIMS)',
    abstract: 'Distribution of Bald Eagle habitat in coastal British Columbia. '.repeat(20),
    subjects: ['Birds', 'Habitat'],
    issued: '2020-06-05',
    source_name: 'Canadian Watershed Information Network (CanWIN)',
    link: 'https://canwin-datahub.ad.umanitoba.ca/data/dataset/1dc40efc',
    licences: [
      { name: '<h2>Statistics Canada Open Licence</h2><p>Long terms.</p><img src="x" onerror="alert(1)">',
        uri: null },
      { name: null, uri: 'info:eu-repo/semantics/openAccess' },
    ],
    final_class: 'bc_only',
    frontend_group: 'bc_data',
    boxes: [{ w: -139.5, e: -113.5, s: 48, n: 60 }],
    box_shared_by: [620],
    points: [],
    looked_up_places: [],
    other_copies: [{ id: 'oai:catalogue.data.gov.bc.ca:crims', source_name: 'BC Data Catalogue',
      link: 'https://catalogue.data.gov.bc.ca/dataset/crims' }],
    requested_copy: null,
    ...fields,
  }
}

// Renders the detail of a dataset the way the page does.
function show(dataset, onOpenSpecies = vi.fn()) {
  return render(<DatasetDetail dataset={dataset} location={describeLocation(dataset)} onBack={vi.fn()}
    onOpenSpecies={onOpenSpecies} />)
}

// The paragraphs of the Location section, as text.
function locationTexts(container) {
  const section = [...container.querySelectorAll('.dataset-section')]
    .find((element) => element.querySelector('h3').textContent === 'Location')
  return [...section.querySelectorAll('p')].map((p) => p.textContent)
}

// The rows of the Details table, each as [label, value] text.
function detailRows(container) {
  return [...container.querySelectorAll('.dataset-details tr')].map((row) =>
    [row.querySelector('th').textContent, row.querySelector('td').textContent])
}

// The value of one row of the Details table, by its label (the label's own
// text, without its note), or undefined when the row is not shown.
function detailValue(container, label) {
  const row = [...container.querySelectorAll('.dataset-details tr')]
    .find((tr) => tr.querySelector('th').firstChild.textContent === label)
  return row?.querySelector('td')
}

// The linked species and other organisms of a dataset, as the API gives them.
const SPECIES = {
  species: [
    { specieskey: '5KDBR', col_name: 'Haliaeetus leucocephalus', english_name: 'Bald Eagle',
      names_as_written: [{ name: 'bald eagles', same_as_shown: false },
        { name: 'Haliaeetus leucocephalus', same_as_shown: true }] },
    { specieskey: '49JFH', col_name: 'Oncorhynchus nerka', english_name: 'Sockeye Salmon',
      names_as_written: [{ name: 'O. nerka', same_as_shown: false }, { name: 'sockeye', same_as_shown: false }] },
    { specieskey: '4LVR5', col_name: 'Populus trichocarpa', english_name: null,
      names_as_written: [{ name: 'Populus trichocarpa', same_as_shown: true }] },
  ],
  other_organisms: ['salmon', 'herring'],
}

describe('the dataset details', () => {
  it('has a button that opens the original site in a new tab', () => {
    show(eagles())
    const link = screen.getByRole('link', { name: 'View and download on Canadian Watershed Information Network (CanWIN)' })
    expect(link.href).toBe('https://canwin-datahub.ad.umanitoba.ca/data/dataset/1dc40efc')
    expect(link.target).toBe('_blank')
  })

  it('folds a long abstract behind "Show more"', () => {
    show(eagles())
    const button = screen.getByRole('button', { name: 'Show more' })
    fireEvent.click(button)
    expect(screen.getByRole('button', { name: 'Show less' })).toBeTruthy()
  })

  it('shows licences as plain text, never as HTML, with the full text behind a button', () => {
    const { container } = show(eagles())
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('h2').textContent).toMatch(/^Bald Eagles/)
    expect(screen.getByText('Statistics Canada Open Licence')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Show the full licence text' }))
    expect(screen.getByText('Statistics Canada Open Licence Long terms.')).toBeTruthy()
    expect(detailValue(container, 'Access').textContent).toBe('Open access')
  })

  it('shows no class, and tags a dataset that may include BC', () => {
    show(eagles())
    expect(screen.queryByText(/BC only/)).toBeNull()
    expect(screen.queryByText('May include BC (national or global data)')).toBeNull()
    cleanup()
    show(eagles({ final_class: 'all_canada', frontend_group: 'pending' }))
    expect(screen.getByText('May include BC (national or global data)')).toBeTruthy()
    expect(screen.queryByText(/All of Canada/)).toBeNull()
  })

  it('says when there is no licence', () => {
    const { container } = show(eagles({ licences: [{ name: null, uri: 'info:eu-repo/semantics/restrictedAccess' }] }))
    expect(detailValue(container, 'Licence').textContent).toBe('No licence in the record. Check the source page.')
    expect(detailValue(container, 'Access').textContent).toBe('Restricted access')
  })

  it('treats the placeholder "None" as no licence', () => {
    show(eagles({ licences: [{ name: 'None', uri: null }, { name: null, uri: 'info:eu-repo/semantics/openAccess' }] }))
    expect(screen.getByText('No licence in the record. Check the source page.')).toBeTruthy()
    expect(screen.queryByText('None')).toBeNull()
  })

  // What BCBN found at the original catalogue, as /api/datasets/{id} gives it.
  function checked(fields = {}) {
    return { catalogue_checked: 'BC Data Catalogue', checked_on: '2026-11-02', checked_how: 'source API',
      access: 'open', access_until: null, access_note: null, access_matches_record: true, licences: null,
      removed: false, ...fields }
  }
  const OPEN = { name: null, uri: 'info:eu-repo/semantics/openAccess' }
  const RESTRICTED = { name: null, uri: 'info:eu-repo/semantics/restrictedAccess' }

  it("shows the original catalogue's access when it differs, marked and explained in a tip", () => {
    const { container } = show(eagles({
      source_name: 'BC Data Catalogue',
      source_check: checked({ access: 'not_downloadable', access_matches_record: false }),
    }))
    const access = detailValue(container, 'Access')
    expect(access.textContent).toBe('Not downloadable (checked by BCBN)?')
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Where does this access come from?' }))
    expect(screen.getByRole('tooltip').textContent).toBe('From BC Data Catalogue, where this dataset is published '
      + '(checked by BCBN, as of 3 November 2026). Lunaris, which collects records from many catalogues, lists it as '
      + 'open access, so we follow the original catalogue.')
  })

  it('puts every checked access value in plain words, with its date', () => {
    const cases = [
      [{ access: 'government_only' }, 'Downloadable by government users only'],
      [{ access: 'ubc_only' }, 'Open to UBC users only'],
      [{ access: 'limited' }, 'Limited access'],
      [{ access: 'no_files' }, 'No files in the record'],
      [{ access: 'some_files_locked', access_until: '2027-02-01' }, 'Some files locked until 1 February 2027'],
    ]
    for (const [fields, words] of cases) {
      const { container } = show(eagles({ source_check: checked({ ...fields, access_matches_record: false }) }))
      expect(detailValue(container, 'Access').textContent).toBe(`${words} (checked by BCBN)?`)
      cleanup()
    }
    const { container } = show(eagles({
      licences: [RESTRICTED], source_check: checked({ catalogue_checked: 'Zenodo', access_matches_record: false }),
    }))
    expect(detailValue(container, 'Access').textContent).toBe('Open access (checked by BCBN)?')
    fireEvent.click(screen.getByRole('button', { name: 'Where does this access come from?' }))
    expect(screen.getByRole('tooltip').textContent).toContain('lists it as restricted access, so we follow')
  })

  it('shows the access as the record gives it when the check agrees, or there was none', () => {
    for (const sourceCheck of [checked(), checked({ access: 'not_downloadable', access_matches_record: null }),
      null, undefined]) {
      const { container } = show(eagles({ source_check: sourceCheck }))
      expect(detailValue(container, 'Access').textContent).toBe('Open access')
      expect(container.textContent).not.toContain('checked by BCBN')
      cleanup()
    }
  })

  it("adds the original catalogue's licence when the record has none, linked, marked and explained", () => {
    const { container } = show(eagles({
      source_name: 'Zenodo', licences: [RESTRICTED],
      source_check: checked({ catalogue_checked: 'Zenodo', access: 'restricted',
        licences: [{ name: 'cc-by-4.0', uri: 'https://creativecommons.org/licenses/by/4.0/legalcode' }] }),
    }))
    expect(detailValue(container, 'Licence').textContent).toBe('cc-by-4.0 (checked by BCBN)?')
    expect(screen.getByRole('link', { name: 'cc-by-4.0' }).href)
      .toBe('https://creativecommons.org/licenses/by/4.0/legalcode')
    fireEvent.click(screen.getByRole('button', { name: 'Where does this licence come from?' }))
    expect(screen.getByRole('tooltip').textContent).toBe('From Zenodo, where this dataset is published (checked by '
      + 'BCBN, as of 3 November 2026). The record Lunaris collected from Zenodo did not include a licence.')
    expect(detailValue(container, 'Access').textContent).toBe('Restricted access')
  })

  it("keeps the record's own licence, and says when neither has one", () => {
    const check = checked({ licences: [{ name: 'Another licence', uri: null }] })
    const { container } = show(eagles({ source_check: check }))
    expect(detailValue(container, 'Licence').textContent)
      .toBe('Statistics Canada Open Licence Show the full licence text')
    expect(container.textContent).not.toContain('Another licence')
    expect(container.textContent).not.toContain('checked by BCBN')
    cleanup()
    const none = show(eagles({ licences: [OPEN], source_check: checked() })).container
    expect(detailValue(none, 'Licence').textContent).toBe('No licence in the record. Check the source page.')
  })

  it('notes under the access line that PANGAEA access is not checked yet, for PANGAEA datasets only', () => {
    const note = 'Note from BCBN: Lunaris marks every PANGAEA record as restricted access, which may not match '
      + 'PANGAEA. Check the source page.'
    const pangaea = { source: 'pangaea.de', licences: [RESTRICTED] }
    let { container } = show(eagles({ ...pangaea, source_check: checked({ access: null, removed: false }) }))
    const access = detailValue(container, 'Access')
    expect(access.querySelector('p').textContent).toBe('Restricted access')
    expect(access.querySelector('.dataset-attribution').textContent.replace(/\s+/g, ' ')).toBe(note)
    cleanup()
    container = show(eagles({ ...pangaea, source_check: null })).container
    expect(container.textContent).toContain('Lunaris marks every PANGAEA record')
    cleanup()
    // Once checked, or for another catalogue, no note.
    container = show(eagles({ ...pangaea, source_check: checked({ access: 'restricted' }) })).container
    expect(container.textContent).not.toContain('Lunaris marks every PANGAEA record')
    cleanup()
    container = show(eagles({ source: 'https-zenodo.org', licences: [RESTRICTED] })).container
    expect(container.textContent).not.toContain('Lunaris marks every PANGAEA record')
  })

  it("shows Lucia's PANGAEA hand check with its tip, and nothing for an agreeing Water Hub copy", () => {
    const { container } = show(eagles({
      source: 'pangaea.de', source_name: 'PANGAEA', licences: [RESTRICTED],
      source_check: checked({ catalogue_checked: 'PANGAEA', checked_on: '2026-11-03', checked_how: 'by hand',
        access_matches_record: false,
        licences: [{ name: 'Creative Commons Attribution 4.0 International (CC-BY-4.0)', uri: null }] }),
    }))
    expect(detailValue(container, 'Access').textContent).toBe('Open access (checked by BCBN)?')
    expect(detailValue(container, 'Licence').textContent)
      .toBe('Creative Commons Attribution 4.0 International (CC-BY-4.0) (checked by BCBN)?')
    expect(container.textContent).not.toContain('Lunaris marks every PANGAEA record')
    fireEvent.click(screen.getByRole('button', { name: 'Where does this access come from?' }))
    expect(screen.getByRole('tooltip').textContent).toBe('From PANGAEA, where this dataset is published (checked by '
      + 'BCBN, as of 3 November 2026). Lunaris, which collects records from many catalogues, lists it as restricted '
      + 'access, so we follow the original catalogue.')
    cleanup()
    const hub = show(eagles({ source_check: checked({ catalogue_checked: 'Columbia Basin Water Hub',
      checked_on: '2026-11-03', checked_how: 'by hand', licences: [{ name: 'CC-BY-SA-4.0', uri: null }] }) }))
    expect(detailValue(hub.container, 'Access').textContent).toBe('Open access')
    expect(hub.container.textContent).not.toContain('checked by BCBN')
  })

  it('says when the original catalogue no longer has the dataset, on the page, and no Zenodo note any more', () => {
    const note = 'Note from BCBN: as of 3 November 2026 this dataset was no longer on Zenodo, where it was '
      + 'published, so the link may not work.'
    const { container } = show(eagles({ source: 'https-zenodo.org', licences: [RESTRICTED],
      source_check: checked({ catalogue_checked: 'Zenodo', access: null, removed: true }) }))
    expect(container.querySelector('.dataset-removed-note').textContent.replace(/\s+/g, ' ')).toBe(note)
    expect(container.textContent).not.toContain('Zenodo and PANGAEA')
    expect(detailValue(container, 'Access').textContent).toBe('Restricted access')
    cleanup()
    expect(show(eagles({ source_check: checked() })).container.querySelector('.dataset-removed-note')).toBeNull()
  })

  it('puts the page in order: button, abstract, Details, Location, Species, Also listed in', () => {
    const { container } = show(eagles({ subjects: ['Birds'] }))
    const parts = [...container.querySelector('.dataset-detail').children].map((element) =>
      element.querySelector('h3')?.textContent ?? element.className)
    expect(parts).toEqual(['text-button dataset-back', '', 'dataset-meta', 'dataset-link-button', 'dataset-abstract',
      'Details', 'Location', 'Species', 'Also listed in'])
    // Subjects and the licence are only in the table, not shown twice.
    expect(screen.getAllByText('Birds')).toHaveLength(1)
    expect(screen.getAllByText('Statistics Canada Open Licence')).toHaveLength(1)
  })

  it('shows the Details rows in order, each left out when empty', () => {
    const { container } = show(eagles({
      creators: ['SBOT Requests', 'Blackburn, Ian'], publisher: 'Ministry of Forests', issued: '2020-06-05',
      link: 'https://doi.org/10.5683/SP3/GZQXVC', link_type: 'doi', subjects: ['Birds', 'Habitat'],
    }))
    expect(detailRows(container).map(([label]) => label)).toEqual(['Creators (as listed in the record)',
      'Publisher', 'Published', 'Identifier', 'Subjects', 'Licence', 'Access'])
    expect(detailValue(container, 'Creators').textContent).toBe('SBOT Requests; Blackburn, Ian')
    expect(detailValue(container, 'Publisher').textContent).toBe('Ministry of Forests')
    expect(detailValue(container, 'Published').textContent).toBe('5 June 2020')
    const identifier = screen.getByRole('link', { name: 'doi:10.5683/SP3/GZQXVC' })
    expect([identifier.href, identifier.target]).toEqual(['https://doi.org/10.5683/SP3/GZQXVC', '_blank'])
    expect([...detailValue(container, 'Subjects').querySelectorAll('li')].map((li) => li.textContent))
      .toEqual(['Birds', 'Habitat'])
    cleanup()
    const bare = show(eagles({ creators: [], publisher: null, issued: '', link: '', subjects: [], licences: [] }))
    expect(detailRows(bare.container)).toEqual([['Licence', 'No licence in the record. Check the source page.']])
  })

  it('names three creators exactly as harvested, then counts the others', () => {
    const { container } = show(eagles({ creators: ['Ma, Li', 'SBOT Requests', 'Ma, Li', 'D', 'E'] }))
    expect(detailValue(container, 'Creators').textContent).toBe('Ma, Li; SBOT Requests; Ma, Li; and 2 others')
    cleanup()
    const four = show(eagles({ creators: ['A', 'B', 'C', 'D'] }))
    expect(detailValue(four.container, 'Creators').textContent).toBe('A; B; C; and 1 other')
  })

  it('leaves out the publisher when it is the catalogue, ignoring case', () => {
    const { container } = show(eagles({ publisher: 'canadian watershed information network (CanWIN)' }))
    expect(detailValue(container, 'Publisher')).toBeUndefined()
  })

  it('links a landing page as the identifier when there is no DOI', () => {
    show(eagles())
    const link = screen.getByRole('link', { name: 'https://canwin-datahub.ad.umanitoba.ca/data/dataset/1dc40efc' })
    expect(link.closest('tr').querySelector('th').textContent).toBe('Identifier')
  })

  it('says, as facts only, that a box covers all of BC and how many records share it', () => {
    const { container } = show(eagles())
    expect(locationTexts(container)).toEqual(['Extent: whole of BC (west -139.5, east -113.5, south 48, north 60). ' +
      'The box covers all of BC; Canadian Watershed Information Network (CanWIN) uses this same box for 620 of ' +
      "its records in Lunaris, so it may not show where this dataset's data actually are."])
    expect(screen.queryByText(/GeoNames/)).toBeNull()
  })

  it('rounds each box number to 4 decimals and puts the number as harvested in its tooltip', () => {
    const box = { w: -128.61694335937497, e: -127.61718749999997, s: 51.3854950692232, n: 52.0322181041453 }
    const { container } = show(eagles({ boxes: [box], box_shared_by: [1] }))
    expect(locationTexts(container)).toEqual(['Extent: from the Canadian Watershed Information Network (CanWIN) ' +
      'record (west -128.6169, east -127.6172, south 51.3855, north 52.0322).'])
    const numbers = [...container.querySelectorAll('.dataset-degrees')]
    expect(numbers.map((number) => [number.textContent, number.title])).toEqual([
      ['-128.6169', '-128.61694335937497'], ['-127.6172', '-127.61718749999997'],
      ['51.3855', '51.3854950692232'], ['52.0322', '52.0322181041453']])
  })

  it('shows the first three of many points, then all of them with "Show all" and three again with "Show less"', () => {
    const points = Array.from({ length: 27 }, (_, i) => ({ lat: 50 + i / 10, lon: -125.123456 }))
    const { container } = show(eagles({ boxes: [], points }))
    const note = () => locationTexts(container)[0]
    expect(note()).toBe('Location: 27 points from the record: (latitude 50, longitude -125.1235), (latitude 50.1, ' +
      'longitude -125.1235), (latitude 50.2, longitude -125.1235) and 24 more points. Show all')
    expect(container.querySelectorAll('.dataset-degrees')).toHaveLength(6)
    fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
    expect(container.querySelectorAll('.dataset-degrees')).toHaveLength(54)
    expect(note()).toMatch(/, \(latitude 52\.5, longitude -125\.1235\) and \(latitude 52\.6, longitude -125\.1235\)\. Show less$/)
    fireEvent.click(screen.getByRole('button', { name: 'Show less' }))
    expect(note()).toMatch(/and 24 more points\. Show all$/)
  })

  it('has no button for three points or fewer', () => {
    show(eagles({ boxes: [], points: [{ lat: 49, lon: -123 }, { lat: 50, lon: -124 }, { lat: 51, lon: -125 }] }))
    expect(screen.queryByRole('button', { name: 'Show all' })).toBeNull()
  })

  it('gives the date only in the Published row, not under the title', () => {
    const { container } = show(eagles())
    expect(container.querySelector('.dataset-meta').textContent).toBe('Canadian Watershed Information Network (CanWIN)')
    expect(detailValue(container, 'Published').textContent).toBe('5 June 2020')
    expect(container.textContent).not.toMatch(/Issued/)
  })

  it('lists the record\'s own places under "Places listed in the record", as given, after the location notes', () => {
    const { container } = show(eagles({ places: ['Vancouver; British Columbia; Canada', 'North America'] }))
    const section = [...container.querySelectorAll('.dataset-section')]
      .find((element) => element.querySelector('h3').textContent === 'Location')
    expect(section.querySelector('h4').textContent).toBe('Places listed in the record')
    expect([...section.querySelectorAll('.dataset-places li')].map((li) => li.textContent)).toEqual([
      'Vancouver; British Columbia; Canada', 'North America'])
    expect(locationTexts(container)[0]).toMatch(/^Extent: whole of BC/)
  })

  it('says only that there is no box or point when the record gives just a place list', () => {
    const { container } = show(eagles({ boxes: [], places: ['Kamloops; British Columbia; Canada'] }))
    expect(locationTexts(container)).toEqual(['No box or point given in the record.'])
    expect(container.querySelector('.dataset-places').textContent).toBe('Kamloops; British Columbia; Canada')
  })

  it('has no place list heading when the record has none, and never shows names from the lookup', () => {
    const { container } = show(eagles({ boxes: [], looked_up_places: [
      { name: 'Columbia Basin', kind: 'outside_bc', role: 'study_location', latitude: 41.8, longitude: -87.6,
        large_area: false }] }))
    expect(container.querySelector('h4')).toBeNull()
    expect(locationTexts(container)).toEqual(['No location given in the record.'])
    expect(container.textContent).not.toMatch(/Columbia Basin|lookup|GeoNames/)
  })

  it('folds a long place list after ten entries, with a button to show them all', () => {
    const places = Array.from({ length: 13 }, (_, i) => `Lake ${i + 1}`)
    const { container } = show(eagles({ places }))
    const items = () => [...container.querySelectorAll('.dataset-places li')].map((li) => li.textContent)
    expect(items()).toHaveLength(10)
    expect(locationTexts(container).at(-1)).toBe('and 3 more. Show all')
    fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
    expect(items()).toEqual(places)
    expect(screen.getByRole('button', { name: 'Show less' })).toBeTruthy()
  })

  it('lists the linked species between Location and Also listed in, each linked to its card', () => {
    const onOpenSpecies = vi.fn()
    const { container } = show(eagles(SPECIES), onOpenSpecies)
    const headings = [...container.querySelectorAll('h3')].map((h) => h.textContent)
    expect(headings).toEqual(['Details', 'Location', 'Species', 'Also listed in'])
    const items = [...container.querySelectorAll('.dataset-species li')].map((li) => li.textContent)
    expect(items).toEqual([
      'Bald Eagle (Haliaeetus leucocephalus) (written in the record as “bald eagles”)',
      'Sockeye Salmon (Oncorhynchus nerka) (written in the record as “O. nerka”, “sockeye”)',
      'Populus trichocarpa',
    ])
    const link = screen.getByRole('link', { name: /^Sockeye Salmon/ })
    expect(link.getAttribute('href')).toBe('?species=49JFH')
    fireEvent.click(link)
    expect(onOpenSpecies).toHaveBeenCalledWith('49JFH')
  })

  it('lists the organisms that are not linked as plain text, with the note on how links are made', () => {
    show(eagles(SPECIES))
    expect(screen.getByText('Other organisms named in the record: salmon, herring')).toBeTruthy()
    expect(screen.queryByRole('link', { name: 'salmon' })).toBeNull()
    expect(screen.getByText('Species links are matched by BCBN from the names in the record.')).toBeTruthy()
  })

  it('says when the record names no organisms, and still lists names that do not link', () => {
    show(eagles({ species: [], other_organisms: [] }))
    expect(screen.getByText('The record names no organisms.')).toBeTruthy()
    expect(screen.queryByText(/Species links are matched/)).toBeNull()
    cleanup()
    show(eagles({ species: [], other_organisms: ['fish'] }))
    expect(screen.getByText('Other organisms named in the record: fish')).toBeTruthy()
    expect(screen.queryByText('The record names no organisms.')).toBeNull()
  })

  it('lists the other copies with links, and says when a copy was asked for', () => {
    show(eagles({ requested_copy: 'oai:catalogue.data.gov.bc.ca:crims' }))
    expect(screen.getByRole('link', { name: 'BC Data Catalogue' }).href).toBe('https://catalogue.data.gov.bc.ca/dataset/crims')
    const note = screen.getByText(/You followed a link to the copy in BC Data Catalogue/)
    expect(note.textContent).toBe('You followed a link to the copy in BC Data Catalogue. This is another copy of '
      + `the same dataset, from ${eagles().source_name}.`)
    expect(note.textContent).not.toMatch(/most recent/)
  })
})
