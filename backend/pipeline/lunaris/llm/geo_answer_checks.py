"""
Checks our code runs on a geography pass 1 answer, whatever the prompt
version.

The model is asked to list place names that are written in the record. These
checks hold it to that:

  - written_in_record: is a text (an as_written) found in the record's own
    text, that is, the record without our "Note from us" lines? A place whose
    as_written is not found was taken from somewhere else (the instructions,
    our notes) or reworded, and from version 3 it is dropped.
  - name_needs_a_look: is a place's standard name either found in the
    record's own text, or plainly an expansion of its as_written ("Mt." to
    "Mount"), or the British Columbia reading rule from the instructions? If
    none of these, a person should look at it: it may be a fair translation,
    or a name the model brought in from elsewhere.
  - drop_duplicates: one place per name and role.

All matching ignores case and runs of spaces or line breaks.
"""

import re

# The reading rule given in every version's instructions: these, written in
# the record, mean British Columbia.
BC_FORMS = {"bc", "b.c.", "c.-b.", "colombie-britannique"}


def squash(text):
    """Lower-case a text and turn every run of spaces or line breaks into one space, for matching."""
    return re.sub(r"\s+", " ", text).strip().lower()


def own_text(prompt):
    """
    Return the record's own text from a prompt: everything after "Record to
    read:", without our notes. Every version lays the record out the same way
    (geo_record_text.record_text), so this works for all of them.
    """
    record = prompt.split("Record to read:", 1)[1]
    return "\n".join(line for line in record.splitlines() if not line.startswith("Note from us"))


def written_in_record(text, prompt):
    """Return True if the text appears in the record's own text (case and spacing ignored)."""
    return squash(text) in squash(own_text(prompt))


def is_short_form(short, word):
    """
    Return True if short could be an abbreviation of word: the same first
    letter, and its other letters found in word in the same order ("mt" for
    "mount", "prov" for "provincial", "pk" for "park").
    """
    short, word = short.strip(".").lower(), word.lower()
    if not short or not word or short[0] != word[0]:
        return False
    rest = iter(word[1:])
    return all(letter in rest for letter in short[1:])


def is_expansion(as_written, name):
    """
    Return True if name is as_written with abbreviations spelled out: the
    same number of words, each word of as_written the same as, or a short
    form of, the word in the same place in name ("L. Examplia" and "Lake
    Examplia").
    """
    short_words, long_words = as_written.replace("-", " ").split(), name.replace("-", " ").split()
    return len(short_words) == len(long_words) and all(
        is_short_form(s, w) for s, w in zip(short_words, long_words))


def name_needs_a_look(place, name, prompt):
    """
    Return True if a place's standard name should be looked at by a person:
    it is not in the record's own text, not an expansion of the as_written,
    and not British Columbia given for one of the BC forms the instructions
    explain. Translations land here too, since code cannot judge them.
    """
    if not name.strip():
        return True
    if written_in_record(name, prompt) or is_expansion(place["as_written"], name):
        return False
    return not (squash(name) == "british columbia" and squash(place["as_written"]) in BC_FORMS)


def drop_duplicates(places, name_of):
    """
    Return the places with repeats removed: the first place for each name
    and role (case and spacing ignored) is kept, in the order given. name_of
    reads a place's standard name, since the field is called differently in
    version 1.
    """
    kept = {}
    for place in places:
        kept.setdefault((squash(name_of(place)), place["role"]), place)
    return list(kept.values())
