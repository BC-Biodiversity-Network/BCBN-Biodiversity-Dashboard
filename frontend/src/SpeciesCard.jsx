import { Fragment, useEffect, useState } from 'react'

import { fetchSpecies } from './api'
import { BCSEE_LIST_HELP_URL, bcseeDefinition } from './bcseeDefinitions'
import InfoTip from './InfoTip'
import OglBcStatement from './OglBcStatement'
import { BCSEE_URL, bcseeCitation, bcseeCitationEnd, citationDate, colRelease } from './sources'

// The BCSEE values that are conservation statuses, each with its own chip
// colour. Every other value (Not Reviewed, Unknown, Accidental, No Status,
// ...) gets a grey chip.
const SHOWN_STATUSES = ['Red', 'Blue', 'Yellow', 'Exotic']

// How BCSEE's levels below the species are written in a sentence, one and
// several.
const LEVEL_WORDS = {
  Subspecies: ['subspecies', 'subspecies'],
  Variety: ['variety', 'varieties'],
  Population: ['population', 'populations'],
}

// The explanation behind the "?" next to the name and authorship, using this
// card's own authorship and scientific name, both exactly as stored. The
// sentence about parentheses is only added when the authorship has them.
function AuthorshipHelp({ name }) {
  return (
    <>
      “{name.col_authorship}” is the authorship of the scientific name{' '}
      <i>{name.col_name}</i>: who first named this species and, if a year is shown, the
      year they named it.
      {name.col_authorship.includes('(') && (
        <span className="tip-paragraph">
          Parentheses around the authorship, as in “(Linnaeus, 1766)”, mean the species was
          first described under a different name, usually in a different genus, and later
          given its current name.
        </span>
      )}
    </>
  )
}

// The species card panel. requestedKey is the key to show: the one picked in
// search, or the old merged key that was typed, so the API can say it was
// merged. meta is /api/meta (or null while it loads), for the GBIF download
// citation. onClose returns to the plain overview.
export default function SpeciesCard({ requestedKey, meta, onClose }) {
  // The card as { key, card } once it arrives, or { key, error } if it fails.
  // Keeping the key with it means a card is only ever shown for the species
  // that was asked for, never a previous one.
  const [loaded, setLoaded] = useState(null)

  // Fetches the card whenever a different species is asked for, and cancels
  // the request for the previous one if it has not arrived yet.
  useEffect(() => {
    const controller = new AbortController()
    fetchSpecies(requestedKey, { signal: controller.signal })
      .then((card) => setLoaded({ key: requestedKey, card }))
      .catch((error) => {
        if (error.name !== 'AbortError') {
          console.error(error)
          setLoaded({ key: requestedKey, error })
        }
      })
    return () => controller.abort()
  }, [requestedKey])

  const current = loaded?.key === requestedKey ? loaded : null

  return (
    <aside className="species-card" aria-label="Species card" aria-busy={!current}>
      <button type="button" className="card-close" onClick={onClose}
        aria-label="Close the species card and return to the overview">
        ×
      </button>
      {!current && <p className="card-message">Loading the species card...</p>}
      {current?.error && <CardError error={current.error} requestedKey={requestedKey} />}
      {current?.card && <CardBody card={current.card} meta={meta} />}
    </aside>
  )
}

// The message shown instead of a card: the key has no card, the API cannot
// be reached, or something else went wrong.
function CardError({ error, requestedKey }) {
  let message = `The species card could not be loaded: ${error.message}`
  if (error.status === 404) {
    message = `No species card for key ${requestedKey}: there are no records of it in BC.`
  } else if (error.kind === 'unreachable') {
    message = 'The species card is not available: the API could not be reached.'
  }
  return <p className="card-message error">{message}</p>
}

// Everything on a loaded card, top to bottom.
function CardBody({ card, meta }) {
  return (
    <>
      <CardTitle card={card} />
      {card.merged_from && (
        <p className="card-note">
          You searched for {card.merged_from}, an old key that was merged into{' '}
          {card.specieskey}: both keys have exactly the same species name.
        </p>
      )}
      <SpeciesImage image={card.image} name={card.name.col_name} />
      <BcseeSection bcsee={card.bcsee} />
      <RecordsSection card={card} />
      <SourcesSection card={card} meta={meta} />
    </>
  )
}

