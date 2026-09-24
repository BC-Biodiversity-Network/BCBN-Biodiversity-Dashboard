# What counts as biodiversity data

The rules the Lunaris filter is measured against. Every line here was settled
by a person, and the note at the end of each section says who and when. Nothing
in this document was inferred by a model.

These rules decide the `is_biodiversity` column in `test_set_reviewed.csv`, and
they are the same rules `decide()` in `score_trial.py` applies to what a model
reports. If a rule changes, both have to change.

---

## The five base criteria

1. **Purely physical environmental data is NO** unless the record itself
   mentions living things. CTD casts, bathymetry, wave height, seismic
   surveys, weather.

2. **Forestry, agriculture and fisheries data is YES only when the record is
   about the organisms themselves.** Tenure boundaries, harvest volumes, farm
   income and licensing are NO.

3. **Park and protected area boundaries, land cover and topographic maps are
   YES only when an ecological or conservation purpose is stated.**

4. **Government administrative records are NO.**

5. **Human health and biomedical records are NO** unless the subject is wild
   organisms or ecosystems.

*Set before the labelling round. Applied by Claude Opus 5 to all 900 records
in the test set.*

---

## Whole categories

**Out of scope**

- **Experimental animals.** Lab mice, fruit fly behaviour studies,
  experimental E. coli populations. This also covers a record that is an
  experiment run *on* farmed animals, see the note under farmed organisms.
- **Extinct species.** This took out an ancient DNA study of Ice Age deer.

**In scope**

- **Farmed and cultivated organisms.** Crop variety trials, aquaculture stock.
  Being farmed rather than wild does not by itself put a record out of scope.
- **Microbes.** Soil microbial communities, cyanobacterial blooms in a
  eutrophic lake, litter decomposition.

**The line between farmed organisms and experimental animals.** A record about
farmed organisms is in scope, but a record that is an experiment on those
animals is not. A broiler chicken walking-ability trial reads as an
experimental animal study, not as agriculture. Evan: *"i think fine for now"*.

*Lucia proposed these lines in Slack, Evan agreed 2026-09-24. The broiler
chicken exception was raised by Evan the same day and agreed by both.*

---

## Seven boundary questions

Lucia reviewed 259 records by hand and settled all but 32. The 32 were not
unclear records, they were places where the criteria above did not say which
way to go. Evan answered all seven questions.

### 1. Wildlife management boundaries that name no species

**NO.** Trapline polygons, game management areas, outfitting concessions, a
fish and wildlife compensation programme boundary, counts of people who hunted
or fished. These exist in order to manage wildlife but they record no species,
no count and no location of an animal. *5 of 5 records.*

### 2. Forest management boundaries that name no species

**NO.** Forest tenure and cutblock polygons, national managed forest extent, a
mountain pine beetle salvage area, historical timber berths, a forest visual
quality indicator. Being named after an organism is not the same as carrying
information about it. *6 of 6 records.*

### 3. Farmed and cultured organisms

**It depends on what else the record is.** Being farmed is not disqualifying,
but the record still has to clear the other criteria.

- Marine aquaculture lease polygons whose keywords name the cultured species:
  **YES**
- A dairy cow feeding return-on-investment spreadsheet: **NO**, farm income
  under criterion 2
- A registry of licensed beekeepers and apiary locations: **NO**, licensing
  under criterion 2
- A broiler chicken leg health experiment: **NO**, experimental animal

### 4. Physical data collected by a biological programme

**NO.** Wave height, current speed, CTD casts, glider hydrography, moorings,
sea ice extent, freshwater quality, a climate station series. It does not
matter that a fisheries department collected them, that the programme
describes itself as biological, or that the results are reported by marine
bioregion. The record itself has to carry biological content.

Evan's words, which say this better than criterion 1 does:
**"environmental data not ecological"**. *9 of 9 records.*

### 5. Imagery that might contain organisms

Under-ice ROV video. The metadata describes sea ice physics and mentions no
organisms, but organisms are likely to be in the footage.

Evan on one of the two: *"no (so it is a yes but super not worth including at
this point)"*. **That is a cost call, not a scope call.** Both records are
labelled NO so the corpus reflects what will actually be included, and the
`decision_note` column marks the one he called a yes.

This question is wider than these two records. The filter only ever sees the
title, the subject keywords and the abstract, so there is a class of record
that cannot be judged from metadata by anything, model or person. Lucia
flagged 12 such records across the review. Revisit if full-text or file-level
inspection becomes available.

### 6. An ecological purpose stated by the programme, not the record

**NO.** A linear disturbance layer mapping roads and pipelines, built for a
review of potential ecological responses. A spatial layer of areas recommended
as protected areas.

Criterion 3 says boundaries count when an ecological purpose is stated. This
narrows it: the purpose has to be visible in what the record contains, not
only in the programme that produced it. *2 of 2 records.*

### 7. One-offs

- **DFO catch and effort statistics, 1998: YES.** Landings broken down by
  species group. **This refines criterion 2.** "Harvest volumes are NO" means
  aggregate tonnage and value with no species information. Once the numbers
  are broken down by species they say which organisms were taken and in what
  quantity, which is information about the organisms.
- **Avian influenza forecasting: NO.** Tracks a bird disease, but the inputs
  are web searches, news and weather rather than bird data.
- **Keno Hill silver deposit interpretive story: NO.** Mentions relict ice age
  plants and birdsong in passing.
- **Synchrotron imaging of hunting bullet fragmentation: NO.** Framed as human
  lead exposure.

*Answered by Evan 2026-09-23, on the 32 records in the review that Lucia could
not settle.*

---

## The one test behind all of it

Reading the seven answers together, they say the same thing:

> **Does the record tell you something about organisms in the environment,
> what they are, where they are or how many there are, or does it only tell
> you what people did around them?**

Legal boundaries, licences, income and tenure are all the second kind. So is
physical data, however biological the programme that gathered it. Evan's
phrase for this is **"environmental data not ecological"**.

This is a summary of his rulings rather than a rule he stated, so where it
disagrees with one of the seven answers above, the answer wins.

---

## Still open

- **The ROV records.** In scope, excluded on cost. If the cost of processing
  video changes, these come back.
- **Records that metadata cannot settle.** 12 flagged. No filter reading title,
  subjects and abstract can reach them.
- **Why aquaculture leases are in and the beekeeper registry is out.** Both are
  licensing records that name a location and an organism. Criterion 2 explains
  the split, but it is worth confirming with Evan that the split is intended
  before applying it to the full corpus.
