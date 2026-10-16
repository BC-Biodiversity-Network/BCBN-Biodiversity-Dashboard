import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import SiteFooter from './SiteFooter'

afterEach(cleanup)

describe('the page footer', () => {
  it('says where the data comes from and that BCBN does not set statuses', () => {
    render(<SiteFooter />)
    const text = screen.getByRole('contentinfo').textContent
    expect(text).toContain('Conservation statuses come from the BC Species and Ecosystems Explorer (BCSEE), ' +
      'maintained by the BC Conservation Data Centre.')
    expect(text).toContain('BCBN shows them as published and does not assess or set conservation statuses.')
    expect(text).toContain('Species names and authorship come from the Catalogue of Life, via GBIF.')
    expect(text).toContain('Occurrence records come from GBIF.org.')
    expect(text).toContain('These sources, and the photographers whose photos are shown, do not ' +
      'endorse BCBN or this site, and this site is not an official government source.')
  })

  it('carries the OGL-BC attribution statement, worded exactly, with a link to the licence', () => {
    render(<SiteFooter />)
    // Right after the BCSEE sentences, so it is clear which data it is about.
    expect(screen.getByRole('contentinfo').textContent).toContain(
      'does not assess or set conservation statuses. ' +
      'Contains information licensed under the Open Government Licence \u2013 British Columbia. Species names')
    expect(screen.getByRole('link', { name: 'Open Government Licence \u2013 British Columbia' }).href).toBe(
      'https://www2.gov.bc.ca/gov/content/data/policy-standards/data-policies/open-data/open-government-licence-bc')
  })

  it('links each source', () => {
    render(<SiteFooter />)
    expect(screen.getByRole('link', { name: 'BC Species and Ecosystems Explorer' }).href)
      .toBe('http://a100.gov.bc.ca/pub/eswp/')
    expect(screen.getByRole('link', { name: 'Catalogue of Life' }).href).toBe('https://www.catalogueoflife.org/')
    expect(screen.getByRole('link', { name: 'GBIF.org' }).href).toBe('https://www.gbif.org/')
  })

  it('credits the base map, with links, so the credit is always visible', () => {
    render(<SiteFooter />)
    expect(screen.getByRole('contentinfo').textContent).toContain(
      'Base map: OpenFreeMap, © OpenMapTiles, © OpenStreetMap contributors.')
    expect(screen.getByRole('link', { name: 'OpenFreeMap' }).href).toBe('https://openfreemap.org/')
    expect(screen.getByRole('link', { name: '© OpenMapTiles' }).href).toBe('https://openmaptiles.org/')
    expect(screen.getByRole('link', { name: '© OpenStreetMap contributors' }).href)
      .toBe('https://www.openstreetmap.org/copyright')
  })
})
