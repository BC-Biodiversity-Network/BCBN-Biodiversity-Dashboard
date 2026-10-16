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
    expect(text).toContain('do not endorse BCBN or this site.')
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
