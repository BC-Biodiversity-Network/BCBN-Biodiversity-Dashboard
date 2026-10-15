// A small stand-in for the species card (step 6): shows which species is
// selected, by its scientific name and key. If an old merged key was typed,
// it says so, because the species shown is the key it was merged into.
// onClear removes the selection.
export default function SelectedSpecies({ species, onClear }) {
  return (
    <div className="selected-species" aria-live="polite">
      <div className="selected-heading">
        <span>Selected species</span>
        <button
          type="button"
          className="selected-clear"
          onClick={onClear}
          aria-label="Clear the selected species"
        >
          ×
        </button>
      </div>
      <div className="scientific-name">{species.col_name}</div>
      <div className="selected-key">key {species.specieskey}</div>
      {species.merged_from && (
        <div className="selected-note">
          You searched for {species.merged_from}, an old key that was merged into{' '}
          {species.specieskey}.
        </div>
      )}
    </div>
  )
}
