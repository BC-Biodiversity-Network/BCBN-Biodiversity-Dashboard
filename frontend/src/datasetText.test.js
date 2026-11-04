import { describe, expect, it } from 'vitest'

import {
  LICENCE_NAME_LENGTH, boxKind, capitalised, checkedAccessLabel, creatorsLabel, describeLocation, harvestedDegrees,
  identifierLabel, issuedLabel, licenceListLabel, noteText, plainText, readLicence, readLicences, shorten,
  shownCoordinates, shownDegrees,
} from './datasetText'

// Real rights entries from the 2026-10-20 harvest (long ones cut).
const STATCAN = {
  name: '<h2>Statistics Canada Open Licence</h2> <p>This licence is issued on behalf of Her Majesty the ' +
    'Queen in Right of Canada.</p>'.repeat(20),
  uri: null,
}
const PRINCE_GEORGE = {
  name: "<a href='",
  uri: "https://pgmapinfo.princegeorge.ca/opendata/CityofPrinceGeorge_Open_Government_License_Open_Data.pdf' " +
    "rel='nofollow ugc'>Open Government License – City of Prince George</a>",
}
const CC0_IMAGE = {
  name: '<p>\n    <img src="',
  uri: 'https://licensebuttons.net/p/zero/1.0/88x31.png" title="Creative Commons CC0 1.0" ' +
    'style="display:none" onload="this.style.display=\'inline\'" />\n    ' +
    '<a href="http://creativecommons.org/publicdomain/zero/1.0" target="_blank">CC0 1.0</a>\n    </p>',
}
const OPEN_ACCESS = { name: null, uri: 'info:eu-repo/semantics/openAccess' }

describe('labels', () => {
  it('writes the issued date in words', () => {
    expect(issuedLabel('2020-06-05')).toBe('5 June 2020')
    expect(issuedLabel('')).toBeNull()
  })

  it('shortens at a space', () => {
    expect(shorten('one two three', 9)).toBe('one two...')
    expect(shorten('short', 9)).toBe('short')
  })

  it('names up to three creators exactly as harvested, then counts the rest', () => {
    expect(creatorsLabel([])).toBeNull()
    expect(creatorsLabel(undefined)).toBeNull()
    expect(creatorsLabel(['SBOT Requests'])).toBe('SBOT Requests')
    expect(creatorsLabel(['Smith, Ana', ' Blackburn, Ian ', 'Smith, Ana'])).toBe('Smith, Ana;  Blackburn, Ian ; Smith, Ana')
    expect(creatorsLabel(['A', 'B', 'C', 'D'])).toBe('A; B; C; and 1 other')
    expect(creatorsLabel(['A', 'B', 'C', 'D', 'E', 'F'])).toBe('A; B; C; and 3 others')
  })

  it('writes the identifier as a DOI or as the landing page address', () => {
    expect(identifierLabel('https://doi.org/10.5683/SP3/GZQXVC', 'doi')).toBe('doi:10.5683/SP3/GZQXVC')
    expect(identifierLabel('https://catalogue.data.gov.bc.ca/dataset/crims', 'url'))
      .toBe('https://catalogue.data.gov.bc.ca/dataset/crims')
    expect(identifierLabel('', 'url')).toBeNull()
    expect(identifierLabel('javascript:alert(1)', 'url')).toBeNull()
  })

  it('reads HTML as plain text', () => {
    expect(plainText('<p>Fish &amp; <b>birds</b></p>\n  of BC')).toBe('Fish & birds of BC')
    expect(plainText('a < b and c > d')).toBe('a < b and c > d')
  })
})

