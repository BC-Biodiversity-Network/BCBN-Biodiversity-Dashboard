// A species' name on the site: its English name from BCSEE with the
// scientific name in brackets and italics, or the scientific name alone.
// species is { col_name, english_name } as the API sends it.
export default function SpeciesName({ species }) {
  return species.english_name
    ? <>{species.english_name} (<i>{species.col_name}</i>)</>
    : <i>{species.col_name}</i>
}

// The same name as plain text, for a button's label.
export function speciesLabel(species) {
  return species.english_name ? `${species.english_name} (${species.col_name})` : species.col_name
}
