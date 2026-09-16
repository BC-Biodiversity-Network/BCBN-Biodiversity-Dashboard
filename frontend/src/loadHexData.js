// Every gzip file starts with these same two bytes. They are the marker that
// tells us we are looking at a compressed file rather than plain text.
const GZIP_FIRST_BYTE = 0x1f
const GZIP_SECOND_BYTE = 0x8b

// Downloads one of the hexagon files and returns its rows as an array of
// objects, one object per hexagon.
//
// The file on disk is gzipped, and it can reach us in two different states:
//
//   - Some servers notice the .gz ending and add a "Content-Encoding: gzip"
//     header. The browser then unzips the file on the way in, so what arrives
//     here is already plain text.
//   - Other servers hand over the bytes untouched, so what arrives here is
//     still the compressed file and we have to unzip it ourselves.
//
// We cannot simply read the response headers to find out which happened,
// because the browser does not let JavaScript see Content-Encoding. So we look
// at the actual bytes: if they start with the gzip marker the file is still
// compressed, and if they do not, the browser already dealt with it.
export async function loadHexData(url) {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`Could not load ${url}: ${response.status} ${response.statusText}`)
  }

  const buffer = await response.arrayBuffer()
  const firstBytes = new Uint8Array(buffer, 0, Math.min(2, buffer.byteLength))
  const stillCompressed =
    firstBytes[0] === GZIP_FIRST_BYTE && firstBytes[1] === GZIP_SECOND_BYTE

  const text = stillCompressed
    ? await unzipToText(buffer)
    : new TextDecoder('utf-8').decode(buffer)

  return parseCsv(text)
}

// Unzips gzipped bytes and returns the text inside them. DecompressionStream
// is built into the browser, so this needs no library.
async function unzipToText(buffer) {
  const compressedStream = new Blob([buffer]).stream()
  const plainStream = compressedStream.pipeThrough(new DecompressionStream('gzip'))
  return await new Response(plainStream).text()
}

// Turns the CSV text into an array of objects, one per row.
//
// The file is written by backend/pipeline/build_hex_aggregates.py and is
// deliberately plain: three columns, plain digits, no quotes and no commas
// inside any value. That is checked when the file is built, so splitting on
// commas is safe here and a full CSV parser would be more than we need.
function parseCsv(text) {
  const lines = text.trim().split('\n')
  const rows = []

  // Line 0 is the header (h3_cell,occurrences,distinct_species), so start at 1.
  for (let i = 1; i < lines.length; i++) {
    const fields = lines[i].split(',')
    rows.push({
      h3Cell: fields[0],
      occurrences: Number(fields[1]),
      distinctSpecies: Number(fields[2]),
    })
  }

  return rows
}
