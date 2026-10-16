import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SpeciesCard, { entryScope, noDateSentence, scopeSummary } from './SpeciesCard'

const BCSEE = 'BC Species and Ecosystems Explorer (BCSEE)'
const SOURCE = { status_source: BCSEE, status_source_date: '2026-05-05', status_pulled_on: '2026-09-26' }

const META = {
  gbif_download: {
    download_key: '0009771-260928105237408', doi: '10.15468/dl.ge9ypx',
    doi_url: 'https://doi.org/10.15468/dl.ge9ypx', date: '2026-10-04T06:24:04Z',
  },
}

// One BCSEE entry as the API sends it. level is BCSEE's own level for it.
function entry(status, label, common, { level = 'Species', speciesLevel = level === 'Species' } = {}) {
  return {
    status, scientific_label: label, scientific_label_source: 'GBIF', common_label: common,
    classification_level: level, is_species_level: speciesLevel, is_gbif_synonym: false,
    element_codes: [`${label}-${status}`], ...SOURCE,
  }
}

// A BCSEE block as the API sends it.
function bcsee(state, entries, { separate = false } = {}) {
  return {
    state, ...SOURCE, has_separate_bcsee_species: separate, entries,
    statuses: entries.map((e) => e.status).filter((s) => ['Red', 'Blue', 'Yellow', 'Exotic'].includes(s)),
  }
}

// A species card in the shape the API sends, based on the robin; a test
// changes only the parts it is about.
function makeCard(overrides = {}) {
  return {
    specieskey: '59PMB',
    requested_key: '59PMB',
    merged_from: null,
    name: {
      col_name: 'Turdus migratorius', col_authorship: 'Linnaeus, 1766',
      scientificname: 'Turdus migratorius Linnaeus, 1766',
      name_source: 'Catalogue of Life, via GBIF', col_release: 'COL26.6 XR (ChecklistBank 315557)',
    },
    english_name: 'American Robin',
    english_name_source: BCSEE,
    taxonomy: { kingdom: 'Animalia', phylum: 'Chordata', class: 'Aves', order: 'Passeriformes',
                family: 'Turdidae', genus: 'Turdus' },
    records: { record_count: 964801, records_no_year: 18, first_year: 1861, last_year: 2026,
               n_datasets: 41, merged_keys: [], merged_record_count: 0 },
    extent: { min_lat: 48.265765, max_lat: 60.002457, min_lon: -137.8949, max_lon: -114.057976 },
    image: {
      url: 'https://inaturalist-open-data.s3.amazonaws.com/photos/736813177/medium.jpg',
      page_url: 'https://www.inaturalist.org/photos/736813177', creator: 'anidamk',
      rightsholder: 'anidamk', license_code: 'CC0_1_0', license_label: 'CC0 1.0',
      license_url: 'https://creativecommons.org/publicdomain/zero/1.0/', no_derivatives: false,
    },
    top_datasets: [
      { datasetkey: 'd-ebird', title: 'EOD – eBird Observation Dataset', records: 940502 },
      { datasetkey: 'd-inat', title: 'iNaturalist Research-grade Observations', records: 20119 },
    ],
    bcsee: bcsee('listed', [entry('Yellow', 'Turdus migratorius', 'American Robin')]),
    ...overrides,
  }
}

// Replaces fetch with one that answers every card request with this card.
function stubCard(card) {
  const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => card }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

// Shows the card for key and waits until it has loaded; returns the card
// element and the close function given to it.
async function showCard(card, { key = card.specieskey, meta = META } = {}) {
  stubCard(card)
  const onClose = vi.fn()
  render(<SpeciesCard requestedKey={key} meta={meta} onClose={onClose} />)
  await screen.findByRole('heading', { level: 2 })
  return { panel: screen.getByRole('complementary'), onClose }
}

// The BCSEE section and the Sources section of the card.
function bcseeSection() {
  return screen.getByRole('region', { name: 'BC conservation status' })
}
function sourcesText() {
  return screen.getByRole('region', { name: 'Sources' }).textContent
}

