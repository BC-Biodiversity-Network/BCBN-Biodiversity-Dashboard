import { useState } from 'react'

import { isWebLink, issuedLabel, looksLikeHtml, plainText, readLicences } from './datasetText'
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

// The licences and access rights, all as plain text.
function Licences({ entries }) {
  const { licences, accessRights } = readLicences(entries)
  return (
    <section className="dataset-section">
      <h3>Licence</h3>
      {licences.length === 0
        ? <p>No licence given in the record.</p>
        : <ul className="dataset-licences">{licences.map((licence, i) => <Licence key={i} licence={licence} />)}</ul>}
      {accessRights.length > 0 && <p>Access: {accessRights.join(', ')}.</p>}
    </section>
  )
}

// Where the dataset's location comes from, in words (see describeLocation),
// with the GeoNames attribution whenever places from our lookup are shown.
function Location({ location }) {
  return (
    <section className="dataset-section">
      <h3>Location</h3>
      {location.notes.map((note) => <p key={note}>{note}</p>)}
      {location.geonames && (
        <p className="dataset-attribution">
          Place names from <a href="https://www.geonames.org/" target="_blank" rel="noreferrer">GeoNames</a>,
          licensed under{' '}
          <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer">CC BY 4.0</a>.
        </p>
      )}
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

// One dataset in full (from /api/datasets/{id}): what it is, the button to
// view and download it on the original site, where its location comes from,
// the species it names, its licence and its other copies. location is
// describeLocation(dataset), worked out once by the page and shared with the
// map. onOpenSpecies(key) opens a species' card on the species map.
export default function DatasetDetail({ dataset, location, onBack, onOpenSpecies }) {
  const copyAsked = dataset.requested_copy
    ? dataset.other_copies?.find((copy) => copy.id === dataset.requested_copy)
    : null
  const issued = issuedLabel(dataset.issued)

  return (
    <article className="dataset-detail">
      <button type="button" className="text-button dataset-back" onClick={onBack}>
        Back to the list
      </button>
      <h2>{plainText(dataset.title) || 'Untitled dataset'}</h2>
      <p className="dataset-meta">
        {dataset.source_name}
        {issued && <> · Issued {issued}</>}
      </p>
      <MayIncludeBcTag frontendGroup={dataset.frontend_group} />
      {copyAsked && (
        <p className="dataset-muted">
          You followed a link to the copy in {copyAsked.source_name}. This is the most recent copy of the
          same dataset.
        </p>
      )}

      {isWebLink(dataset.link) && (
        <a className="dataset-link-button" href={dataset.link} target="_blank" rel="noopener noreferrer">
          View and download on {dataset.source_name}
        </a>
      )}

      <Abstract text={dataset.abstract} />

      {dataset.subjects?.length > 0 && (
        <section className="dataset-section">
          <h3>Subjects</h3>
          <ul className="dataset-subjects">
            {dataset.subjects.map((subject) => <li key={subject}>{plainText(subject)}</li>)}
          </ul>
        </section>
      )}

      <Location location={location} />

      <Species species={dataset.species ?? []} others={dataset.other_organisms ?? []} onOpen={onOpenSpecies} />

      <Licences entries={dataset.licences} />
      <OtherCopies copies={dataset.other_copies} />
    </article>
  )
}
