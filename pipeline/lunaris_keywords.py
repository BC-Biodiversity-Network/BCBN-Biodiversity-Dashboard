"""
lunaris_keywords.py

Single source of truth for the Lunaris biodiversity keyword filter.

Everything in exploration/lunaris/ imports from here. Each of those scripts
used to keep its own copy of the keyword list, so a fix in one never reached
the others.

How a record is judged:

  1. mask_false_positives() blanks out phrases where a keyword is not being
     used biologically, before anything is matched.
  2. The keyword list, plus the plural forms built from it, is matched
     against what is left.
  3. NO_PLURAL holds the keywords that get no plural, either because the
     plural would be wrong (fungus/fungi) or because it brings in more
     non-biodiversity than biodiversity (tree/trees).

Matching is whole-word only, so a keyword does not catch its own plural:
"plant" never matched "plants". Plurals are therefore listed one at a time
rather than assumed, which keeps every added form reviewable.
"""

import re


STRONG_KEYWORDS = [
    "biodiversity", "species", "taxonomy", "taxonomic", "taxa", "taxon",
    "wildlife", "fauna", "flora", "vegetation", "habitat", "organism",
    "animal", "plant", "fish", "fishery", "fisheries", "bird", "avian",
    "seabird", "mammal", "cetacean", "whale", "insect", "beetle", "moth",
    "butterfly", "bee", "pollinator", "amphibian", "frog", "toad",
    "reptile", "snake", "lizard", "invertebrate", "mollusc", "mollusk",
    "crustacean", "plankton", "zooplankton", "phytoplankton", "benthic",
    "fungi", "fungal", "fungus", "algae", "algal", "moss", "lichen",
    "coral", "shrub", "grass", "tree", "salmon", "trout", "herring",
    "crab", "shrimp",
    "ecology", "ecological", "ecosystem", "conservation", "endangered",
    "threatened species", "invasive", "abundance", "occurrence", "biomass",
    "wetland", "estuary", "estuarine", "riparian", "intertidal", "reef",
    "ecotoxicology",
    "biodiversité", "espèce", "espèces", "faune", "flore",
    "poisson", "poissons", "oiseau", "oiseaux", "écologie",
]

# Phrases blanked out before matching, because the keyword inside them is not
# being used biologically. Never add one without checking it first with
# lunaris_check_false_positives.py.
#
# Each count below is how many records that phrase alone stops from being kept,
# with every other phrase already in place. All of them were read; none were
# biodiversity.
FALSE_POSITIVE_PATTERNS = [
    # "sea-bird" is masked because Sea-Bird Scientific is a CTD/sensor brand,
    # not a bird; real "seabird" / "sea birds" stay live. 791 records, and not
    # one of the 887 mentions meant the animal.
    (r"sea-bird",
     "Sea-Bird Scientific: CTD/oxygen sensor manufacturer, not a bird. "
     "887 occurrences, 0 in an animal sense, 791 records flipped."),

    # A power plant generates electricity, and in aviation tables "type of
    # power plant" means engine type. 23 records, verified.
    (r"power\s+plants?",
     "electricity generation, and in aviation stats 'type of power plant' "
     "means engine type. 23 records flipped, 0 biodiversity."),

    # Treatment works, abattoirs, refineries and the like: factories, not
    # vegetation. 53 records, verified.
    (r"(?:treatment|processing|manufacturing|industrial|nuclear|generating"
     r"|thermal|pilot|meat|packing|inspected)\s+plants?",
     "industrial facilities: water/sewage treatment, abattoirs, refineries. "
     "53 records flipped, 0 biodiversity."),

    # Drinking-water utilities, same idea as the industrial plants above.
    # 13 records, verified.
    (r"drinking\s+water\s+plants?",
     "water utilities. Same class as the industrial plants above. "
     "13 records flipped."),

    # "energy conservation" is energy policy, not habitat conservation. The
    # department name "Environment and Energy Conservation" is left alone,
    # since it is the only match on some real wildlife datasets. 11 records.
    (r"(?<!environment and )energy\s+conservation|conservation\s+of\s+energy",
     "energy policy, not habitat conservation. 11 records flipped, "
     "0 biodiversity once the department name is excepted."),

    # In StatCan's innovation tables a "plant" is a factory. 37 records, all
    # from that one series, verified.
    (r"percentage\s+of\s+plants|innovative\s+plants",
     "StatCan 'Innovation, logging and manufacturing industries' series, where "
     "a plant is a factory. 37 records flipped, all from that one series, "
     "0 biodiversity."),

    # An "invasive method" or "invasive test" is clinical or geotechnical
    # measurement, not an invasive species. 5 records, verified.
    (r"invasive\s+(?:method|technique|procedure|test|surgery|measurement)s?",
     "clinical and geotechnical measurement, not invasive species. "
     "5 records flipped, 0 biodiversity."),
]