// The text of each BCSEE entry on the card, in order.
function entryTexts(panel) {
  return [...panel.querySelectorAll('.bcsee-entry')].map((li) => li.textContent)
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the top of the card', () => {
  it('shows the name in italics exactly as stored, and the authorship as stored', async () => {
    const { panel } = await showCard(makeCard({
      name: { ...makeCard().name, col_name: 'Culex (Culex) pipiens', col_authorship: 'Linnaeus, 1758' },
    }))
    const title = panel.querySelector('.card-title h2')
    expect(title.querySelector('.scientific-name').textContent).toBe('Culex (Culex) pipiens')
    expect(title.querySelector('.card-authorship').textContent).toBe(' Linnaeus, 1758')
  })

  it('puts the classification right under the name, then the common name from BCSEE', async () => {
    const { panel } = await showCard(makeCard())
    const header = panel.querySelector('.card-title')
    const parts = [...header.children].map((el) => el.className || el.tagName)
    expect(parts).toEqual(['H2', 'card-taxonomy', 'card-common-name'])
    expect(header.querySelector('.card-taxonomy').textContent)
      .toBe('Animalia › Chordata › Aves › Passeriformes › Turdidae › Turdus')
    expect(header.querySelector('.card-common-name').textContent).toBe('American RobinCommon name from BCSEE')
  })

  it('each classification name opens its rank on hover, with the keyboard and on a tap', async () => {
    await showCard(makeCard())
    const aves = screen.getByRole('button', { name: 'Class: Aves' })
    fireEvent.mouseEnter(aves.parentElement)
    expect(screen.getByRole('tooltip').textContent).toBe('Class')
    fireEvent.mouseLeave(aves.parentElement)
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Kingdom: Animalia' }))
    expect(screen.getByRole('tooltip').textContent).toBe('Kingdom')
    fireEvent.keyDown(screen.getByRole('button', { name: 'Kingdom: Animalia' }), { key: 'Escape' })
    act(() => screen.getByRole('button', { name: 'Genus: Turdus' }).focus())
    expect(screen.getByRole('tooltip').textContent).toBe('Genus')
  })

  it('says "common name", never "English name", and no source lines in the card body', async () => {
    const { panel } = await showCard(makeCard())
    expect(panel.textContent).not.toMatch(/English name/)
    expect(panel.textContent).not.toContain('BCBN does not set')
    expect(panel.querySelector('.card-title').textContent).not.toContain('Catalogue of Life')
    expect(bcseeSection().textContent).not.toContain('Source:')
  })

  it('the "?" explains the authorship on hover, with the keyboard and on a tap', async () => {
    await showCard(makeCard())
    const help = screen.getByRole('button', { name: 'What is the authorship?' })
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.mouseEnter(help.parentElement)
    expect(screen.getByRole('tooltip').textContent).toContain(
      'The text after the species name is the authorship: who first published the name')
    expect(screen.getByRole('tooltip').textContent).toContain('"L." is Linnaeus')
    fireEvent.mouseLeave(help.parentElement)
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.click(help)
    expect(screen.getByRole('tooltip')).toBeTruthy()
  })

  it('does not show the extent box coordinates', async () => {
    const { panel } = await showCard(makeCard())
    expect(panel.textContent).not.toMatch(/48\.26|60\.00|137\.89|114\.05/)
  })
})

describe('the photo', () => {
  it('shows the photo whole, with creator, licence link and a link to the original', async () => {
    const { panel } = await showCard(makeCard())
    const img = screen.getByRole('img', { name: 'Photo of Turdus migratorius' })
    expect(img.getAttribute('src')).toContain('/medium.jpg')
    expect(img.className).toContain('whole-image')
    expect(panel.querySelector('.card-credit').textContent).toBe('Photo: anidamk · CC0 1.0 · View original')
    expect(screen.getByRole('link', { name: 'CC0 1.0' }).href)
      .toBe('https://creativecommons.org/publicdomain/zero/1.0/')
    expect(screen.getByRole('link', { name: 'View original' }).href)
      .toBe('https://www.inaturalist.org/photos/736813177')
  })

  it('never crops an ND photo, and shows the copyright holder for a CC BY licence', async () => {
    await showCard(makeCard({ image: { ...makeCard().image, license_label: 'CC BY-ND 4.0',
      license_code: 'CC_BY_ND_4_0', license_url: 'https://creativecommons.org/licenses/by-nd/4.0/',
      no_derivatives: true, creator: 'Andrea Benville', rightsholder: 'Andrea Benville' } }))
    const img = screen.getByRole('img')
    expect(img.className).toContain('whole-image')
    expect(img.className).toContain('no-derivatives')
    expect(screen.getByRole('figure').textContent).toContain(
      'Photo: Andrea Benville · © Andrea Benville · CC BY-ND 4.0 · View original')
  })

  it('shows a short note, not a broken image, when there is no photo', async () => {
    const { panel } = await showCard(makeCard({ image: null }))
    expect(screen.queryByRole('img')).toBeNull()
    expect(panel.textContent).toContain('No fully credited photo available.')
  })

  it('replaces a photo that fails to load with a note and the link to the original', async () => {
    await showCard(makeCard())
    fireEvent.error(screen.getByRole('img'))
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByRole('link', { name: 'See the original' }).href)
      .toBe('https://www.inaturalist.org/photos/736813177')
  })
})

