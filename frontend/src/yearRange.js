// The year range shared by all three maps: what it looks like, how it is
// written, and which recent years are still incomplete.
//
// A range is null for all years (no filter, the default), or { from, to },
// where either may be null for an open end: from the first year in the data,
// or up to the last.

// The first year whose records are still clearly incomplete, checked against
// the GBIF download of 4 October 2026 (records per year in species_hex):
// 2023 had 3.92 million, 2024 4.18 million, then 2025 only 0.68 million and
// 2026 0.48 million so far. Records keep reaching GBIF a year or more after
// they are made, so recheck this after every new download.
export const INCOMPLETE_FROM_YEAR = 2025

// The short note shown under the year boxes. lastYear is the last year in
// the data (from /api/meta).
export function incompleteYearsNote(lastYear) {
  const years = lastYear > INCOMPLETE_FROM_YEAR
    ? `${INCOMPLETE_FROM_YEAR} and ${lastYear}`
    : String(INCOMPLETE_FROM_YEAR)
  return `Records from ${years} are still incomplete: many reach GBIF a year or more after they are made.`
}

// A short text that names a range, for cache keys and layer ids: "all" for
// all years, otherwise the two ends with an empty end left blank, as
// "2000-2020" or "2000-".
export function yearsKey(years) {
  return years ? `${years.from ?? ''}-${years.to ?? ''}` : 'all'
}

// The years an answer actually counted, as people write them: "2000 to
// 2020", or one year on its own. yearsCounted is the API's years_counted
// ([from, to] with open ends filled in).
export function yearsLabel(yearsCounted) {
  const [from, to] = yearsCounted
  return from === to ? String(from) : `${from} to ${to}`
}

// Reads one year box. Returns { year } (null for an empty box, meaning an
// open end) or { problem } with a sentence saying what is wrong.
export function readYearBox(text, firstYear, lastYear) {
  const trimmed = text.trim()
  if (trimmed === '') {
    return { year: null }
  }
  const year = Number(trimmed)
  if (!/^\d{4}$/.test(trimmed) || year < firstYear || year > lastYear) {
    return { problem: `Years run from ${firstYear} to ${lastYear}.` }
  }
  return { year }
}

// Turns the two year boxes into a range, or a problem. Two empty boxes mean
// all years (null).
export function readYearBoxes(fromText, toText, firstYear, lastYear) {
  const from = readYearBox(fromText, firstYear, lastYear)
  const to = readYearBox(toText, firstYear, lastYear)
  if (from.problem || to.problem) {
    return { problem: from.problem ?? to.problem }
  }
  if (from.year !== null && to.year !== null && from.year > to.year) {
    return { problem: 'The first year must not be after the last.' }
  }
  if (from.year === null && to.year === null) {
    return { years: null }
  }
  return { years: { from: from.year, to: to.year } }
}

// Whether two ranges are the same.
export function sameYears(a, b) {
  return yearsKey(a) === yearsKey(b)
}
