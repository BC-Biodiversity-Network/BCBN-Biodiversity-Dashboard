import { describe, expect, it } from 'vitest'

import {
  LICENCE_NAME_LENGTH, boxKind, describeLocation, issuedLabel, licenceListLabel, placeLabel,
  plainText, readLicence, readLicences, shorten,
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

  it('splits licences from access rights, and labels the list item', () => {
    const { licences, accessRights } = readLicences([STATCAN, OPEN_ACCESS])
    expect(licences.map((l) => l.name)).toEqual(['Statistics Canada Open Licence'])
    expect(accessRights).toEqual(['Open access'])
    expect(licenceListLabel([OPEN_ACCESS])).toBe('No licence given')
    expect(licenceListLabel([PRINCE_GEORGE, { name: 'CC0 1.0', uri: null }])).toBe(
      'Open Government License – City of Prince George and 1 more')
    expect(licenceListLabel([STATCAN])).toBe('Statistics Canada Open Licence')
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

describe('where the location comes from', () => {
  it('draws a real box and says it is from the record', () => {
    const location = describeLocation(dataset({ boxes: [{ w: -124, e: -123, s: 49, n: 50 }] }))
    expect(location.boxes).toHaveLength(1)
    expect(location.notes).toEqual(['Extent: from the CanWIN record.'])
  })

  it('draws a box matching all of BC or all of Canada dashed, with how many records share it', () => {
    const bc = describeLocation(dataset({ boxes: [{ w: -139.5, e: -113.5, s: 48, n: 60 }], box_shared_by: [620] }))
    expect([bc.boxes, bc.matchingBoxes]).toEqual([[], [{ w: -139.5, e: -113.5, s: 48, n: 60 }]])
    expect(bc.notes).toEqual(['Extent: whole of BC. The box covers all of BC; CanWIN uses this same box for ' +
      "620 of its records in Lunaris, so it may not show where this dataset's data actually are."])
    const canada = describeLocation(dataset({ boxes: [{ w: -141, e: -52.6, s: 41.7, n: 83.1 }], box_shared_by: [951] }))
    expect(canada.notes).toEqual(['Extent: all of Canada. The box covers all of Canada; CanWIN uses this same ' +
      "box for 951 of its records in Lunaris, so it may not show where this dataset's data actually are."])
  })

  it('states only what the box covers when no other record shares it, and puts swapped edges back', () => {
    const location = describeLocation(dataset({ boxes: [{ w: -139.06, e: -114.03, s: 60, n: 48.3 }], box_shared_by: [1] }))
    expect(location.notes).toEqual(['Extent: whole of BC. The box covers all of BC.'])
    expect(location.matchingBoxes).toEqual([{ w: -139.06, e: -114.03, s: 48.3, n: 60 }])
  })

  it('draws a solid box and a dashed one side by side', () => {
    const location = describeLocation(dataset({
      boxes: [{ w: -124, e: -123, s: 49, n: 50 }, { w: -139.5, e: -113.5, s: 48, n: 60 }], box_shared_by: [1, 620] }))
    expect([location.boxes.length, location.matchingBoxes.length]).toEqual([1, 1])
    expect(location.notes[0]).toBe('Extent: from the CanWIN record.')
  })

  it('writes an odd box as harvested and says why it is not drawn', () => {
    const location = describeLocation(dataset({ boxes: [{ w: -124, e: -123, s: 50, n: 49 }] }))
    expect(location.boxes).toEqual([])
    expect(location.notes[0]).toBe('Box in the record: west -124, east -123, south 50, north 49. Not drawn, ' +
      'because its edges are the wrong way round (south above north, or west beyond east).')
  })

  it('draws points from the record', () => {
    const location = describeLocation(dataset({ points: [{ lat: 49, lon: -123 }, { lat: 50, lon: -124 }] }))
    expect(location.points).toHaveLength(2)
    expect(location.notes).toEqual(['Location: 2 points from the record.'])
  })

  it('draws looked-up places, says BCBN located them and which are not in BC', () => {
    const location = describeLocation(dataset({
      looked_up_places: [place('Kamloops', 'in_bc'), place('Goose Island', 'outside_bc'),
        { name: 'Nowhere Creek', kind: null, role: 'study_location', latitude: null, longitude: null }],
    }))
    expect(location.places.map((p) => p.name)).toEqual(['Kamloops', 'Goose Island'])
    expect(location.notes).toEqual(['Places named in the record: Kamloops, Goose Island (not in BC), ' +
      'Nowhere Creek (could not be placed on the map) (located by BCBN using GeoNames).'])
    expect(location.geonames).toBe(true)
  })

  it('lists large areas in the text only, once each, and never names an author\'s institution', () => {
    const location = describeLocation(dataset({
      looked_up_places: [place('Vancouver Island', 'in_bc'), place('British Columbia', 'in_bc', { large: true }),
        place('North America', 'contains_bc', { large: true }), place('Canada', 'contains_bc', { large: true }),
        place('Canada', 'contains_bc', { large: true }),
        place('University of Manitoba', 'outside_bc', { role: 'affiliation' })],
    }))
    expect(location.places.map((p) => p.name)).toEqual(['Vancouver Island'])
    expect(location.notes).toEqual([
      'Places named in the record: Vancouver Island (located by BCBN using GeoNames).',
      'Other places named: British Columbia, North America, Canada (large areas, not drawn on the map).'])
  })

  it('says "Places named in the record" when every place is a large area', () => {
    const location = describeLocation(dataset({
      looked_up_places: [place('British Columbia', 'in_bc', { large: true })] }))
    expect([location.places, location.geonames]).toEqual([[], true])
    expect(location.notes).toEqual(
      ['Places named in the record: British Columbia (large areas, not drawn on the map).'])
  })

  it('treats a record whose only places are authors\' institutions as having no location', () => {
    const location = describeLocation(dataset({
      looked_up_places: [place('University of Victoria', 'in_bc', { role: 'affiliation' })] }))
    expect([location.places, location.notes, location.geonames]).toEqual(
      [[], ['No location given in the record.'], false])
  })

  it('uses looked-up places only when the record itself gives nothing to draw', () => {
    const withBox = describeLocation(dataset({
      boxes: [{ w: -124, e: -123, s: 49, n: 50 }], looked_up_places: [place('Kamloops', 'in_bc')] }))
    expect([withBox.places, withBox.geonames]).toEqual([[], false])
    const standardBox = describeLocation(dataset({
      boxes: [{ w: -139.5, e: -113.5, s: 48, n: 60 }], looked_up_places: [place('Kamloops', 'in_bc')] }))
    expect(standardBox.places).toHaveLength(1)
  })

  it('says when there is no location at all', () => {
    expect(describeLocation(dataset({})).notes).toEqual(['No location given in the record.'])
  })

  it('labels a place on its own', () => {
    expect(placeLabel(place('Canada', 'contains_bc'))).toBe('Canada (a large area that includes BC)')
    expect(placeLabel(place('Victoria', 'in_bc'))).toBe('Victoria')
  })
})
