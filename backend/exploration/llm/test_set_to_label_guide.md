# How to fill in the labelling sheet

Hide the `stratum` column before you start. It says whether the keyword
filter kept or dropped each record, and seeing it will pull your judgement
towards agreeing with the filter. The whole point is an independent answer.

## The columns

**is_biodiversity** — `yes` or `no`.
Is this dataset about living things, ecosystems, or biodiversity?
If no, leave the rest of the row blank and move on.

**subject** — only when is_biodiversity is yes. One of:

  - `species`
  - `habitat`
  - `forestry`
  - `agriculture`
  - `genetic`
  - `environment`
  - `other`

**form** — only when is_biodiversity is yes. One of:

  - `dataset`
  - `report`
  - `policy`
  - `other`

**organism** — whatever organism the record is about, in any language.
Write what comes to mind. Do not look anything up, a later script
matches whatever you write against the BC species list.

**notes** — anything you were unsure about.
The unsure ones are where the interesting disagreements show up later,
and they are also what you take to Evan when you need a scope decision.

## Advice

Consistency matters more than getting every hard case right. If you find
yourself changing your mind about a whole category halfway through, write
it in notes rather than silently switching, so the change can be found.

When a record is genuinely borderline, say so in notes instead of forcing
a yes or no. A record marked unsure is information. A record forced into
the wrong box is noise.

## What is in this sample

  kept_nothing_found: 500
  dropped: 400

The dropped pool is the important one. Most of what the keyword filter
threw away really is unrelated, so this sample is there to find the rare
mistakes. Read the result as 'are there misses at all, and roughly how
common', not as a precise rate.