// The top of the card: the Catalogue of Life name in italics, exactly as
// stored (a subgenus in brackets included), with its authorship as stored
// and a "?" explaining what the authorship is (no "?" when the card has no
// authorship, as 454 of 38,992 cards had in October 2026, since there is
// then nothing to explain); the classification from
// kingdom to genus; and the species' own common name from BCSEE when there
// is one. When BCSEE lists separate species on this key, the API sends no
// common name, so none is shown here; each BCSEE entry shows its own.
function CardTitle({ card }) {
  const { name } = card
  return (
    <header className="card-title">
      <h2>
        <span className="scientific-name">{name.col_name}</span>
        {name.col_authorship && (
          <>
            <span className="card-authorship"> {name.col_authorship}</span>
            <InfoTip label="?" className="help-button" ariaLabel="What is the authorship?">
              <AuthorshipHelp name={name} />
            </InfoTip>
          </>
        )}
      </h2>
      <TaxonomyLine taxonomy={card.taxonomy} />
      {card.english_name && (
        <p className="card-common-name">
          {card.english_name}
          <span className="card-tag">Common name from BCSEE</span>
        </p>
      )}
    </header>
  )
}

// The ranks of the classification line, in order, with how each is named
// when its name is hovered, focused or tapped.
const RANKS = [
  ['kingdom', 'Kingdom'], ['phylum', 'Phylum'], ['class', 'Class'],
  ['order', 'Order'], ['family', 'Family'], ['genus', 'Genus'],
]

// The classification from kingdom to genus on one line. Each name opens its
// rank (Kingdom, Phylum, ...) on hover, with the keyboard and on a tap, the
// same way as the status chips.
function TaxonomyLine({ taxonomy }) {
  const present = RANKS.filter(([rank]) => taxonomy[rank])
  return (
    <p className="card-taxonomy" aria-label="Classification">
      {present.map(([rank, rankName], index) => (
        <Fragment key={rank}>
          {index > 0 && ' › '}
          <InfoTip label={taxonomy[rank]} className="rank-button"
            ariaLabel={`${rankName}: ${taxonomy[rank]}`}>
            {rankName}
          </InfoTip>
        </Fragment>
      ))}
    </p>
  )
}

// The species photo with its full credit: creator, licence with its link,
// and a link to the original. The API only sends a photo when all of these
// are known. The whole photo is always shown, never cropped, which an ND
// licence requires. For a licence other than CC0 or the Public Domain Mark
// the copyright holder is shown too. If the photo fails to load, a note
// replaces it.
function SpeciesImage({ image, name }) {
  const [failed, setFailed] = useState(false)

  if (!image) {
    return <p className="card-no-image">No fully credited photo available.</p>
  }

  const publicDomain = image.license_code === 'CC0_1_0' || image.license_code === 'PDM_1_0'
  const holder = image.rightsholder || image.creator

  return (
    <figure className="card-image">
      {failed ? (
        <p className="card-no-image">
          The photo could not be loaded.{' '}
          <a href={image.page_url} target="_blank" rel="noreferrer">See the original</a>.
        </p>
      ) : (
        <img
          src={image.url}
          alt={`Photo of ${name}`}
          className={image.no_derivatives ? 'whole-image no-derivatives' : 'whole-image'}
          onError={() => setFailed(true)}
        />
      )}
      <figcaption className="card-credit">
        Photo: {image.creator}
        {!publicDomain && holder && ` · © ${holder}`}
        {' · '}
        {image.license_url ? (
          <a href={image.license_url} target="_blank" rel="noreferrer">{image.license_label}</a>
        ) : (
          image.license_label
        )}
        {' · '}
        <a href={image.page_url} target="_blank" rel="noreferrer">View original</a>
      </figcaption>
    </figure>
  )
}

// What a BCSEE entry applies to: 'whole' (the whole species), 'separate' (a
// species BCSEE lists separately, though the Catalogue of Life counts it as
// part of this one) or 'part' (a subspecies, variety or population). Uses
// BCSEE's own level for the entry; for a card from an older file without it,
// falls back on is_species_level.
export function entryScope(entry, separate) {
  const level = entry.classification_level
  const speciesLevel = level ? level === 'Species' : entry.is_species_level || separate
  if (!speciesLevel) {
    return 'part'
  }
  return separate ? 'separate' : 'whole'
}

// The word for an entry's level below the species ("subspecies", "variety",
// "population"), or a general phrase when the level is not known.
function levelWord(entry, plural = false) {
  const words = LEVEL_WORDS[entry.classification_level]
  if (!words) {
    return plural ? 'subspecies, varieties or populations' : 'subspecies, variety or population'
  }
  return words[plural ? 1 : 0]
}

