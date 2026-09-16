"""
build_lunaris_taxa.py

Read the biodiversity candidates and note, for each record, which taxon names it
mentions. Nothing is filtered out: all 18,931 records come through, each with six
new columns that may be empty.

This is the second pass over the Lunaris data. The first pass
(build_lunaris_candidates.py) decided which records are about biodiversity at
all. This one asks a different question of the survivors: which living things are
they actually about?

Two things make this work, and both are easy to get wrong:

  Capitals carry meaning. A genus is written with a capital and its epithet
  without, which is the only thing separating "Beta" the beet genus from "beta
  diversity". So the original text is searched exactly as written. This is why
  build_haystack() from lunaris_keywords.py cannot be reused: it lowercases
  everything, which would erase the distinction.

  Names are looked up, not matched. There are tens of thousands of names, and
  turning them into one giant pattern would be slow and unreadable. Instead a
  small pattern finds things SHAPED like a name -- a capitalised word, or a
  capitalised word followed by a lowercase one -- and each candidate is then
  checked against a set.

Input:  data/lunaris_biodiv_candidates.parquet   (18,931 records)
        bc_species_summary.csv                   (the BC species list)
Output: data/lunaris_taxa.parquet                (same records, six new columns)
        data/lunaris_common_name_map.csv         (common name -> scientific name)
        data/lunaris_no_taxon.csv                (records where nothing matched)

Run:
    python build_lunaris_taxa.py
"""

import argparse
import collections
import os
import re
import time

import pandas as pd


# A capitalised word that could be a genus, family or higher rank. Three letters
# minimum, because two-letter capitals are nearly all abbreviations.
CAP_WORD = re.compile(r"(?<![A-Za-z])([A-Z][a-z]{2,})(?![A-Za-z])")

# A capitalised word followed by a lowercase one: the shape of a binomial.
BINOMIAL = re.compile(
    r"(?<![A-Za-z])([A-Z][a-z]{2,})[ ]+([a-z][a-z-]{2,})(?![A-Za-z])")

# "Vulpes sp." or "Vulpes spp." -- a genus named without pinning the species.
GENUS_QUALIFIED = re.compile(
    r"(?<![A-Za-z])([A-Z][a-z]{2,})[ ]+(spp?\.)(?![A-Za-z])")

# Text in round brackets, which is where common names sit.
BRACKET = re.compile(r"\(([^()]{2,80})\)")

# Up to four whole words immediately before a bracket. The lookbehind stops the
# match starting in the middle of a word, and the pattern cannot cross a comma
# or full stop because those are not part of a word.
WORDS_BEFORE = re.compile(
    r"(?<![A-Za-zÀ-ÿ])((?:[A-Za-zÀ-ÿ'’-]+[ \t]+){1,4})$")

# Two-word shapes that look like a species but are not. Listed as whole pairs
# and compared in lower case, never as bare epithets: blacklisting "major" on
# its own would also kill Parus major, the great tit.
OFF_LIST_BLACKLIST = {
    "canada par", "vitesse par", "the minimum", "the major", "the maximum",
    "flour medium", "parks ontario",
}


# Prepositions. Everything up to and including the last one is cut from a
# candidate common name, because "context of lake whitefish" is really "lake
# whitefish". English and French.
# English only. The French de/des/du/dans/sur were tried and removed: they are
# part of the name, not noise in front of it, so cutting at them turned
# "crabe des neiges" into "neiges" and "l'ammophile des sables" into "sables".
PREPOSITIONS = {
    "of", "in", "from", "for", "with", "on", "to", "at", "by",
}

# A common name does not end in one of these. They mark a place, so
# "Lawrence Estuary" in front of a bracketed genus is a location, not a name for
# an animal.
GEOGRAPHIC_NOUNS = {
    "estuary", "lake", "river", "bay", "creek", "island", "park", "region",
    "county", "coast", "basin", "watershed", "sound", "strait",
}