# Keywords that get no plural, with the reason. Everything else is pluralised
# by pluralize(). Where a count is quoted, it comes from reading the records
# that plural would have added.
NO_PLURAL = {
    # not nouns
    "taxonomic": "adjective", "avian": "adjective", "fungal": "adjective",
    "algal": "adjective", "benthic": "adjective", "ecological": "adjective",
    "endangered": "adjective", "estuarine": "adjective",
    "riparian": "adjective", "intertidal": "adjective",
    "invasive": "adjective; 'invasives' as a noun is rare",

    # no sensible plural -- you don't count wildlife or plankton
    "biodiversity": "uncountable", "wildlife": "uncountable",
    "vegetation": "uncountable", "flora": "collective",
    "fauna": "collective", "plankton": "collective",
    "zooplankton": "collective", "phytoplankton": "collective",
    "ecotoxicology": "uncountable", "conservation": "uncountable",
    "biodiversité": "uncountable", "faune": "collective",
    "flore": "collective; 'flores' is also a place name and Spanish 'flowers'",
    "écologie": "uncountable",

    # already a plural, or spelled the same either way
    "species": "same form", "taxa": "already the plural of taxon",
    "taxon": "irregular plural 'taxa' is already a keyword",
    "fisheries": "already plural", "fungi": "already plural",
    "algae": "already plural", "espèces": "already plural",
    "poissons": "already plural", "oiseaux": "already plural",
    "salmon": "same form", "trout": "same form", "herring": "same form",
    "shrimp": "same form",
    "fungus": "irregular plural 'fungi' is already a keyword",
    "oiseau": "irregular plural 'oiseaux' is already a keyword",

    # the plural brings in more non-biodiversity than biodiversity
    "occurrence": "'occurrences' of events/failures dominates; 266 records, "
                  "almost none biological",
    "tree": "'trees' also means decision/phylogenetic/R-trees in maths, CS and "
            "statistics; of 153 records roughly half are non-biological",
    "abundance": "'abundances' is standard for isotopic and elemental abundances",
    "taxonomy": "'taxonomies' is used for classification schemes in any field",
    "ecology": "'ecologies' is rare and mostly figurative",
    "biomass": "'biomasses' is rare",
    "animal": "'animals' is dominated by lab-animal, companion-animal and "
              "livestock boilerplate ('all animals were housed...', dog breed "
              "behaviour, poultry); of 109 records only ~2 in 12 sampled were "
              "biodiversity",
    "organism": "'organisms' mostly appears in molecular and evolutionary "
                "biology of model organisms (Drosophila, protists, codon bias) "
                "rather than in biodiversity or ecology datasets",
}


def pluralize(word):
    """The plural of one keyword, or None if it should not get one."""
    if " " in word:                        # "threatened species"
        return None
    if word in NO_PLURAL:
        return None
    if re.search(r"(?:s|x|z|ch|sh)$", word):
        return word + "es"                 # moss -> mosses, fish -> fishes
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"           # estuary -> estuaries, butterflies
    return word + "s"


def plural_forms(words):
    """The plural forms added to the keyword list, in keyword order."""
    out, seen = [], set(words)
    for w in words:
        p = pluralize(w)
        if p and p not in seen:
            out.append(p)
            seen.add(p)
    return out


PLURAL_KEYWORDS = plural_forms(STRONG_KEYWORDS)
MATCH_KEYWORDS = STRONG_KEYWORDS + PLURAL_KEYWORDS


def compile_boundary(words):
    """Matcher for these words as whole words only, ignoring case.

    Whole-word means "plant" does not fire inside "plantation" -- nor inside
    "plants", which is why plurals are listed separately.
    """
    escaped = [re.escape(w) for w in words]
    return re.compile(r"(?<![a-zA-Z])(?:" + "|".join(escaped) + r")(?![a-zA-Z])",
                      re.IGNORECASE)


STRONG_RE = compile_boundary(MATCH_KEYWORDS)

# One matcher per keyword, so a keep decision can name the word that fired.
KEYWORD_RES = {kw: compile_boundary([kw]) for kw in MATCH_KEYWORDS}

# Singulars only, kept so the effect of adding the plurals can be measured.
SINGULAR_RE = compile_boundary(STRONG_KEYWORDS)

# Every masked phrase in one matcher.
FALSE_POSITIVE_RE = re.compile(
    "|".join(f"(?:{p})" for p, _ in FALSE_POSITIVE_PATTERNS), re.IGNORECASE)


def as_text(v):
    """Turn a title, subjects or abstract field into a plain string.

    These arrive as None, as an empty pandas value (which is a float, so a
    bare `x or ""` misses it), or as a list of subject strings.
    """
    if v is None:
        return ""
    if isinstance(v, float):     # an empty field arrives as a number
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in v)
    if hasattr(v, "tolist"):     # numpy array of subject strings
        return " ".join(str(x) for x in v.tolist())
    return str(v)


def mask_false_positives(text):
    """Blank out the non-biological phrases. They become spaces rather than
    being deleted, so the words either side are not glued together."""
    return FALSE_POSITIVE_RE.sub(lambda m: " " * len(m.group(0)), text)


def build_haystack(title, subjects, abstract):
    """The lowercased, masked text that one record is judged on."""
    raw = f"{as_text(title)} {as_text(subjects)} {as_text(abstract)}"
    return mask_false_positives(raw.lower())


def is_biodiversity(haystack):
    """True if any keyword survives in text from build_haystack()."""
    return bool(STRONG_RE.search(haystack))


def keyword_hits(haystack):
    """Which keywords fired, for explaining why a record was kept."""
    return [kw for kw, rx in KEYWORD_RES.items() if rx.search(haystack)]