describe('what each BCSEE status applies to', () => {
  it('one whole-species entry: said once in the heading, nothing under the entry', async () => {
    const { panel } = await showCard(makeCard())
    expect(within(bcseeSection()).getByRole('heading').textContent)
      .toBe('BC conservation status (applies to the whole species)')
    expect(entryTexts(panel)).toEqual(['BCSEE: YellowTurdus migratorius (common name: American Robin)'])
    expect(panel.querySelector('.bcsee-scope')).toBeNull()
  })

  it('separate BCSEE species: one sentence at the top, nothing under each entry, no common name under the title', async () => {
    const card = makeCard({
      specieskey: '66H6W', english_name: null, english_name_source: null,
      name: { ...makeCard().name, col_name: 'Amsinckia menziesii', col_authorship: '(Lehm.) A.Nelson & J.F.Macbr.' },
      bcsee: bcsee('listed', [
        entry('Yellow', 'Amsinckia menziesii', 'small-flowered fiddleneck', { speciesLevel: false }),
        entry('Unknown', 'Amsinckia intermedia', 'common fiddleneck', { speciesLevel: false }),
      ], { separate: true }),
    })
    const { panel } = await showCard(card)
    expect(panel.querySelector('.card-common-name')).toBeNull()
    expect(within(bcseeSection()).getByRole('heading').textContent).toBe('BC conservation status')
    expect(bcseeSection().querySelector('.bcsee-summary').textContent).toBe(
      'This card follows the Catalogue of Life, which treats these as one species. ' +
      'BCSEE lists them as 2 separate species, each with its own status:')
    expect(entryTexts(panel)).toEqual([
      'BCSEE: YellowAmsinckia menziesii (common name: small-flowered fiddleneck)',
      'BCSEE: UnknownAmsinckia intermedia (common name: common fiddleneck)',
    ])
    expect(panel.querySelector('.bcsee-scope')).toBeNull()
  })

  it('mixed scopes: a short line under each entry', async () => {
    const card = makeCard({
      bcsee: bcsee('listed', [
        entry('Yellow', 'Aegolius acadicus', 'Northern Saw-whet Owl'),
        entry('Blue', 'Aegolius acadicus brooksi', 'Northern Saw-whet Owl, brooksi subspecies',
          { level: 'Subspecies' }),
      ]),
    })
    const { panel } = await showCard(card)
    expect(within(bcseeSection()).getByRole('heading').textContent).toBe('BC conservation status')
    const scopes = [...panel.querySelectorAll('.bcsee-scope')].map((el) => el.textContent)
    expect(scopes).toEqual([
      'Applies to the whole species.',
      'Applies to subspecies Aegolius acadicus brooksi only.',
    ])
  })

  it('only entries below the species: said once at the top, with their level', () => {
    const entries = [
      entry('Red', 'Acipenser transmontanus pop. 1', 'White Sturgeon (Upper Kootenay River Population)', { level: 'Population' }),
      entry('Red', 'Acipenser transmontanus pop. 2', 'White Sturgeon (Upper Columbia River Population)', { level: 'Population' }),
    ]
    expect(scopeSummary(entries, false)).toEqual({
      heading: 'BC conservation status',
      intro: 'BCSEE has no entry for the species as a whole, only for these populations:',
      perEntry: false,
    })
  })

  it('separate species and subspecies on one card: both counted at the top, a line under each', () => {
    const entries = [
      entry('Yellow', 'Arceuthobium laricis', 'larch dwarf mistletoe', { speciesLevel: false }),
      entry('Unknown', 'Arceuthobium campylopodum', 'western dwarf mistletoe', { speciesLevel: false }),
      entry('Blue', 'Arceuthobium tsugense ssp. mertensianae', '', { level: 'Subspecies' }),
      entry('Yellow', 'Arceuthobium tsugense ssp. contortae', '', { level: 'Subspecies' }),
    ]
    expect(scopeSummary(entries, true)).toEqual({
      heading: 'BC conservation status',
      intro: 'This card follows the Catalogue of Life, which treats these as one species. ' +
        'BCSEE lists them as 2 separate species and 2 subspecies, each with its own status:',
      perEntry: true,
    })
  })

  it('a separate BCSEE species and a subspecies on one card are told apart by BCSEE level', () => {
    expect(entryScope(entry('Yellow', 'Arceuthobium laricis', ''), true)).toBe('separate')
    expect(entryScope(entry('Blue', 'Arceuthobium tsugense ssp. mertensianae', '', { level: 'Subspecies' }), true))
      .toBe('part')
    // A card from an older file without the level falls back on is_species_level.
    const old = { ...entry('Yellow', 'Turdus migratorius', ''), classification_level: undefined }
    expect(entryScope(old, false)).toBe('whole')
  })

  it('lists entries without a status under a listed species, each with its own BCSEE value', async () => {
    const card = makeCard()
    card.bcsee.entries.push(entry('Not Reviewed', 'Turdus migratorius caurinus', '', { level: 'Subspecies' }))
    const { panel } = await showCard(card)
    const chips = [...panel.querySelectorAll('.status-chip')]
    expect(chips.map((chip) => chip.textContent)).toEqual(['BCSEE: Yellow', 'BCSEE: Not Reviewed'])
    expect(chips[1].className).toContain('status-none')
    expect(entryTexts(panel)[1]).toBe(
      'BCSEE: Not ReviewedTurdus migratorius caurinusApplies to subspecies Turdus migratorius caurinus only.')
  })
})

