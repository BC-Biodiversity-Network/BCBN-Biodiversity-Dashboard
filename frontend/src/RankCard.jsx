import { useEffect, useState } from 'react'

import { fetchRank, rankExportUrl } from './api'
import CsvDownload from './CsvDownload'
import { InPageLink, SpeciesRow } from './HexagonPanel'
import RankName, { rankTitle } from './RankName'
import { NO_LIST, mapSearch, rankKey } from './route'
import { yearsKey, yearsLabel } from './yearRange'
import SpeciesListControls, { SpeciesListPaging, noMatchText, shownText } from './SpeciesListControls'

// How many species one page of the card's list holds.
export const PAGE_SIZE = 50

// How many names of the next rank down are shown before "Show all".
export const SHOWN_CHILDREN = 10

// Each rank's name for several of them, for the heading of the next rank
// down ("Genera (3)").
const PLURALS = { phylum: 'Phyla', class: 'Classes', order: 'Orders', family: 'Families', genus: 'Genera' }

// "1 species" or "12 species"; "1 record" or "12 records".
function speciesWord(count) {
  return `${count.toLocaleString()} species`
}
function recordsWord(count) {
  return `${count.toLocaleString()} ${count === 1 ? 'record' : 'records'}`
}

// The card's totals in words: "5 species in BC, 965,555 records, 1861 to
// 2026."
export function rankTotalsText(card) {
  const years = card.first_year === null ? ''
    : card.first_year === card.last_year ? `, ${card.first_year}` : `, ${card.first_year} to ${card.last_year}`
  return `${speciesWord(card.species_count)} in BC, ${recordsWord(card.record_count)}${years}.`
}

// A classification line, kingdom first, on a species card (the whole path
// to the genus) and a rank card (the ranks above it). Each named rank is a
// link that opens that rank (onOpenRank(rankRef), with its own path up to
// it), named on hover ("Class Aves"); a rank GBIF leaves out is shown as
// "no order", not a link, since there is no such rank to open.
export function ClassificationLine({ path, onOpenRank }) {
  if (path.length === 0) {
    return null
  }
  return (
    <p className="card-taxonomy" aria-label="Classification">
      {path.map((step, index) => {
        const rankRef = { rank: step.rank, name: step.name, path: path.slice(0, index + 1) }
        return (
          <span key={step.rank}>
            {index > 0 && ' › '}
            {step.name
              ? (
                <InPageLink href={mapSearch({ rank: rankRef })} onFollow={() => onOpenRank(rankRef)}
                  title={`${rankTitle(step.rank)} ${step.name}`} aria-label={`${rankTitle(step.rank)} ${step.name}`}>
                  <RankName rank={step.rank} name={step.name} />
                </InPageLink>
              )
              : <span className="rank-missing" title={`GBIF gives no ${step.rank} here`}>no {step.rank}</span>}
          </span>
        )
      })}
    </p>
  )
}

// The next rank down, most records first: each name with its species and
// records, a link that opens that rank (onOpenRank), and the species missing
// that rank in GBIF, not a link. More than SHOWN_CHILDREN are folded behind
// "Show all".
function Children({ card, onOpenRank }) {
  const [all, setAll] = useState(false)
  const shown = all ? card.children : card.children.slice(0, SHOWN_CHILDREN)
  return (
    <section className="card-section">
      <h3>{PLURALS[card.children_rank]} ({card.children.filter((child) => child.name).length.toLocaleString()})</h3>
      <ul className="rank-children">
        {shown.map((child) => {
          if (!child.name) {
            return (
              <li key="(missing)" className="rank-missing">
                No {card.children_rank} in GBIF: {speciesWord(child.species_count)}, {recordsWord(child.record_count)}
              </li>
            )
          }
          const childRef = { rank: card.children_rank, name: child.name,
            path: [...card.path, { rank: card.children_rank, name: child.name }] }
          return (
            <li key={child.name}>
              <InPageLink href={mapSearch({ rank: childRef })} onFollow={() => onOpenRank(childRef)}>
                <RankName rank={card.children_rank} name={child.name} />
              </InPageLink>
              <span className="rank-child-count">
                {' '}{speciesWord(child.species_count)}, {recordsWord(child.record_count)}
              </span>
            </li>
          )
        })}
      </ul>
      {card.children.length > SHOWN_CHILDREN && (
        <button type="button" className="text-button" onClick={() => setAll(!all)} aria-expanded={all}>
          {all ? 'Show less' : `Show all ${card.children.length.toLocaleString()}`}
        </button>
      )}
    </section>
  )
}

// The message shown instead of a card.
function CardError({ error }) {
  let message = `The card could not be loaded: ${error.message}`
  if (error.status === 404) {
    message = 'No species in BC has this classification.'
  } else if (error.kind === 'unreachable') {
    message = 'The card is not available: the API could not be reached.'
  }
  return <p className="card-message error">{message}</p>
}