describe('licences', () => {
  it('gives an access right in words', () => {
    expect(readLicence(OPEN_ACCESS)).toEqual({ kind: 'access', label: 'Open access' })
    expect(readLicence({ name: '', uri: 'info:eu-repo/semantics/restrictedAccess' }).label).toBe('Restricted access')
  })

  it('names a long HTML licence by its heading and keeps the full text, as plain text', () => {
    const licence = readLicence(STATCAN)
    expect(licence.name).toBe('Statistics Canada Open Licence')
    expect(licence.fullText).toMatch(/^Statistics Canada Open Licence This licence is issued/)
    expect(licence.fullText).not.toContain('<')
    expect(licence.uri).toBeNull()
  })

  it('shortens a long licence with no heading', () => {
    const licence = readLicence({ name: 'word '.repeat(60), uri: null })
    expect(licence.name.length).toBeLessThanOrEqual(LICENCE_NAME_LENGTH + 3)
    expect(licence.fullText.length).toBeGreaterThan(licence.name.length)
  })

  it('joins a link split between the name and the URI (Prince George and other city hubs)', () => {
    expect(readLicence(PRINCE_GEORGE)).toEqual({
      kind: 'licence',
      name: 'Open Government License – City of Prince George',
      fullText: null,
      uri: 'https://pgmapinfo.princegeorge.ca/opendata/CityofPrinceGeorge_Open_Government_License_Open_Data.pdf',
    })
    // Abbotsford: the name ends with whole opening tags, the URI holds the text and closing tags.
    const abbotsford = readLicence({
      name: "<p style='margin: 0cm'><span style='font-size: 11.5pt;'>",
      uri: 'https://opendata-abbotsford.hub.arcgis.com/pages/city-of-abbotsford-open-data-licence</span></p>',
    })
    expect([abbotsford.name, abbotsford.uri]).toEqual([
      'https://opendata-abbotsford.hub.arcgis.com/pages/city-of-abbotsford-open-data-licence',
      'https://opendata-abbotsford.hub.arcgis.com/pages/city-of-abbotsford-open-data-licence'])
    const cc0 = readLicence(CC0_IMAGE)
    expect([cc0.name, cc0.uri]).toEqual(['CC0 1.0', 'http://creativecommons.org/publicdomain/zero/1.0'])
  })

  it('names a licence given only as a URI, and links only web addresses', () => {
    expect(readLicence({ name: null, uri: 'https://spdx.org/licenses/CC0-1.0.html' }))
      .toEqual({ kind: 'licence', name: 'CC0-1.0', fullText: null, uri: 'https://spdx.org/licenses/CC0-1.0.html' })
    expect(readLicence({ name: 'Bad', uri: 'javascript:alert(1)' }).uri).toBeNull()
    expect(readLicence({ name: 'CC0 1.0', uri: null })).toEqual(
      { kind: 'licence', name: 'CC0 1.0', fullText: null, uri: null })
  })

  it('reads the placeholder "None" with no link as no licence, but not a link or other text', () => {
    expect(readLicence({ name: 'None', uri: null })).toEqual({ kind: 'none' })
    expect(readLicence({ name: 'None', uri: '' })).toEqual({ kind: 'none' })
    expect(readLicences([{ name: 'None', uri: null }, OPEN_ACCESS])).toEqual(
      { licences: [], accessRights: ['Open access'] })
    expect(licenceListLabel([{ name: 'None', uri: null }, OPEN_ACCESS])).toBe('No licence in the record')
    expect(readLicence({ name: 'None', uri: 'https://example.org/licence' }).kind).toBe('licence')
    expect(readLicence({ name: 'None of the above', uri: null }).kind).toBe('licence')
  })

  it('splits licences from access rights, and labels the list item', () => {
    const { licences, accessRights } = readLicences([STATCAN, OPEN_ACCESS])
    expect(licences.map((l) => l.name)).toEqual(['Statistics Canada Open Licence'])
    expect(accessRights).toEqual(['Open access'])
    expect(licenceListLabel([OPEN_ACCESS])).toBe('No licence in the record')
    expect(licenceListLabel([PRINCE_GEORGE, { name: 'CC0 1.0', uri: null }])).toBe(
      'Open Government License – City of Prince George and 1 more')
    expect(licenceListLabel([STATCAN])).toBe('Statistics Canada Open Licence')
    // No licence in the record: the one BCBN found at the original catalogue, else none.
    const checked = [{ name: 'Creative Commons Attribution 4.0 International', uri: 'https://example.org/by' }]
    expect(licenceListLabel([OPEN_ACCESS], checked)).toBe('Creative Commons Attribution 4.0 International')
    expect(licenceListLabel([STATCAN], checked)).toBe('Statistics Canada Open Licence')
    expect(licenceListLabel([OPEN_ACCESS], null)).toBe('No licence in the record')
    expect(licenceListLabel([{ name: 'The Government of Example Open Data Licence for Data Sets, Version 2', uri: null }]))
      .toBe('The Government of Example Open Data Licence for Data Sets...')
  })
})