# Words that are never a common name on their own. A candidate is thrown away
# when EVERY word in it is on this list, so "black" goes and "black bear" stays.
# Built from what actually turned up in the mined map rather than guessed: the
# number after each group is how many no-taxon records that word was matching.
COMMON_NAME_STOPWORDS = {
    # sentence residue the boundary rule left behind
    "is", "which", "non", "species", "diversity", "local", "link", "days",
    "host", "culture", "pairs", "sorted", "interspecific", "bacterial",
    "native", "introduced", "wild", "fin", "related", "certaines", "espèces",
    "pour", "biological", "sciences",
    # directions and colours, which only ever qualify a name. Only the ones
    # that actually fire are listed: a rule that never fires cannot be told
    # apart from a broken one later.
    "northern", "eastern", "north", "white", "black", "blue", "brown", "grey",
    "vert",
    # places
    "usa", "europe", "canada", "america", "american",
}

# Botanists are credited in the same brackets that hold common names:
# "Picea glauca (Moench) Voss", and "L." is Linnaeus. Without this the map
# gained "moench" and "l." as if they were names for a tree.
AUTHORITY_ABBREVIATIONS = {
    "l.", "dc.", "michx.", "nutt.", "hook.", "pursh", "raf.", "vahl",
    "willd.", "moench",
}

# Words that follow a one-word name when it is really part of a place: "Trout
# River", "Elk Island". Built from what actually follows a match in this corpus,
# not from a general list of geographic words -- bay, valley, park, sound,
# harbour, pass and ridge were checked and never occur, so they are not here.
# Counts across the records a one-word name would rescue: river 29, lake 20,
# creek 20, island 20, fraser 19 (as in Simon Fraser).
GEOGRAPHIC_FOLLOWERS = {"river", "lake", "creek", "island", "fraser"}

# Mined entries that are not names for a living thing at all. They are dropped
# from the seventh tier AND from the map, because the map doubles as the synonym
# list for dashboard search and a place name is no use there either. Scored
# against 1,102 hand-judged records: together these removed 18 wrong matches and
# cost nothing -- no record that was judged right lost its tag.
NOT_ORGANISM_NAMES = {
    # a place. It was mined from "... de l'estuaire du Saint-Laurent (Genus
    # species)" and then matched every St. Lawrence record: 14 wrong matches,
    # the single largest cause.
    "l'estuaire du saint-laurent",
    # says where a creature lives, not what it is. All 4 records it rescued were
    # wrong matches. "graminoids" was checked the same way and kept: it rescued
    # 8 records with no wrong matches, so a coarse plant-group tag is earning
    # its place even though it is not a species name.
    "epibenthic",
    # the other half of the same accident. Both were mined from one sentence in
    # one ecotoxicology paper -- "an epibenthic (Hyalella azteca) and
    # endobenthic (Tubifex tubifex) invertebrate" -- which is a choice of test
    # animals, not a definition. Living in the sediment is not a synonym for one
    # worm. It tagged a single record in the whole corpus, and that record
    # already names both species properly in species_found, so removing it
    # scored 0 on every count.
    "endobenthic",
    # where a herd lives, not what it is called. Mined from "belugas from the
    # St. Lawrence Estuary population (Delphinapterus leucas)" -- the same
    # failure as the entry above. It tags 3 records in the whole corpus and none
    # of the judged ones, so removing it scored 0 on every count; it is here to
    # keep the map honest, not to fix anything.
    "lawrence estuary population",
}

# Names switched off while it is undecided whether crop records belong in the
# dashboard at all. NOT deleted, and deliberately still in the map, because a
# crop name is a perfectly good dashboard synonym -- emptying this set turns
# them back on. Measured over 1,102 hand-judged records, neither has EVER been
# right:
#   coffee   8 records, 8 wrong, 0 right, 0 scope -- six household food
#            expenditure surveys, a Yukon gold deposit named Coffee, and a
#            study of ants living in coffee plantations
#   potato   8 records, 4 wrong, 0 right, 4 scope -- soil-survey and phenology
#            records mentioning potato planting; the 4 scope records are real
#            potato-as-a-crop material that a decision to include crops would
#            bring back
DISABLED_PENDING_SCOPE = {"coffee", "potato"}

