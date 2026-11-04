// A rank's name as the site writes it: a genus in italics, as scientific
// names are; a family, order, class, phylum or kingdom in plain type.
export default function RankName({ rank, name }) {
  return rank === 'genus' ? <i>{name}</i> : name
}

// A rank's word with a capital, for the start of a title: "Genus".
export function rankTitle(rank) {
  return rank.charAt(0).toUpperCase() + rank.slice(1)
}