describe('boxes', () => {
  it.each([
    [{ w: -124, e: -123, s: 49, n: 50 }, null],
    [{ w: -139.5, e: -113.5, s: 48, n: 60 }, 'matches_bc_rectangle'],
    [{ w: -139.06, e: -114.03, s: 60, n: 48.3 }, 'matches_bc_rectangle'],
    [{ w: -140.5, e: -113.5, s: 48, n: 60 }, null],
    [{ w: -141.003, e: -52.6174, s: 41.6755, n: 83.1139 }, 'matches_canada_extent'],
    [{ w: -140, e: -50, s: 42, n: 82 }, null],
    [{ w: -124, e: -123, s: 50, n: 49 }, 'swapped'],
    [{ w: 170, e: -170, s: 50, n: 60 }, null],
    [{ w: -123, e: -123, s: 49, n: 49 }, 'zero_width'],
    [{ w: -180, e: 180, s: -90, n: 90 }, 'most_of_world'],
  ])('box %o is %s', (box, kind) => {
    // The same cases as test_box_flags in the export's tests.
    expect(boxKind(box)).toBe(kind)
  })
})

// A dataset as the detail endpoint gives it, with no location.
function dataset(fields) {
  return { source_name: 'CanWIN', boxes: [], points: [], looked_up_places: [], ...fields }
}

// A looked-up place with coordinates, as the export gives it.
function place(name, kind, { role = 'study_location', large = false } = {}) {
  return { name, kind, role, latitude: 50, longitude: -120, large_area: large }
}

// The notes of a location as the page shows them, folded.
function texts(location) {
  return location.notes.map((note) => noteText(note))
}