# Words that name a kind of creature and so usually finish a longer name:
# "Colorado potato BEETLE", "walleye POLLOCK". When a one-word name is followed
# by one of these the match is a slice out of the middle of some other
# creature's name, so it is thrown away. Scored on the judged records: 4 wrong
# matches removed, nothing judged right lost.
ORGANISM_HEAD_NOUNS = {
    "beetle", "beetles", "leafhopper", "leafhoppers", "nematode", "nematodes",
    "cyst", "pollock", "moth", "moths", "weevil", "weevils", "borer", "borers",
    "aphid", "aphids", "midge", "midges", "mite", "mites", "louse", "lice",
    "wasp", "wasps", "worm", "worms",
}

# One-word names allowed into the seventh tier. Each was checked for how often
# it lands inside a place name; anything above roughly one in ten is left out
# and listed in the report instead. "trout" is the clearest rejection: 37 of its
# 47 matches are Trout River, Trout Lake or Trout Creek.
SINGLE_WORD_COMMON_NAMES = {
    "caribou", "moose", "seagrass", "eelgrass", "bison", "coyote", "canola",
    "soybean", "birch", "potato", "aspen", "wolves", "sunflower", "coffee",
    "beluga", "capelin", "zostère", "guppies", "woodpecker", "alfalfa",
    "douglas-fir", "duckweed", "muskox", "raccoon", "larch", "lynx", "walleye",
    "earthworms", "pumpkinseed", "reindeer", "sablefish", "bobcat", "cheetah",
    "dandelion", "redfish", "sanderling", "turbot", "walrus", "kokanee",
    "bullsnake", "clubroot", "cougars", "golden-crowned-kinglets",
    "houndstongue", "lionfish", "merlin", "muskoxen", "ovenbird", "pronghorn",
    "puma", "quatre-temps", "ragweed", "salmonberry", "tamarack", "thyme",
    "tête-de-boule", "yellowhammer",
    # generic rather than a species name, but it only appears in biological
    # writing, so it still marks a record as being about living things. It
    # rescued 8 judged records with no wrong matches. "epibenthic" and
    # "endobenthic" used to sit here and were both removed: see
    # NOT_ORGANISM_NAMES.
    "graminoids",
}

# Words that should not start a common name. "of the lake whitefish" is really
# just "lake whitefish". English and French, because much of this corpus is
# French.
FUNCTION_WORDS = {
    "of", "the", "in", "for", "and", "from", "on", "to", "a", "an", "with",
    "des", "de", "du", "la", "le", "les",
}

# Words too common to say anything about what a record is about. Used only for
# the summary of titles that matched nothing.
STOPWORDS = set("""
the of and to in a for is are on with by this that as from at be or an
data dataset study used using we our results was were which it its
been has have had can may also more not their they these such between
de la le les des et en un une pour dans sur par au aux du ce cette est
sont ont ete plus ou qui se ne pas avec comme leur
""".split())


def load_name_sets(species_csv):
    """Read the BC species list and build the five sets of names to look for.

    Returns species (full binomials), genera, epithets (the second word of a
    binomial), families, and higher (order, class, phylum and kingdom together).
    Everything is kept exactly as written, capitals included, because that is
    how the text will be searched.
    """
    df = pd.read_csv(species_csv)
    species = set(df["species"].dropna())
    genera = set(df["genus"].dropna())
    epithets = {s.split()[1] for s in species if len(s.split()) == 2}
    families = set(df["family"].dropna())
    higher = (set(df["order"].dropna()) | set(df["class"].dropna())
              | set(df["phylum"].dropna()) | set(df["kingdom"].dropna()))
    return species, genera, epithets, families, higher


def report_overlaps(genera, epithets, families, higher):
    """List names that appear in more than one of the name sets.

    A name in two sets has to be assigned to one tier or the other, so rather
    than choosing quietly this prints them and says which tier wins.
    """
    overlaps = []
    for a_name, a, b_name, b in [
        ("genera", genera, "families", families),
        ("genera", genera, "higher", higher),
        ("families", families, "higher", higher),
        ("epithets", epithets, "genera", genera),
    ]:
        for name in sorted(a & b):
            overlaps.append((name, a_name, b_name))
    return overlaps


