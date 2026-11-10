import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  cellExportUrl, datasetsExportUrl, fetchCell, fetchDatasets, fetchRank, rankExportUrl,
} from './api'
import CsvDownload from './CsvDownload'
import { NO_LIST, readDatasetsState } from './route'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

// The options that choose a page of a list, which a download never has.
const PAGING = ['page', 'page_size', 'offset', 'limit']

// The query of an address as [name, value] pairs, in order.
function queryOf(url) {
  return [...new URL(url, 'http://site.example').searchParams]
}

// The query of an address without its paging options.
function withoutPaging(url) {
  return queryOf(url).filter(([name]) => !PAGING.includes(name))
}

// A pretend fetch that answers {} and records the addresses asked for.
function recordUrls() {
  const urls = []
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    urls.push(url)
    return { ok: true, status: 200, json: async () => ({}) }
  }))
  return urls
}

// The phylum Chordata, and a bee family GBIF files with no order.
const CHORDATA = { rank: 'phylum', name: 'Chordata', path: [
  { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Chordata' },
] }
const APIDAE = { rank: 'family', name: 'Apidae', path: [
  { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Arthropoda' }, { rank: 'class', name: 'Insecta' },
  { rank: 'order', name: null }, { rank: 'family', name: 'Apidae' },
] }

describe('the download addresses', () => {
  it('give the three examples', () => {
    expect(datasetsExportUrl(readDatasetsState('?view=datasets&q=salmon')))
      .toBe('/api/export/datasets?q=salmon&frontend_group=bc_data')
    expect(rankExportUrl(CHORDATA, { years: { from: 2000, to: 2020 }, list: NO_LIST }))
      .toBe('/api/export/ranks/phylum?kingdom=Animalia&phylum=Chordata&year_from=2000&year_to=2020')
    expect(cellExportUrl('861298d4fffffff', { years: null, q: '', status: [], sort: 'most_records', rank: null }))
      .toBe('/api/export/hexes/861298d4fffffff')
  })

  it.each([
    '?view=datasets',
    '?view=datasets&q=salmon&page=3',
    '?view=datasets&q=eagle+nests&sort=title&catalogue=a&catalogue=b&year_from=2010&year_to=2020'
      + '&organism=Oncorhynchus&access=open&access=restricted&mappable=1&species=49JFH&wider=1&page=2',
    '?view=datasets&sort=relevance',
  ])('carry what the Datasets list asks for, without its page (%s)', async (search) => {
    const urls = recordUrls()
    const state = readDatasetsState(search)
    await fetchDatasets(state)
    const exportUrl = datasetsExportUrl(state)
    expect(exportUrl.split('?')[0]).toBe('/api/export/datasets')
    expect(queryOf(exportUrl)).toEqual(withoutPaging(urls[0]))
    expect(queryOf(exportUrl).some(([name]) => PAGING.includes(name))).toBe(false)
  })

  it.each([
    [CHORDATA, { years: null, list: NO_LIST, offset: 0 }],
    [CHORDATA, { years: { from: 2000, to: 2020 }, list: { q: 'robin', sort: 'english', status: ['red', 'none'], page: 3 },
      offset: 100 }],
    [APIDAE, { years: { from: null, to: 1990 }, list: { q: '', sort: 'scientific', status: [], page: 1 }, offset: 0 }],
  ])('carry what a rank card asks for, its path and list included, without its page', async (rank, options) => {
    const urls = recordUrls()
    await fetchRank(rank, { ...options, limit: 50 })
    const exportUrl = rankExportUrl(rank, options)
    expect(exportUrl.split('?')[0]).toBe(`/api/export/ranks/${rank.rank}`)
    expect(queryOf(exportUrl)).toEqual(withoutPaging(urls[0]))
    expect(queryOf(exportUrl).some(([name]) => PAGING.includes(name))).toBe(false)
  })

  it.each([
    [{}],
    [{ years: { from: 2000, to: null }, q: ' robin ', status: ['blue'], sort: 'fewest_records' }],
    [{ years: { from: 2010, to: 2020 }, rank: APIDAE, q: 'bombus', sort: 'english' }],
  ])('carry what a hexagon panel asks for, its rank included, without its page', async (options) => {
    const urls = recordUrls()
    await fetchCell('872800b30ffffff', { ...options, offset: 50, limit: 50 })
    const exportUrl = cellExportUrl('872800b30ffffff', { ...options, offset: 50, limit: 50 })
    expect(exportUrl.split('?')[0]).toBe('/api/export/hexes/872800b30ffffff')
    expect(queryOf(exportUrl)).toEqual(withoutPaging(urls[0]))
    expect(queryOf(exportUrl).some(([name]) => PAGING.includes(name))).toBe(false)
  })

  it('never send a species for a hexagon (the API refuses it)', () => {
    expect(cellExportUrl('872800b30ffffff', { species: '59PMB', q: 'robin' }))
      .toBe('/api/export/hexes/872800b30ffffff?q=robin')
  })
})

describe('the Download CSV button', () => {
  it('is a plain link to the address, on its own, with no note beside it', () => {
    const { container } = render(
      <CsvDownload href="/api/export/datasets?q=salmon" label="Download these datasets as a CSV file" />)
    const link = screen.getByRole('link', { name: 'Download these datasets as a CSV file' })
    expect(link.getAttribute('href')).toBe('/api/export/datasets?q=salmon')
    expect(link.hasAttribute('download')).toBe(true)
    expect(link.textContent).toBe('Download CSV')
    // The file's own 4th line says how to read it, so nothing is said here.
    expect(container.querySelector('.csv-download').textContent).toBe('Download CSV')
  })

  it('goes nowhere when disabled', () => {
    render(<CsvDownload href="/api/export/datasets" disabled />)
    const link = screen.getByRole('link', { name: 'Download this list as a CSV file' })
    expect(link.hasAttribute('href')).toBe(false)
    expect(link.getAttribute('aria-disabled')).toBe('true')
    expect(link.className).toContain('disabled')
  })
})