// A rank's card (from /api/ranks/{rank}), in the side panel where the
// species card goes: the rank and its path (a missing rank shown as such),
// its species in BC, records and years, the next rank down (each opening
// that rank: onOpenRank(rankRef)), its species, a page of PAGE_SIZE at a
// time (each opening its card: onOpenSpecies(key)), with a filter by name
// and by BC status and a choice of order, as in a hexagon's panel (see
// SpeciesListControls.jsx), and where the classification and names come
// from. The ranks above it open as well (onOpenRank). rankRef is { rank,
// name, path }; list holds the species list's options ({ q, sort, status,
// page }, from the address) and onListChange(list) changes them; onClose
// closes the rank. years is the year range of the map ({ from, to }, or
// null for all years): every number on the card then counts only those
// years, as the map does, and the card says so.
export default function RankCard({
  rankRef, years = null, list = NO_LIST, onListChange = () => {}, onClose, onOpenSpecies, onOpenRank,
}) {
  const [loaded, setLoaded] = useState(null)
  const offset = (list.page - 1) * PAGE_SIZE
  const request = JSON.stringify([rankKey(rankRef), yearsKey(years), list.q, list.status, list.sort, offset])

  // Loads the card asked for; a newer request cancels an older one.
  useEffect(() => {
    const controller = new AbortController()
    fetchRank(rankRef, { years, list, offset, limit: PAGE_SIZE, signal: controller.signal })
      .then((card) => setLoaded({ request, card }))
      .catch((error) => {
        if (error.name !== 'AbortError') {
          console.error(error)
          setLoaded({ request, error })
        }
      })
    return () => controller.abort()
    // request holds everything the fetch depends on, so it is the only
    // dependency.
  }, [request])

  const current = loaded?.request === request ? loaded : null
  // While another page, filter or year range loads, the last card of this
  // rank stays.
  const sameRank = loaded && JSON.parse(loaded.request)[0] === rankKey(rankRef)
  const card = current?.card ?? (sameRank ? loaded.card : null)
  const shown = card ? shownText(card, list) : null

  return (
    <aside className="species-card rank-card" aria-label="Rank card" aria-busy={!current}>
      <button type="button" className="card-close" onClick={onClose}
        aria-label="Close this rank and show all species">
        ×
      </button>
      <header className="card-title">
        <h2>{rankTitle(rankRef.rank)} <RankName rank={rankRef.rank} name={rankRef.name} /></h2>
      </header>
      <ClassificationLine path={rankRef.path.slice(0, -1)} onOpenRank={onOpenRank} />
      {current?.error && <CardError error={current.error} />}
      {!card && !current?.error && <p className="card-message">Loading the card...</p>}
      {card && (
        <>
          {card.years_counted && (
            <p className="rank-years">
              Every number on this card counts only the records from {yearsLabel(card.years_counted)}, as on
              the map.
            </p>
          )}
          {card.species_count > 0
            ? <p className="rank-totals">{rankTotalsText(card)}</p>
            : (
              <p className="rank-totals">
                No records of this {rankRef.rank} from {yearsLabel(card.years_counted)}.
              </p>
            )}
          {card.records_no_year > 0 && (
            <p className="hexagon-note">
              {card.records_no_year.toLocaleString()} of these records {card.records_no_year === 1 ? 'has' : 'have'} no date.
            </p>
          )}
          {card.records_no_year_left_out > 0 && (
            <p className="hexagon-note">
              {card.records_no_year_left_out.toLocaleString()} {card.records_no_year_left_out === 1
                ? 'record with no date is' : 'records with no date are'} not counted.
            </p>
          )}
          {card.children && card.children.length > 0 && <Children card={card} onOpenRank={onOpenRank} />}
          {card.species_count > 0 && (
            <section className="card-section">
              <h3>Species ({card.species_count.toLocaleString()})</h3>
              <SpeciesListControls list={list} data={card} onChange={onListChange}
                filterLabel={`Filter the species of this ${rankRef.rank} by name`} />
              {shown && <p className="species-list-shown">{shown}</p>}
              {/* The whole list as a CSV file, for the options asked for;
                  not while they load, after an error, or with nothing to list. */}
              <CsvDownload href={rankExportUrl(rankRef, { years, list })}
                disabled={!current || Boolean(current.error) || card.matching === 0}
                label={`Download the species of this ${rankRef.rank} as a CSV file`} />
              {card.matching === 0
                ? <p className="hexagon-note">{noMatchText(list)}</p>
                : (
                  <ol className="hexagon-species" start={card.offset + 1}>
                    {card.members.map((member) => (
                      <SpeciesRow key={member.specieskey} item={{ ...member, records: member.record_count }}
                        onOpen={onOpenSpecies} />
                    ))}
                  </ol>
                )}
              <SpeciesListPaging data={card} list={list} onChange={onListChange} />
            </section>
          )}
          <p className="hexagon-sources">
            The classification (kingdom to genus) comes from {card.classification_source}; the species names
            come from the Catalogue of Life. Records are counted after merging keys, as on the maps.
          </p>
        </>
      )}
    </aside>
  )
}