def record_text(title, subjects, abstract):
    """Join a record's fields into one string, keeping the original capitals.

    Lists of subjects are flattened. Nothing is lowercased and nothing is
    masked, because capitals are what tell a genus from an ordinary word.
    """
    parts = []
    for value in (title, subjects, abstract):
        if value is None:
            continue
        if isinstance(value, float):        # an empty field arrives as a number
            continue
        if hasattr(value, "tolist"):
            parts.extend(str(x) for x in value.tolist())
        elif isinstance(value, (list, tuple)):
            parts.extend(str(x) for x in value)
        else:
            parts.append(str(value))
    return " ".join(parts)


def find_taxa(text, names):
    """Find every taxon name in one record's text, sorted into six tiers.

    Returns a dict of six lists. A name is only counted once per tier, and a
    word already used as part of a binomial is not offered again as a bare
    genus, so "Apis mellifera" appears as a species and not also as the genus
    Apis.

    The test that decides a binomial is the EPITHET, not the genus: "Apis
    mellifera" is kept because "mellifera" is a known epithet, while "Beta
    diversity" is rejected because "diversity" is not. The cost of that rule is
    that a real species whose epithet is absent from the BC list, such as
    "Betula alleghaniensis", is missed.
    """
    species, genera, epithets, families, higher = names
    found = {k: [] for k in ("species_found", "species_off_list",
                             "genus_qualified", "family_found",
                             "higher_taxa_found", "genus_bare")}
    used = []                     # character spans already claimed

    def claim(start, end):
        used.append((start, end))

    def taken(start, end):
        return any(s < end and start < e for s, e in used)

    # "Vulpes sp." first, so its genus is not offered again on its own.
    for m in GENUS_QUALIFIED.finditer(text):
        if m.group(1) in genera:
            found["genus_qualified"].append(f"{m.group(1)} {m.group(2)}")
            claim(m.start(), m.end())

    # Then two-word names. A pair that fails both tests claims nothing, so its
    # capitalised word can still be picked up as a family or genus below.
    for m in BINOMIAL.finditer(text):
        if taken(m.start(), m.end()):
            continue
        genus, epithet = m.group(1), m.group(2)
        pair = f"{genus} {epithet}"
        if pair in species:
            found["species_found"].append(pair)
            claim(m.start(), m.end())
        elif epithet in epithets and pair.lower() not in OFF_LIST_BLACKLIST:
            # The genus is deliberately NOT checked here. This tier exists to
            # catch species that are missing from the BC list, and a species
            # missing from that list has a genus missing from it too --
            # Pagophilus, Parus and Poecilia are all absent from the genus set
            # and all three are real animals. Requiring a known genus was tried
            # and removed: it threw away 2,140 mentions to catch about 91 bad
            # ones. The bad ones are named individually instead.
            found["species_off_list"].append(pair)
            claim(m.start(), m.end())

    # Finally single capitalised words that nothing has claimed.
    for m in CAP_WORD.finditer(text):
        if taken(m.start(), m.end()):
            continue
        word = m.group(1)
        if word in families:
            found["family_found"].append(word)
        elif word in higher:
            found["higher_taxa_found"].append(word)
        elif word in genera:
            found["genus_bare"].append(word)

    # If a genus already appears as part of a species or a "Vulpes sp.", drop it
    # from the bare-genus tier. Otherwise a record saying "Apis mellifera" would
    # report both the species and the genus Apis, which is the same finding
    # twice, and it would happen again for every later mention of Apis alone.
    named_genera = {n.split()[0] for n in (found["species_found"]
                                           + found["species_off_list"]
                                           + found["genus_qualified"])}
    found["genus_bare"] = [g for g in found["genus_bare"] if g not in named_genera]

    # Keep each name once per tier, in the order it first appeared.
    return {k: list(dict.fromkeys(v)) for k, v in found.items()}


def scientific_name_in(fragment, names):
    """Return the SPECIES name in this fragment, or None if there is not one.

    Only a two-word species counts here, either one on the BC list or one whose
    genus and epithet are both recognised. A bare genus is not enough: accepting
    it turned "Lawrence Estuary (Pandalus ...)" into a common name for a place.
    """
    hits = find_taxa(fragment.strip(), names)
    for tier in ("species_found", "species_off_list"):
        if hits[tier]:
            return hits[tier][0]
    return None