describe('the BCSEE cases', () => {
  it('in BCSEE without a status (Pacific herring): no extra sentence, the chip says it', async () => {
    const card = makeCard({
      specieskey: 'WCTM', english_name: 'Pacific Herring',
      bcsee: bcsee('in_bcsee_no_status', [entry('Not Reviewed', 'Clupea pallasii', 'Pacific Herring')]),
    })
    await showCard(card)
    const text = bcseeSection().textContent
    expect(text).not.toContain('with no conservation status')
    expect(text).not.toContain('Not in BCSEE')
    expect(text).toContain('BCSEE: Not Reviewed')
    expect(within(bcseeSection()).getByRole('heading').textContent)
      .toBe('BC conservation status (applies to the whole species)')
  })

  it('not in BCSEE says so, clearly different from no status', async () => {
    await showCard(makeCard({ english_name: null, english_name_source: null,
      bcsee: bcsee('not_in_bcsee', []) }))
    expect(bcseeSection().textContent).toBe(
      'BC conservation statusNot in BCSEE: it has no entry for this species.')
  })

  it('each chip opens BCSEE’s own definition on hover, with the keyboard and on a tap, and Escape closes it', async () => {
    await showCard(makeCard({ bcsee: bcsee('in_bcsee_no_status', [entry('Not Reviewed', 'Clupea pallasii', '')]) }))
    const chip = screen.getByRole('button', { name: 'BCSEE: Not Reviewed. What does this mean?' })
    fireEvent.mouseEnter(chip.parentElement)
    expect(screen.getByRole('tooltip').textContent).toBe(
      'Not Reviewed (BCSEE): Species and ecological communities that have not undergone a ' +
      'conservation status assessment.')
    expect(chip.getAttribute('aria-describedby')).toBe(screen.getByRole('tooltip').id)
    fireEvent.mouseLeave(chip.parentElement)
    expect(screen.queryByRole('tooltip')).toBeNull()
    // A tap (click) keeps it open; a second tap closes it.
    fireEvent.click(chip)
    expect(screen.getByRole('tooltip')).toBeTruthy()
    fireEvent.click(chip)
    expect(screen.queryByRole('tooltip')).toBeNull()
    // Reached with the keyboard (real focus, which counts as :focus-visible),
    // it opens; Escape closes it.
    act(() => chip.focus())
    expect(screen.getByRole('tooltip')).toBeTruthy()
    fireEvent.keyDown(chip, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('shows no definition text on the card until a chip is opened', async () => {
    const { panel } = await showCard(makeCard())
    expect(panel.textContent).not.toContain('apparently secure')
  })
})

describe('records and merged keys', () => {
  it('shows the records, years and datasets, and the no-date sentence on its own', async () => {
    const { panel } = await showCard(makeCard())
    const records = within(panel).getByRole('region', { name: 'Records' }).textContent
    expect(records).toContain('964,801 records, 1861 to 2026, from 41 datasets. 18 of these records have no date.')
    expect(records).toContain('EOD – eBird Observation Dataset940,502')
    expect(screen.getByRole('link', { name: 'EOD – eBird Observation Dataset' }).href)
      .toBe('https://www.gbif.org/dataset/d-ebird')
  })

  it('writes the no-date sentence for one, several and none', () => {
    expect(noDateSentence(1)).toBe('1 of these records has no date.')
    expect(noDateSentence(1234)).toBe('1,234 of these records have no date.')
    expect(noDateSentence(0)).toBeNull()
  })

  it('asks for the old key, says it was merged, and how many records came from merged keys', async () => {
    const card = makeCard({
      specieskey: '79DVG', requested_key: 'VM8MB', merged_from: 'VM8MB',
      records: { record_count: 714, records_no_year: 0, first_year: 1885, last_year: 2026,
                 n_datasets: 17, merged_keys: ['VM8MB'], merged_record_count: 116 },
    })
    const fetchMock = stubCard(card)
    render(<SpeciesCard requestedKey="VM8MB" meta={META} onClose={() => {}} />)
    await screen.findByRole('heading', { level: 2 })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/species/VM8MB')
    const panel = screen.getByRole('complementary')
    expect(panel.textContent).toContain('You searched for VM8MB, an old key that was merged into 79DVG')
    expect(panel.textContent).toContain('Includes 116 records filed under key VM8MB, merged into this species')
    expect(panel.textContent).not.toContain('no date')
  })
})

describe('the Sources section', () => {
  it('cites the names, classification, status, records and key, each the way its source asks', async () => {
    await showCard(makeCard({ specieskey: '7NFJR' }))
    const text = sourcesText()
    expect(text).toContain('Names and authorship: Catalogue of Life, via GBIF, COL26.6 XR (ChecklistBank 315557).')
    expect(text).toContain('Catalogue of Life (2026-06-19 XR). Catalogue of Life Foundation, Amsterdam, Netherlands.')
    expect(text).toContain('https://doi.org/10.48580/dgy8b')
    expect(text).toContain('Licence: CC BY 4.0.')
    expect(text).toContain("Classification: as on this species' GBIF occurrence records, which GBIF matches to the Catalogue of Life (COL26.6 XR (ChecklistBank 315557)).")
    expect(text).toContain('Conservation status: B.C. Conservation Data Centre. 2026. BC Species and Ecosystems Explorer. ' +
      'B.C. Government, Victoria B.C. Available: http://a100.gov.bc.ca/pub/eswp/ (provincial export of 2026-05-05, accessed 2026-09-26).')
    expect(text).toContain('Occurrence records: GBIF.org (4 October 2026) GBIF Occurrence Download https://doi.org/10.15468/dl.ge9ypx')
    expect(text).toContain('Species key: 7NFJR')
    expect(screen.getByRole('link', { name: 'https://doi.org/10.15468/dl.ge9ypx' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'CC BY 4.0' }).href).toBe('https://creativecommons.org/licenses/by/4.0/')
  })

  it('leaves out the Catalogue of Life citation for a release it does not know', async () => {
    await showCard(makeCard({ name: { ...makeCard().name, col_release: 'COL27.1 XR (ChecklistBank 999999)' } }))
    expect(sourcesText()).toContain('Names and authorship: Catalogue of Life, via GBIF, COL27.1 XR (ChecklistBank 999999).')
    expect(sourcesText()).not.toContain('dgy8b')
  })

  it('shows a placeholder citation while /api/meta has not arrived', async () => {
    await showCard(makeCard(), { meta: null })
    expect(sourcesText()).toContain('Occurrence records: GBIF.org (citation loading).')
  })
})

describe('loading, errors and closing', () => {
  it('says it is loading until the card arrives', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
    render(<SpeciesCard requestedKey="59PMB" meta={META} onClose={() => {}} />)
    expect(screen.getByText('Loading the species card...')).toBeTruthy()
  })

  it('says when a key has no card (404)', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404,
      json: async () => ({ detail: "No species with key 'ZZZZ' in the BC data" }) })))
    render(<SpeciesCard requestedKey="ZZZZ" meta={META} onClose={() => {}} />)
    expect(await screen.findByText('No species card for key ZZZZ: there are no records of it in BC.')).toBeTruthy()
  })

  it('says when the API cannot be reached', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    }))
    render(<SpeciesCard requestedKey="59PMB" meta={META} onClose={() => {}} />)
    expect(await screen.findByText('The species card is not available: the API could not be reached.')).toBeTruthy()
  })

  it('the close button returns to the overview', async () => {
    const { onClose } = await showCard(makeCard())
    fireEvent.click(screen.getByRole('button', { name: /Close the species card/ }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