// Works out how the BCSEE section describes what its statuses apply to. If
// every entry has the same scope, it is said once at the top; if they
// differ, a line goes under each entry. Returns { heading, intro,
// perEntry }.
export function scopeSummary(entries, separate) {
  const scopes = entries.map((entry) => entryScope(entry, separate))
  const allSame = scopes.every((scope) => scope === scopes[0])
  const separateCount = scopes.filter((scope) => scope === 'separate').length

  let heading = 'BC conservation status'
  if (allSame && scopes[0] === 'whole') {
    heading = 'BC conservation status (applies to the whole species)'
  }

  let intro = null
  if (separateCount > 0) {
    // Entries below the species can sit on the same card (a subspecies of one
    // of the separate BCSEE species); they are counted too, so the number
    // matches the list.
    const parts = entries.filter((entry, index) => scopes[index] === 'part')
    const partWords = [...new Set(parts.map((entry) => levelWord(entry, parts.length > 1)))]
    const alsoParts = parts.length > 0 ? ` and ${parts.length} ${partWords.join(' and ')}` : ''
    intro = 'This card follows the Catalogue of Life, which treats these as one species. ' +
      `BCSEE lists them as ${separateCount} separate species${alsoParts}, each with its own status:`
  } else if (allSame && scopes[0] === 'part') {
    const words = [...new Set(entries.map((entry) => levelWord(entry, entries.length > 1)))]
    intro = entries.length === 1
      ? `BCSEE has no entry for the species as a whole, only for this ${words[0]}:`
      : `BCSEE has no entry for the species as a whole, only for these ${words.join(' and ')}:`
  }

  return { heading, intro, perEntry: !allSame }
}