def clean_common_name(raw):
    """Tidy the words before a bracket into a common name, or return None.

    The words arrive as whole tokens, so nothing is ever cut mid-word. Then:

      - everything up to and including the last preposition is thrown away,
        which turns "context of lake whitefish" into "lake whitefish"
      - at most four words are kept
      - leading function words go, so "the tufted puffin" becomes "tufted puffin"
      - a candidate ending in a place word is rejected outright, because
        "Lawrence Estuary" in front of a bracketed name is a location
    """
    words = raw.split()

    def bare(word):
        return word.lower().strip(".,;:'’-")

    cut = [i for i, w in enumerate(words) if bare(w) in PREPOSITIONS]
    if cut:
        words = words[cut[-1] + 1:]
    words = words[-4:]
    while words and bare(words[0]) in FUNCTION_WORDS:
        words.pop(0)
    if not words:
        return None
    if bare(words[-1]) in GEOGRAPHIC_NOUNS:
        return None
    name = " ".join(words).strip(" -'’").lower()
    if not name:
        return None
    # An author credit is not a common name.
    if name in AUTHORITY_ABBREVIATIONS or name.endswith(".") or len(name) <= 2:
        return None
    # Nothing but filler words.
    if all(bare(w) in COMMON_NAME_STOPWORDS for w in name.split()):
        return None
    return name


def mine_common_names(text, names):
    """Find "common name (Scientific name)" pairs, and the reverse, in one text.

    Returns a list of (common_name, scientific_name, example) tuples. A pair is
    only kept when the half meant to be scientific really is a taxon name, which
    throws away ordinary brackets like "(2015)" or "(see Appendix)".
    """
    pairs = []
    for m in BRACKET.finditer(text):
        inner = m.group(1).strip()
        before = text[:m.start()]
        window = before[-120:]
        example = text[max(0, m.start() - 60):m.end() + 10].replace("\n", " ")

        # common name (Scientific name)
        sci = scientific_name_in(inner, names)
        if sci:
            wb = WORDS_BEFORE.search(window)
            if wb:
                common = clean_common_name(wb.group(1))
                if common:
                    pairs.append((common, sci, example))
            continue

        # Scientific name (common name)
        wb = WORDS_BEFORE.search(window)
        if wb:
            sci = scientific_name_in(wb.group(1), names)
            if sci:
                common = clean_common_name(inner)
                if common:
                    pairs.append((common, sci, example))
    return pairs


def common_name_tier(texts, pair_counts, min_seen=2):
    """Find the mined common names in each record's text.

    A name is used only if it was mined at least `min_seen` times. Names of more
    than one word are taken as they come; one-word names are the dangerous group
    ("trout" also means Trout Lake) so only those on the checked list get in.
    Hand-judging 1,102 of the records this tier rescues bore that out: one-word
    names are wrong about four times as often as longer ones.

    Two kinds of near-miss are thrown away after a match is found, both because
    the name turned out to be part of a longer phrase: a place ("Trout Lake")
    and another creature's name ("Colorado potato beetle").

    Matching ignores case, because a common name is written lowercase in a
    sentence and capitalised in a title, so case carries no information. This is
    the opposite of the scientific names, where capitals are the whole point.

    A WARNING FOR WHOEVER READS THIS NEXT. It is tempting to treat a record that
    has both a common name and a bare genus as better evidence than one with a
    common name alone -- two weak signals agreeing. It is not. Measured by hand
    on the 68 such records: the wrong-match rate is roughly DOUBLE, 14.7%
    against 6.7% (n=68, p=0.015 -- a real effect, but a small sample). The two
    tiers fail on the same records rather than independently, because genus_bare
    fires on Barbara, Beta, Erica and Columba, which are ordinary words in
    non-biological writing -- exactly the writing where a stray common name also
    turns up. Those 68 stay in common_name_found and are not given a tier of
    their own; they can be picked out from the existing columns whenever they
    are wanted (common_name_found filled, the five real scientific tiers empty,
    genus_bare filled).
    """
    seen_enough = {name for (name, _), n in pair_counts.items() if n >= min_seen}
    multi = {n for n in seen_enough if " " in n}
    single = {n for n in seen_enough
              if " " not in n and n in SINGLE_WORD_COMMON_NAMES}
    names = sorted((multi | single) - NOT_ORGANISM_NAMES - DISABLED_PENDING_SCOPE,
                   key=len, reverse=True)
    if not names:
        return [[] for _ in texts], names
    # The word after the match is looked at but NOT eaten. It used to be eaten,
    # which silently hid any name starting at that word: in "moose Bighorn
    # Sheep" only "moose" was ever found. Peeking instead of eating recovered a
    # missing name on 39 of the 1,102 hand-judged records and lost nothing.
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in names)
                         + r")(?!\w)(?:(?=\W+(\w+)))?", re.IGNORECASE)
    found = []
    for text in texts:
        hits = []
        for m in pattern.finditer(text):
            name = m.group(0).lower()
            following = (m.group(1) or "").lower()
            if " " not in name:
                # "Trout Lake" is a place, not a fish.
                if following in GEOGRAPHIC_FOLLOWERS:
                    continue
                # "potato" in "Colorado potato beetle" is a slice out of the
                # middle of another creature's name.
                if following in ORGANISM_HEAD_NOUNS:
                    continue
            hits.append(name)
        found.append(list(dict.fromkeys(hits)))
    return found, names


