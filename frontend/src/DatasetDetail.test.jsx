import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import DatasetDetail from './DatasetDetail'
import { describeLocation } from './datasetText'

afterEach(cleanup)

// A dataset as /api/datasets/{id} gives it: the CanWIN Bald Eagles record,
// with its standard BC box, and made-up licences and copies.
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
    points: [],
    looked_up_places: [],
    other_copies: [{ id: 'oai:catalogue.data.gov.bc.ca:crims', source_name: 'BC Data Catalogue',
      link: 'https://catalogue.data.gov.bc.ca/dataset/crims' }],
    requested_copy: null,
    ...fields,
  }
}

// Renders the detail of a dataset the way the page does.
function show(dataset) {
  return render(<DatasetDetail dataset={dataset} location={describeLocation(dataset)} onBack={vi.fn()} />)
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

  it('says a standard BC box is not the real extent', () => {
    show(eagles())
    expect(screen.getByText('Extent: whole of BC (standard box used by Canadian Watershed Information ' +
      "Network (CanWIN), not the dataset's real extent). Not drawn.")).toBeTruthy()
    expect(screen.queryByText(/GeoNames/)).toBeNull()
  })

  it('credits GeoNames when places from our lookup are shown', () => {
    show(eagles({ boxes: [], looked_up_places: [
      { name: 'Haida Gwaii', kind: 'in_bc', role: 'study_location', latitude: 53, longitude: -132 }] }))
    expect(screen.getByText(/Places named in the record: Haida Gwaii \(located by BCBN using GeoNames\)/))
      .toBeTruthy()
    expect(screen.getByRole('link', { name: 'CC BY 4.0' }).href).toBe('https://creativecommons.org/licenses/by/4.0/')
  })

  it('lists the other copies with links, and says when a copy was asked for', () => {
    show(eagles({ requested_copy: 'oai:catalogue.data.gov.bc.ca:crims' }))
    expect(screen.getByRole('link', { name: 'BC Data Catalogue' }).href).toBe('https://catalogue.data.gov.bc.ca/dataset/crims')
    expect(screen.getByText(/You followed a link to the copy in BC Data Catalogue/)).toBeTruthy()
  })
})
