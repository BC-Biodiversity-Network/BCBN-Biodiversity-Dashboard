import { Fragment, useState } from 'react'

import {
  capitalised, checkedAccessLabel, coordinateFields, creatorsLabel, harvestedDegrees, identifierLabel, isWebLink,
  issuedLabel, looksLikeHtml, plainText, readLicences, shownCoordinates, shownDegrees,
} from './datasetText'
import InfoTip from './InfoTip'
import { speciesMapSearch } from './route'
import SpeciesName from './SpeciesName'
import { MayIncludeBcTag } from './widerDatasets'

// An abstract longer than this is shown cut, with "Show more".
const ABSTRACT_FOLD = 700

// The full abstract, as plain text, folded with "Show more" when long.
function Abstract({ text }) {
  const [open, setOpen] = useState(false)
  const plain = looksLikeHtml(text) ? plainText(text) : (text ?? '').trim()
  if (!plain) {
    return <p className="dataset-muted">No description in the record.</p>
  }
  const long = plain.length > ABSTRACT_FOLD
  const shown = long && !open ? `${plain.slice(0, ABSTRACT_FOLD).replace(/\s+\S*$/, '')}...` : plain
  return (
    <div className="dataset-abstract">
      <p>{shown}</p>
      {long && (
        <button type="button" className="text-button" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  )
}

// One licence: its short name, linked to its web address when there is one,
// and its full text behind a button when the name had to be shortened.
function Licence({ licence }) {
  const [open, setOpen] = useState(false)
  return (
    <li>
      {licence.uri
        ? <a href={licence.uri} target="_blank" rel="noopener noreferrer">{licence.name}</a>
        : licence.name}
      {licence.fullText && (
        <>
          {' '}
          <button type="button" className="text-button" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? 'Hide the full licence text' : 'Show the full licence text'}
          </button>
          {open && <p className="dataset-licence-text">{licence.fullText}</p>}
        </>
      )}
    </li>
  )
}

// "(checked by BCBN)" after a value taken from the original catalogue, and a
// "?" whose tip says where it comes from and why the page follows it (the
// original catalogue is where the dataset is published; Lunaris only
// collects records from many catalogues).
function CheckedByBcbn({ ariaLabel, children }) {
  return (
    <>
      {' '}
      <span className="dataset-checked">
        (checked by BCBN)
        <InfoTip label="?" className="help-button" ariaLabel={ariaLabel}>{children}</InfoTip>
      </span>
    </>
  )
}

// The catalogues whose access BCBN has not checked yet although Lunaris
// marks every one of their records restricted, which may not match the
// catalogue: their datasets get a note under the access line until they are
// checked by hand.
const SOURCES_WITH_UNCHECKED_ACCESS = new Set(['pangaea.de'])

// The one date the site gives for BCBN's checks: they were done on 2 and 3
// November 2026 and finished on 3 November. Each row's own date stays in the
// data (checked_on) but is not shown.
const SOURCE_CHECK_AS_OF = '3 November 2026'

// Where a checked value comes from, the start of each tip.
function checkedFrom(check) {
  return `From ${check.catalogue_checked}, where this dataset is published (checked by BCBN, as of `
    + `${SOURCE_CHECK_AS_OF}).`
}

// One row of the Details table: the label (with an optional note under it)
// on the left, the value on the right; stacked on a phone.
function DetailRow({ label, note, children }) {
  return (
    <tr>
      <th scope="row">
        {label}
        {note && <span className="dataset-details-note"> ({note})</span>}
      </th>
      <td>{children}</td>
    </tr>
  )
}

// The Details table, like the "Citation Metadata" table on Borealis: what the
// record itself says, all as plain text. Each row is left out when the
// record has nothing for it, except Licence, which says so when there is
// none. The publisher is left out when it is the catalogue's own name
// (ignoring case), which the line under the title already gives. Where BCBN
// checked the original catalogue (dataset.source_check), its access is shown
// instead of the record's when the two differ, and its licence when the
// record has none, each marked "(checked by BCBN)" with a tip.
function Details({ dataset }) {
  const creators = creatorsLabel(dataset.creators)
  const publisher = dataset.publisher?.trim()
  const showPublisher = Boolean(publisher)
    && publisher.toLowerCase() !== (dataset.source_name ?? '').trim().toLowerCase()
  const published = issuedLabel(dataset.issued)
  const identifier = identifierLabel(dataset.link, dataset.link_type)
  const subjects = dataset.subjects ?? []
  const { licences, accessRights } = readLicences(dataset.licences)
  const check = dataset.source_check
  const checkedAccess = check?.access_matches_record === false ? checkedAccessLabel(check) : null
  const checkedLicences = licences.length === 0 ? readLicences(check?.licences).licences : []
  const uncheckedAccess = SOURCES_WITH_UNCHECKED_ACCESS.has(dataset.source) && !check?.access

  return (
    <section className="dataset-section">
      <h3>Details</h3>
      <table className="dataset-details">
        <tbody>
          {creators && <DetailRow label="Creators" note="as listed in the record">{creators}</DetailRow>}
          {showPublisher && <DetailRow label="Publisher">{plainText(dataset.publisher)}</DetailRow>}
          {published && <DetailRow label="Published">{published}</DetailRow>}
          {identifier && (
            <DetailRow label="Identifier">
              <a href={dataset.link} target="_blank" rel="noopener noreferrer">{identifier}</a>
            </DetailRow>
          )}
          {subjects.length > 0 && (
            <DetailRow label="Subjects">
              <ul className="dataset-subjects">
                {subjects.map((subject) => <li key={subject}>{plainText(subject)}</li>)}
              </ul>
            </DetailRow>
          )}
          <DetailRow label="Licence">
            {licences.length === 0 && checkedLicences.length === 0
              && 'No licence in the record. Check the source page.'}
            {licences.length > 0 && (
              <ul className="dataset-licences">
                {licences.map((licence, i) => <Licence key={i} licence={licence} />)}
              </ul>
            )}
            {checkedLicences.length > 0 && (
              <div className="dataset-checked-value">
                <ul className="dataset-licences">
                  {checkedLicences.map((licence, i) => <Licence key={i} licence={licence} />)}
                </ul>
                <CheckedByBcbn ariaLabel="Where does this licence come from?">
                  {checkedFrom(check)} The record Lunaris collected from {dataset.source_name} did not include a
                  licence.
                </CheckedByBcbn>
              </div>
            )}
          </DetailRow>
          {(accessRights.length > 0 || checkedAccess) && (
            <DetailRow label="Access">
              {checkedAccess
                ? (
                  <p>
                    {capitalised(checkedAccess)}
                    <CheckedByBcbn ariaLabel="Where does this access come from?">
                      {checkedFrom(check)} Lunaris, which collects records from many catalogues, lists it
                      as {accessRights.length > 0 ? accessRights.join(', ').toLowerCase() : 'no access'}, so we
                      follow the original catalogue.
                    </CheckedByBcbn>
                  </p>
                )
                : <p>{accessRights.join(', ')}</p>}
              {uncheckedAccess && (
                <p className="dataset-attribution">
                  Note from BCBN: Lunaris marks every PANGAEA record as restricted access, which may not match
                  PANGAEA. Check the source page.
                </p>
              )}
            </DetailRow>
          )}
        </tbody>
      </table>
    </section>
  )
}

// One box or point written out: "west -150, east -110, south 35, north 65",
// each number rounded to at most 4 decimals, with the number as harvested
// in its tooltip so it can still be checked.
function Coordinates({ item, kind, brackets }) {
  const fields = coordinateFields(item, kind)
  return (
    <>
      {brackets && '('}
      {fields.map(([name, value], i) => (
        <Fragment key={name}>
          {i > 0 && ', '}
          {name} <span className="dataset-degrees" title={harvestedDegrees(value)}>{shownDegrees(value)}</span>
        </Fragment>
      ))}
      {brackets && ')'}
    </>
  )
}

// A sentence that lists boxes or points (a coordinates note of
// describeLocation). More than SHOWN_COORDINATES are folded: the first ones,
// then "and N more points", with a button that shows them all and folds
// them again.
function CoordinatesNote({ note }) {
  const [all, setAll] = useState(false)
  const { items, before, more, foldable } = shownCoordinates(note, all)
  return (
    <p>
      {note.lead}{' '}
      {items.map((item, i) => (
        <Fragment key={i}>
          {before[i]}
          <Coordinates item={item} kind={note.kind} brackets={note.brackets} />
        </Fragment>
      ))}
      {more}
      {note.tail}
      {foldable && (
        <>
          {' '}
          <button type="button" className="text-button" onClick={() => setAll(!all)} aria-expanded={all}>
            {all ? 'Show less' : 'Show all'}
          </button>
        </>
      )}
    </p>
  )
}

// How many entries of a record's place list are shown before the rest are
// folded behind a "Show all" button.
const SHOWN_PLACES = 10

// The record's own place list, as the catalogue gives it, one entry per
// line, under "Places listed in the record". A long list is folded after
// SHOWN_PLACES entries.
function RecordPlaces({ places }) {
  const [all, setAll] = useState(false)
  if (places.length === 0) {
    return null
  }
  const shown = all ? places : places.slice(0, SHOWN_PLACES)
  return (
    <>
      <h4>Places listed in the record</h4>
      <ul className="dataset-places">
        {shown.map((place, i) => <li key={i}>{place}</li>)}
      </ul>
      {places.length > SHOWN_PLACES && (
        <p>
          {!all && `and ${(places.length - SHOWN_PLACES).toLocaleString()} more. `}
          <button type="button" className="text-button" onClick={() => setAll(!all)} aria-expanded={all}>
            {all ? 'Show less' : 'Show all'}
          </button>
        </p>
      )}
    </>
  )
}

// Where the dataset's location comes from, in words (see describeLocation),
// then the record's own place list.
function Location({ location }) {
  return (
    <section className="dataset-section">
      <h3>Location</h3>
      {location.notes.map((note, i) => (typeof note === 'string'
        ? <p key={i}>{note}</p>
        : <CoordinatesNote key={i} note={note} />))}
      <RecordPlaces places={location.places} />
    </section>
  )
}

// The species the record names, each linked to its card on the species map
// (onOpen(key) opens it in place of following the link), with the names as
// written in the record that differ from the site's; then the organisms the
// record names that are not linked, as plain text, so none is hidden.
function Species({ species, others, onOpen }) {
  return (
    <section className="dataset-section">
      <h3>Species</h3>
      {species.length === 0 && others.length === 0 && <p>The record names no organisms.</p>}
      {species.length > 0 && (
        <ul className="dataset-species">
          {species.map((item) => {
            const written = item.names_as_written.filter((name) => !name.same_as_shown).map((name) => name.name)
            return (
              <li key={item.specieskey}>
                <a
                  href={speciesMapSearch(item.specieskey)}
                  onClick={(event) => {
                    if (!onOpen || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) {
                      return
                    }
                    event.preventDefault()
                    onOpen(item.specieskey)
                  }}
                >
                  <SpeciesName species={item} />
                </a>
                {written.length > 0 && (
                  <span className="dataset-muted">
                    {' '}(written in the record as {written.map((name) => `“${name}”`).join(', ')})
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {others.length > 0 && <p>Other organisms named in the record: {others.join(', ')}</p>}
      {(species.length > 0 || others.length > 0) && (
        <p className="dataset-attribution">Species links are matched by BCBN from the names in the record.</p>
      )}
    </section>
  )
}

// The other copies of this dataset in other catalogues, each linked.
function OtherCopies({ copies }) {
  if (!copies?.length) {
    return null
  }
  return (
    <section className="dataset-section">
      <h3>Also listed in</h3>
      <ul className="dataset-copies">
        {copies.map((copy) => (
          <li key={copy.id}>
            {isWebLink(copy.link)
              ? <a href={copy.link} target="_blank" rel="noopener noreferrer">{copy.source_name}</a>
              : copy.source_name}
          </li>
        ))}
      </ul>
    </section>
  )
}

// One dataset in full (from /api/datasets/{id}), in this order: its title,
// catalogue, the button to view and download it on the original site, the
// abstract, the Details table (creators, publisher, published date,
// identifier, subjects, licence, access), where its location comes from,
// the species it names and its other copies. location is
// describeLocation(dataset), worked out once by the page and shared with the
// map. onOpenSpecies(key) opens a species' card on the species map.
export default function DatasetDetail({ dataset, location, onBack, onOpenSpecies }) {
  const copyAsked = dataset.requested_copy
    ? dataset.other_copies?.find((copy) => copy.id === dataset.requested_copy)
    : null
  return (
    <article className="dataset-detail">
      <button type="button" className="text-button dataset-back" onClick={onBack}>
        Back to the list
      </button>
      <h2>{plainText(dataset.title) || 'Untitled dataset'}</h2>
      <p className="dataset-meta">{dataset.source_name}</p>
      <MayIncludeBcTag frontendGroup={dataset.frontend_group} />
      {dataset.source_check?.removed && (
        <p className="dataset-removed-note">
          Note from BCBN: as of {SOURCE_CHECK_AS_OF} this dataset was no longer
          on {dataset.source_check.catalogue_checked}, where it was published, so the link may not work.
        </p>
      )}
      {copyAsked && (
        <p className="dataset-muted">
          You followed a link to the copy in {copyAsked.source_name}. This is another copy of the same
          dataset, from {dataset.source_name}.
        </p>
      )}

      {isWebLink(dataset.link) && (
        <a className="dataset-link-button" href={dataset.link} target="_blank" rel="noopener noreferrer">
          View and download on {dataset.source_name}
        </a>
      )}

      <Abstract text={dataset.abstract} />
      <Details dataset={dataset} />
      <Location location={location} />
      <Species species={dataset.species ?? []} others={dataset.other_organisms ?? []} onOpen={onOpenSpecies} />
      <OtherCopies copies={dataset.other_copies} />
    </article>
  )
}