describe('where the location comes from', () => {
  it('draws a real box and says it is from the record', () => {
    const location = describeLocation(dataset({ boxes: [{ w: -124, e: -123, s: 49, n: 50 }] }))
    expect(location.boxes).toHaveLength(1)
    expect(texts(location)).toEqual(['Extent: from the CanWIN record (west -124, east -123, south 49, north 50).'])
  })

  it('shows at most 4 decimals, keeping the number as harvested for the tooltip, and lists several boxes', () => {
    const location = describeLocation(dataset({ boxes: [
      { w: -128.61694335937497, e: -127.61718749999997, s: 51.3854950692232, n: 52.0322181041453 },
      { w: -150.0, e: -110.0, s: 35.0, n: 65.0 }, { w: -124, e: -123.5, s: 49.25, n: 50 }] }))
    expect(texts(location)).toEqual(['Extent: 3 boxes from the CanWIN record: (west -128.6169, ' +
      'east -127.6172, south 51.3855, north 52.0322), ' +
      '(west -150, east -110, south 35, north 65) and (west -124, east -123.5, south 49.25, north 50).'])
    expect(location.notes[0].items[0].w).toBe(-128.61694335937497)
    expect([shownDegrees(-128.61694335937497), harvestedDegrees(-128.61694335937497)])
      .toEqual(['-128.6169', '-128.61694335937497'])
    expect([shownDegrees(35.0), shownDegrees(-0.00001), shownDegrees(49.12346)]).toEqual(['35', '0', '49.1235'])
  })

  it('folds more than three boxes or points: the first three, then how many more', () => {
    const points = Array.from({ length: 27 }, (_, i) => ({ lat: 50 + i / 10, lon: -125 }))
    const location = describeLocation(dataset({ points }))
    expect(texts(location)).toEqual(['Location: 27 points from the record: (latitude 50, longitude -125), ' +
      '(latitude 50.1, longitude -125), (latitude 50.2, longitude -125) and 24 more points.'])
    expect(noteText(location.notes[0], true)).toMatch(/\(latitude 52\.5, longitude -125\) and \(latitude 52\.6, longitude -125\)\.$/)
    expect(shownCoordinates(location.notes[0]).foldable).toBe(true)
    const boxes = Array.from({ length: 4 }, (_, i) => ({ w: -124 + i, e: -123 + i, s: 49, n: 50 }))
    expect(texts(describeLocation(dataset({ boxes })))).toEqual(['Extent: 4 boxes from the CanWIN record: ' +
      '(west -124, east -123, south 49, north 50), (west -123, east -122, south 49, north 50), ' +
      '(west -122, east -121, south 49, north 50) and 1 more box.'])
    const three = describeLocation(dataset({ points: points.slice(0, 3) }))
    expect(shownCoordinates(three.notes[0]).foldable).toBe(false)
  })

  it('draws a box matching all of BC or all of Canada dashed, with how many records share it', () => {
    const bc = describeLocation(dataset({ boxes: [{ w: -139.5, e: -113.5, s: 48, n: 60 }], box_shared_by: [620] }))
    expect([bc.boxes, bc.matchingBoxes]).toEqual([[], [{ w: -139.5, e: -113.5, s: 48, n: 60 }]])
    expect(texts(bc)).toEqual(['Extent: whole of BC (west -139.5, east -113.5, south 48, north 60). The box ' +
      'covers all of BC; CanWIN uses this same box for ' +
      "620 of its records in Lunaris, so it may not show where this dataset's data actually are."])
    const canada = describeLocation(dataset({ boxes: [{ w: -141, e: -52.6, s: 41.7, n: 83.1 }], box_shared_by: [951] }))
    expect(texts(canada)).toEqual(['Extent: all of Canada (west -141, east -52.6, south 41.7, north 83.1). The box ' +
      'covers all of Canada; CanWIN uses this same ' +
      "box for 951 of its records in Lunaris, so it may not show where this dataset's data actually are."])
  })

  it('states only what the box covers when no other record shares it, and puts swapped edges back', () => {
    const location = describeLocation(dataset({ boxes: [{ w: -139.06, e: -114.03, s: 60, n: 48.3 }], box_shared_by: [1] }))
    // The numbers are the record's own, edges the wrong way round included.
    expect(texts(location)).toEqual(['Extent: whole of BC (west -139.06, east -114.03, south 60, north 48.3). ' +
      'The box covers all of BC.'])
    expect(location.matchingBoxes).toEqual([{ w: -139.06, e: -114.03, s: 48.3, n: 60 }])
  })

  it('draws a solid box and a dashed one side by side', () => {
    const location = describeLocation(dataset({
      boxes: [{ w: -124, e: -123, s: 49, n: 50 }, { w: -139.5, e: -113.5, s: 48, n: 60 }], box_shared_by: [1, 620] }))
    expect([location.boxes.length, location.matchingBoxes.length]).toEqual([1, 1])
    expect(texts(location)[0]).toBe('Extent: from the CanWIN record (west -124, east -123, south 49, north 50).')
  })

  it('writes an odd box as harvested and says why it is not drawn', () => {
    const location = describeLocation(dataset({ boxes: [{ w: -124, e: -123, s: 50, n: 49 }] }))
    expect(location.boxes).toEqual([])
    expect(texts(location)[0]).toBe('Box in the record: west -124, east -123, south 50, north 49. Not drawn, ' +
      'because its edges are the wrong way round (south above north, or west beyond east).')
  })

  it('draws points from the record', () => {
    const location = describeLocation(dataset({ points: [{ lat: 49, lon: -123 }, { lat: 50, lon: -124 }] }))
    expect(location.points).toHaveLength(2)
    expect(texts(location)).toEqual(['Location: 2 points from the record: (latitude 49, longitude -123) and ' +
      '(latitude 50, longitude -124).'])
    const one = describeLocation(dataset({ points: [{ lat: 51.65, lon: -128.1 }] }))
    expect(texts(one)).toEqual(['Location: a point from the record (latitude 51.65, longitude -128.1).'])
  })

  it('gives the record\'s own place list unchanged, whether or not it has a box or point', () => {
    const places = ['Vancouver; British Columbia; Canada', 'Lac du Bois Grasslands Protected Area', 'Global']
    const withBox = describeLocation(dataset({ boxes: [{ w: -124, e: -123, s: 49, n: 50 }], places }))
    expect(withBox.places).toEqual(places)
    expect(withBox.boxes).toHaveLength(1)
    const withPoint = describeLocation(dataset({ points: [{ lat: 49, lon: -123 }], places: ['Bamfield'] }))
    expect(withPoint.places).toEqual(['Bamfield'])
    expect(describeLocation(dataset({})).places).toEqual([])
    expect(describeLocation(dataset({ places: null })).places).toEqual([])
  })

  it('leaves out only empty entries of the place list', () => {
    expect(describeLocation(dataset({ places: ['', '  ', ' Kamloops ', 'BC'] })).places).toEqual([' Kamloops ', 'BC'])
  })

  it('never shows or draws place names from the lookup', () => {
    const location = describeLocation(dataset({
      looked_up_places: [place('Kamloops', 'in_bc'), place('Columbia Basin', 'outside_bc')] }))
    expect([location.boxes, location.matchingBoxes, location.points, location.places]).toEqual([[], [], [], []])
    expect(location.notes).toEqual(['No location given in the record.'])
    expect(JSON.stringify(location)).not.toMatch(/Kamloops|Columbia Basin|lookup/)
  })

  it('says there is no box or point when the record gives only a place list', () => {
    const location = describeLocation(dataset({ places: ['Kamloops; British Columbia; Canada'] }))
    expect(location.notes).toEqual(['No box or point given in the record.'])
    const withBox = describeLocation(dataset({ boxes: [{ w: -124, e: -123, s: 49, n: 50 }], places: ['Kamloops'] }))
    expect(texts(withBox).join(' ')).not.toMatch(/No box or point|No location/)
  })

  it('says when there is no location at all', () => {
    expect(describeLocation(dataset({})).notes).toEqual(['No location given in the record.'])
  })

})

describe('what BCBN checked at the source', () => {
  it('puts the checked access in plain words, with its date, or null when it was not checked', () => {
    expect(checkedAccessLabel({ access: 'not_downloadable' })).toBe('not downloadable')
    expect(checkedAccessLabel({ access: 'government_bceid_only' }))
      .toBe('downloadable by government and business BCeID users only')
    expect(checkedAccessLabel({ access: 'some_files_locked', access_until: '2027-02-01' }))
      .toBe('some files locked until 1 February 2027')
    expect(checkedAccessLabel({ access: 'embargoed', access_until: '2027-08-14' })).toBe('embargoed until 14 August 2027')
    expect(checkedAccessLabel({ access: null })).toBeNull()
    expect(checkedAccessLabel(null)).toBeNull()
  })

  it('capitalises the first letter only', () => {
    expect(capitalised('open to UBC users only')).toBe('Open to UBC users only')
    expect(capitalised('')).toBe('')
  })
})

