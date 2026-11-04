import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import RankCard, { SHOWN_CHILDREN, rankTotalsText } from './RankCard'

// The family Turdidae, as App gives it.
const TURDIDAE = { rank: 'family', name: 'Turdidae', path: [
  { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Chordata' }, { rank: 'class', name: 'Aves' },
  { rank: 'order', name: 'Passeriformes' }, { rank: 'family', name: 'Turdidae' },
] }
const TURDIDAE_URL = '/api/ranks/family?kingdom=Animalia&phylum=Chordata&class=Aves&order=Passeriformes'
  + '&family=Turdidae'

// A pretend answer from /api/ranks/family for Turdidae, with fields changed
// as given.
function answer(fields = {}) {
  return {
    rank: 'family', name: 'Turdidae', path: TURDIDAE.path, species_count: 3, record_count: 965600,
    records_no_year: 18, first_year: 1861, last_year: 2026, children_rank: 'genus',
    children: [
      { name: 'Turdus', species_count: 2, record_count: 965000 },
      { name: 'Catharus', species_count: 1, record_count: 590 },
      { name: null, species_count: 1, record_count: 10 },
    ],
    offset: 0, limit: 50,
    members: [
      { specieskey: '59PMB', col_name: 'Turdus migratorius', name_source: 'Catalogue of Life, via GBIF',
        english_name: 'American Robin', statuses: ['Yellow'], record_count: 964801 },
      { specieskey: 'HHHH', col_name: 'Catharus ustulatus', name_source: 'Catalogue of Life, via GBIF',
        english_name: "Swainson's Thrush", statuses: [], record_count: 590 },
    ],
    classification_source: 'GBIF', name_source: 'Catalogue of Life, via GBIF',
    english_name_source: 'BC Species and Ecosystems Explorer (BCSEE)', sensitive_max_res: null,
    ...fields,
  }
}

// A pretend fetch that answers with answerFor(url) (status 200, or the
// status it gives) and records the URLs.
function stubRank(answerFor) {
  const fetchMock = vi.fn(async (url) => {
    const { status = 200, body } = answerFor(url)
    return { ok: status < 300, status, json: async () => body }
  })
  vi.stubGlobal('fetch', fetchMock)
  return () => fetchMock.mock.calls.map(([url]) => url)
}

// The card for rankRef with these props.
function show(props = {}) {
  return render(<RankCard rankRef={TURDIDAE} onClose={vi.fn()} onOpenSpecies={vi.fn()} onOpenRank={vi.fn()}
    {...props} />)
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the rank card', () => {
  it('names the rank and the ranks above it, with its species, records and years', async () => {
    const urls = stubRank(() => ({ body: answer() }))
    const { container } = show()
    expect(container.querySelector('h2').textContent).toBe('Family Turdidae')
    expect(container.querySelector('.card-taxonomy').textContent).toBe('Animalia › Chordata › Aves › Passeriformes')
    await screen.findByText('3 species in BC, 965,600 records, 1861 to 2026.')
    expect(screen.getByText('18 of these records have no date.')).toBeTruthy()
    expect(urls()).toEqual([`${TURDIDAE_URL}&offset=0&limit=50`])
  })

  it('links each rank above it to that rank, and shows a rank missing in GBIF as such, not as a link', async () => {
    stubRank(() => ({ body: answer() }))
    const bee = { rank: 'family', name: 'Apidae', path: [
      { rank: 'kingdom', name: 'Animalia' }, { rank: 'phylum', name: 'Arthropoda' }, { rank: 'class', name: 'Insecta' },
      { rank: 'order', name: null }, { rank: 'family', name: 'Apidae' },
    ] }
    const onOpenRank = vi.fn()
    const { container } = show({ rankRef: bee, onOpenRank })
    const line = container.querySelector('.card-taxonomy')
    expect(line.textContent).toBe('Animalia › Arthropoda › Insecta › no order')
    expect(line.querySelector('.rank-missing').textContent).toBe('no order')
    expect([...line.querySelectorAll('a')].map((a) => a.textContent)).toEqual(['Animalia', 'Arthropoda', 'Insecta'])
    const insecta = screen.getByRole('link', { name: 'Class Insecta' })
    expect(insecta.getAttribute('href')).toBe('?rank=class&kingdom=Animalia&phylum=Arthropoda&class=Insecta')
    fireEvent.click(insecta)
    expect(onOpenRank).toHaveBeenCalledWith({ rank: 'class', name: 'Insecta', path: bee.path.slice(0, 3) })
  })

  it('has no classification line for a kingdom', async () => {
    stubRank(() => ({ body: answer() }))
    const { container } = show({ rankRef: { rank: 'kingdom', name: 'Animalia', path: TURDIDAE.path.slice(0, 1) } })
    expect(container.querySelector('.card-taxonomy')).toBeNull()
  })

  it('lists the next rank down, each opening that rank in place of following its link', async () => {
    stubRank(() => ({ body: answer() }))
    const onOpenRank = vi.fn()
    const { container } = show({ onOpenRank })
    await screen.findByText('Genera (2)')
    const rows = [...container.querySelectorAll('.rank-children li')]
    expect(rows.map((row) => row.textContent)).toEqual([
      'Turdus 2 species, 965,000 records', 'Catharus 1 species, 590 records',
      'No genus in GBIF: 1 species, 10 records',
    ])
    const link = rows[0].querySelector('a')
    expect(link.querySelector('i').textContent).toBe('Turdus')
    expect(link.getAttribute('href')).toBe('?rank=genus&kingdom=Animalia&phylum=Chordata&class=Aves'
      + '&order=Passeriformes&family=Turdidae&genus=Turdus')
    expect(rows[2].querySelector('a')).toBeNull()
    fireEvent.click(link)
    expect(onOpenRank).toHaveBeenCalledWith({ rank: 'genus', name: 'Turdus',
      path: [...TURDIDAE.path, { rank: 'genus', name: 'Turdus' }] })
  })

  it(`folds a long list of the next rank down after ${SHOWN_CHILDREN}`, async () => {
    const children = Array.from({ length: 12 }, (_, index) => ({
      name: `Genus${index}`, species_count: 1, record_count: 12 - index }))
    stubRank(() => ({ body: answer({ children }) }))
    const { container } = show()
    await screen.findByText('Genera (12)')
    expect(container.querySelectorAll('.rank-children li')).toHaveLength(SHOWN_CHILDREN)
    fireEvent.click(screen.getByRole('button', { name: 'Show all 12' }))
    expect(container.querySelectorAll('.rank-children li')).toHaveLength(12)
    fireEvent.click(screen.getByRole('button', { name: 'Show less' }))
    expect(container.querySelectorAll('.rank-children li')).toHaveLength(SHOWN_CHILDREN)
  })

  it('lists its species, most records first, each opening its card in place of following its link', async () => {
    stubRank(() => ({ body: answer() }))
    const onOpenSpecies = vi.fn()
    const { container } = show({ onOpenSpecies })
    await screen.findByText('Species (3)')
    const rows = [...container.querySelectorAll('.hexagon-row')]
    expect(rows.map((row) => row.textContent)).toEqual([
      'Turdus migratorius American RobinYellow964,801', "Catharus ustulatus Swainson's Thrush590"])
    expect(rows[0].querySelector('a').getAttribute('href')).toBe('?species=59PMB')
    fireEvent.click(screen.getByText('Catharus ustulatus'))
    expect(onOpenSpecies).toHaveBeenCalledWith('HHHH')
  })

  it('pages through a long list of species, 50 at a time', async () => {
    const urls = stubRank((url) => ({ body: answer({ species_count: 120, offset: url.includes('offset=50') ? 50 : 0 }) }))
    show()
    await screen.findByText('1 to 2 of 120')
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText('51 to 52 of 120')
    expect(urls()).toEqual([`${TURDIDAE_URL}&offset=0&limit=50`, `${TURDIDAE_URL}&offset=50&limit=50`])
  })

  it('says where the classification and the names come from', async () => {
    stubRank(() => ({ body: answer() }))
    const { container } = show()
    await screen.findByText('Species (3)')
    expect(container.querySelector('.hexagon-sources').textContent.replace(/\s+/g, ' ')).toBe(
      'The classification (kingdom to genus) comes from GBIF; the species names come from the Catalogue of Life. '
      + 'Records are counted after merging keys, as on the maps.')
  })

  it('has no next rank down for a genus', async () => {
    stubRank(() => ({ body: answer({ rank: 'genus', children_rank: null, children: null }) }))
    const { container } = show()
    await screen.findByText('Species (3)')
    expect(container.querySelector('.rank-children')).toBeNull()
  })

  it('says when no species in BC has the classification, and closes with the x', async () => {
    stubRank(() => ({ status: 404, body: { detail: 'No such family' } }))
    const onClose = vi.fn()
    show({ onClose })
    expect(await screen.findByText('No species in BC has this classification.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Close this rank and show all species' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('writes its totals in the singular, and one year once', () => {
    expect(rankTotalsText({ species_count: 1, record_count: 1, first_year: 2001, last_year: 2001 }))
      .toBe('1 species in BC, 1 record, 2001.')
    expect(rankTotalsText({ species_count: 2, record_count: 3, first_year: null, last_year: null }))
      .toBe('2 species in BC, 3 records.')
  })
})