def merge_plurals(pair_counts):
    """Count "tree swallow" and "tree swallows" as one name.

    Two entries are merged only when they point at the SAME scientific name and
    differ by nothing more than a trailing s on their last word. The singular
    form is kept and the counts are added together. Tying it to the scientific
    name is what stops unrelated names being folded into each other.

    Returns the merged counter and how many entries were folded away.
    """
    merged = collections.Counter()
    folded = 0
    for (common, sci), n in pair_counts.items():
        words = common.split()
        singular = common
        if words and words[-1].endswith("s") and len(words[-1]) > 3:
            candidate = " ".join(words[:-1] + [words[-1][:-1]])
            if (candidate, sci) in pair_counts:
                singular = candidate
                folded += 1
        merged[(singular, sci)] += n
    return merged, folded


def annotate(df, names):
    """Add the six taxon columns to every record, and collect the common names.

    Returns the annotated frame and a counter of the common-name pairs seen
    across the whole corpus.
    """
    columns = collections.defaultdict(list)
    pair_counts = collections.Counter()
    pair_example = {}
    texts = []

    for title, subjects, abstract in zip(df["title"], df["subjects"],
                                         df["abstract"]):
        text = record_text(title, subjects, abstract)
        texts.append(text)
        hits = find_taxa(text, names)
        for tier, value in hits.items():
            columns[tier].append(value)
        for common, sci, example in mine_common_names(text, names):
            pair_counts[(common, sci)] += 1
            pair_example.setdefault((common, sci), example)

    out = df.copy()
    for tier, values in columns.items():
        out[tier] = values
    return out, pair_counts, pair_example, texts