// The conservation status section. Everything in it comes from BCSEE, and
// every status chip says so; the source and its dates are in the Sources
// section at the bottom.
function BcseeSection({ bcsee }) {
  const entries = bcsee.entries
  const separate = bcsee.has_separate_bcsee_species
  const { heading, intro, perEntry } = entries.length > 0
    ? scopeSummary(entries, separate)
    : { heading: 'BC conservation status', intro: null, perEntry: false }

  return (
    <section className="card-section" aria-label="BC conservation status">
      <h3>{heading}</h3>
      {bcsee.state === 'not_in_bcsee' && (
        <p className="bcsee-summary">Not in BCSEE: it has no entry for this species.</p>
      )}
      {bcsee.state === 'unknown' && (
        <p className="bcsee-summary">BCSEE status not known for this species.</p>
      )}
      {intro && <p className="bcsee-summary">{intro}</p>}
      {entries.length > 0 && (
        <ul className="bcsee-entries">
          {entries.map((entry) => (
            <BcseeEntry
              key={entry.element_codes.join(' ')}
              entry={entry}
              scope={perEntry ? entryScope(entry, separate) : null}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

// One BCSEE entry: its value as a chip that names BCSEE and opens BCSEE's
// definition, its scientific name in italics with its common name, and,
// when the entries on the card apply to different things, what this one
// applies to (scope; null when it is said once at the top).
function BcseeEntry({ entry, scope }) {
  const isStatus = SHOWN_STATUSES.includes(entry.status)
  const chipClass = isStatus ? `status-chip status-${entry.status.toLowerCase()}` : 'status-chip status-none'

  return (
    <li className="bcsee-entry">
      <InfoTip
        label={`BCSEE: ${entry.status}`}
        className={chipClass}
        ariaLabel={`BCSEE: ${entry.status}. What does this mean?`}
      >
        <strong>{entry.status}</strong> (BCSEE): {bcseeDefinition(entry.status)}
      </InfoTip>
      <span className="bcsee-label">
        <span className="scientific-name">{entry.scientific_label}</span>
        {entry.common_label && ` (common name: ${entry.common_label})`}
      </span>
      {scope && <ScopeLine entry={entry} scope={scope} />}
    </li>
  )
}

// The short line under an entry saying what it applies to.
function ScopeLine({ entry, scope }) {
  if (scope === 'whole') {
    return <span className="bcsee-scope">Applies to the whole species.</span>
  }
  if (scope === 'separate') {
    return (
      <span className="bcsee-scope">
        Applies to the separate BCSEE species{' '}
        <span className="scientific-name">{entry.scientific_label}</span> only.
      </span>
    )
  }
  return (
    <span className="bcsee-scope">
      Applies to {levelWord(entry)} <span className="scientific-name">{entry.scientific_label}</span> only.
    </span>
  )
}

// The sentence about records with no date, or nothing when there are none.
export function noDateSentence(count) {
  if (!count) {
    return null
  }
  return count === 1
    ? '1 of these records has no date.'
    : `${count.toLocaleString()} of these records have no date.`
}

// The records section: how many, over which years, from how many datasets,
// how many have no date, and the biggest datasets. Says how many records
// came from keys merged into this one.
function RecordsSection({ card }) {
  const { records } = card
  const years = records.first_year === null ? '' : `, ${records.first_year} to ${records.last_year}`
  const noDate = noDateSentence(records.records_no_year)
  return (
    <section className="card-section" aria-label="Records">
      <h3>Records in BC</h3>
      <p>
        <strong>{records.record_count.toLocaleString()}</strong>{' '}
        {records.record_count === 1 ? 'record' : 'records'}{years}, from{' '}
        {records.n_datasets.toLocaleString()} {records.n_datasets === 1 ? 'dataset' : 'datasets'}.
        {noDate && ` ${noDate}`}
      </p>
      {records.merged_keys.length > 0 && (
        <p className="card-note">
          Includes {records.merged_record_count.toLocaleString()}{' '}
          {records.merged_record_count === 1 ? 'record' : 'records'} filed under{' '}
          {records.merged_keys.length === 1 ? 'key' : 'keys'} {records.merged_keys.join(', ')}, merged
          into this species because the species name is exactly the same.
        </p>
      )}
      {card.top_datasets.length > 0 && (
        <>
          <h4>Largest datasets</h4>
          <ul className="card-datasets">
            {card.top_datasets.map((dataset) => (
              <li key={dataset.datasetkey}>
                <a href={`https://www.gbif.org/dataset/${dataset.datasetkey}`}
                  target="_blank" rel="noreferrer">
                  {dataset.title || dataset.datasetkey}
                </a>
                <span className="dataset-count">{dataset.records.toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

// Where everything on the card comes from, in small text at the bottom:
// names and authorship (with the Catalogue of Life's own citation and
// licence), the classification, the conservation status (cited the way
// the BC Conservation Data Centre asks, with the Open Government Licence –
// British Columbia attribution statement), the occurrence records (the GBIF
// download, cited the way GBIF asks) and the species key.
function SourcesSection({ card, meta }) {
  const { name, bcsee } = card
  const col = colRelease(name.col_release)
  const download = meta?.gbif_download

  return (
    <section className="card-sources" aria-label="Sources">
      <h3>Sources</h3>
      <p>
        <strong>Names and authorship:</strong> {name.name_source}, {name.col_release}.
        {col && (
          <>
            {' '}{col.authors} <i>{col.title}</i> {col.rest}{' '}
            <a href={col.doiUrl} target="_blank" rel="noreferrer">{col.doiUrl}</a>. Licence:{' '}
            <a href={col.licenceUrl} target="_blank" rel="noreferrer">{col.licenceLabel}</a>.
          </>
        )}
      </p>
      <p>
        <strong>Classification:</strong> as on this species&apos; GBIF occurrence records,
        which GBIF matches to the Catalogue of Life ({name.col_release}).
      </p>
      <p>
        <strong>Conservation status:</strong> {bcseeCitation(bcsee.status_source_date)}{' '}
        <a href={BCSEE_URL} target="_blank" rel="noreferrer">{BCSEE_URL}</a>{' '}
        {bcseeCitationEnd(bcsee.status_source_date, bcsee.status_pulled_on)} <OglBcStatement />{' '}
        Status definitions:{' '}
        <a href={BCSEE_LIST_HELP_URL} target="_blank" rel="noreferrer">BCSEE help, B.C. List</a>.
      </p>
      <p>
        <strong>Occurrence records:</strong>{' '}
        {download ? (
          <>
            GBIF.org ({citationDate(download.date)}) GBIF Occurrence Download{' '}
            <a href={download.doi_url} target="_blank" rel="noreferrer">{download.doi_url}</a>
          </>
        ) : (
          'GBIF.org (citation loading).'
        )}
      </p>
      <p>
        <strong>Species key:</strong> {card.specieskey}
      </p>
    </section>
  )
}
