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
    expect(screen.getByText('Access: Open access.')).toBeTruthy()
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
    show(eagles({ licences: [{ name: null, uri: 'info:eu-repo/semantics/restrictedAccess' }] }))
    expect(screen.getByText('No licence given in the record.')).toBeTruthy()
    expect(screen.getByText('Access: Restricted access.')).toBeTruthy()
  })

  it('says, as facts only, that a box covers all of BC and how many records share it', () => {
    show(eagles())
    expect(screen.getByText('Extent: whole of BC. The box covers all of BC; Canadian Watershed Information ' +
      'Network (CanWIN) uses this same box for 620 of its records in Lunaris, so it may not show where this ' +
      "dataset's data actually are.")).toBeTruthy()
    expect(screen.queryByText(/GeoNames/)).toBeNull()
  })

  it('credits GeoNames when places from our lookup are shown', () => {
    show(eagles({ boxes: [], looked_up_places: [
      { name: 'Haida Gwaii', kind: 'in_bc', role: 'study_location', latitude: 53, longitude: -132 }] }))
    expect(screen.getByText(/Places named in the record: Haida Gwaii \(located by BCBN using GeoNames\)/))
      .toBeTruthy()
    expect(screen.getByRole('link', { name: 'CC BY 4.0' }).href).toBe('https://creativecommons.org/licenses/by/4.0/')
  })

  it('lists the linked species between Location and Licence, each linked to its card', () => {
    const onOpenSpecies = vi.fn()
    const { container } = show(eagles(SPECIES), onOpenSpecies)
    const headings = [...container.querySelectorAll('h3')].map((h) => h.textContent)
    expect(headings.slice(headings.indexOf('Location'), headings.indexOf('Licence') + 1)).toEqual(
      ['Location', 'Species', 'Licence'])
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
    expect(screen.getByText(/You followed a link to the copy in BC Data Catalogue/)).toBeTruthy()
  })
})