def main():
    parser = argparse.ArgumentParser(
        description="Note which taxon names each biodiversity candidate mentions."
    )
    parser.add_argument(
        "--candidates", default="data/lunaris_biodiv_candidates.parquet",
        help="Path to the biodiversity candidates.")
    parser.add_argument(
        "--species", default="/Users/lucia/Desktop/BCBN/Results/bc_species_summary.csv",
        help="Path to the BC species summary.")
    parser.add_argument(
        "--out", default="data/lunaris_taxa.parquet",
        help="Where to write the annotated records.")
    parser.add_argument(
        "--common-out", default="data/lunaris_common_name_map.csv",
        help="Where to write the common-name map.")
    parser.add_argument(
        "--none-out", default="data/lunaris_no_taxon.csv",
        help="Where to write the records that matched nothing.")
    args = parser.parse_args()

    started = time.perf_counter()
    print(f"Candidates: {args.candidates}")
    print(f"Species:    {args.species}")

    names = load_name_sets(args.species)
    species, genera, epithets, families, higher = names
    print(f"\nNames loaded: species {len(species):,}, genera {len(genera):,}, "
          f"epithets {len(epithets):,}, families {len(families):,}, "
          f"higher {len(higher):,}")

    overlaps = report_overlaps(genera, epithets, families, higher)
    print(f"Names in more than one set: {len(overlaps)}")
    for name, a, b in overlaps:
        print(f"   {name} is in both {a} and {b}")

    df = pd.read_parquet(args.candidates)
    print(f"\nRead {len(df):,} records.")

    out, pair_counts, pair_example, texts = annotate(df, names)

    # The common-name map has to exist before it can be matched back, so the
    # pairs are merged first and the seventh tier is a second pass.
    pair_counts, merged_count = merge_plurals(pair_counts)
    found, tier_names = common_name_tier(texts, pair_counts)
    out["common_name_found"] = found
    print(f"\nCommon names used by the seventh tier: {len(tier_names):,} "
          f"(more than one word, seen at least twice)")

    tiers = ["species_found", "species_off_list", "genus_qualified",
             "family_found", "higher_taxa_found", "genus_bare"]
    print("\nRecords mentioning at least one name, by tier:")
    for tier in tiers + ["common_name_found"]:
        n = int(out[tier].apply(bool).sum())
        print(f"{n:>8,}  {100 * n / len(out):5.1f}%  {tier}")

    # genus_bare is the weakest tier -- its commonest entries are Beta,
    # Argentina, Pan and Cancer, none of which is really about an organism. So
    # "found nothing" is decided on the other five, and the figure including it
    # is printed alongside for comparison.
    strong_tiers = [t for t in tiers if t != "genus_bare"]
    without_cn = out[strong_tiers].apply(lambda row: not any(row), axis=1)
    nothing = out[strong_tiers + ["common_name_found"]].apply(
        lambda row: not any(row), axis=1)
    nothing_all = out[tiers + ["common_name_found"]].apply(
        lambda row: not any(row), axis=1)
    print(f"\n{int(without_cn.sum()):,} records matched nothing "
          f"({100 * without_cn.sum() / len(out):.1f}%)  "
          f"[no common names, genus_bare not counted]")
    print(f"{int(nothing.sum()):,} records matched nothing "
          f"({100 * nothing.sum() / len(out):.1f}%)  "
          f"[with common names, genus_bare not counted]")
    print(f"{int(nothing_all.sum()):,} records matched nothing "
          f"({100 * nothing_all.sum() / len(out):.1f}%)  "
          f"[with common names, genus_bare counted]")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    out.to_parquet(args.out, index=False)

    # already merged above, before the seventh tier was matched
    print(f"Singular/plural pairs merged: {merged_count:,}")
    # Entries that name a place or a way of life rather than a creature are of
    # no use to dashboard search either, so they leave the map as well as the
    # seventh tier.
    dropped_rows = sum(1 for (c, _) in pair_counts if c in NOT_ORGANISM_NAMES)
    rows = [{"common_name": c, "scientific_name": s, "times_seen": n,
             "example": pair_example.get((c, s)) or pair_example.get((c + "s", s))}
            for (c, s), n in pair_counts.items() if c not in NOT_ORGANISM_NAMES]
    print(f"Map rows dropped as not a creature's name: {dropped_rows}")
    common = pd.DataFrame(rows).sort_values(
        ["times_seen", "common_name"], ascending=[False, True])
    common.to_csv(args.common_out, index=False, encoding="utf-8-sig")
    print(f"Common-name pairs: {len(common):,}")

    # records with nothing
    none_cols = ["id", "doi", "title", "publisher", "matched_keywords", "abstract"]
    # "nothing" now includes the seventh tier as a find.
    none_df = out.loc[nothing, none_cols].copy()
    none_df["matched_keywords"] = none_df["matched_keywords"].apply(
        lambda v: "; ".join(v.tolist() if hasattr(v, "tolist") else list(v)))
    none_df.to_csv(args.none_out, index=False, encoding="utf-8-sig")

    words = collections.Counter()
    for title in none_df["title"].fillna(""):
        for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", str(title).lower()):
            if w not in STOPWORDS:
                words[w] += 1
    print("\nCommonest words in the titles that matched nothing:")
    for word, n in words.most_common(40):
        print(f"{n:>6,}  {word}")

    print(f"\nDone. {len(out):,} records written in "
          f"{time.perf_counter() - started:.1f}s")
    print(f"File: {args.out}")
    print(f"File: {args.common_out}")
    print(f"File: {args.none_out}")


if __name__ == "__main__":
    main()
