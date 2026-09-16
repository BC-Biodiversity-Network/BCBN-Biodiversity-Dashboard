"""
lunaris_semantic_review.py

Attach semantic (LLM) biodiversity judgments to sample_for_review.csv and
compare them against the keyword filter's decision.

Judgments were made by reading title + subjects + abstract of each of the 48
sampled records. Rule applied: YES if the dataset's *measured subject matter*
includes organisms, their populations/distributions, or the habitats and
ecosystems they occupy. NO if organisms/habitats are only incidental wording
(org names, "energy conservation", "power plant") or the subject is purely
physical, chemical, engineering, clinical, or socio-economic.
"""

import argparse

import pandas as pd

import sys
from pathlib import Path

# The filter itself is finalized and lives in pipeline/lunaris_keywords.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))

from lunaris_keywords import (KEYWORD_RES, STRONG_RE, as_text,
                              is_biodiversity, mask_false_positives)
from lunaris_sample_for_review import looks_french

# (title, judgment, reason). Looked up by title rather than by row number:
# re-running the sampler now returns a different set of records, and matching
# on position would quietly attach these labels to the wrong ones.
JUDGMENTS = [
    ("Eulachon Migration Study Bottom Trawl Surveys",
     "YES", "Eulachon trawl survey: species catch, effort and biological data on the BC coast."),
    ("Data from: Genetic relationships between Atlantic and Pacific populations of the notothenioid fish Eleginops maclovinus: the footprints of Quaternary glaciations in Patagonia",
     "YES", "Population genetics / genetic diversity of a fish species (Eleginops maclovinus)."),
    ("Mercury biomagnification in marine zooplankton food webs in Hudson Bay",
     "YES", "Taxon-resolved zooplankton sampling and marine food-web contaminant transfer."),
    ("Consumption of red maple in anticipation of beech mast-seeding drives reproduction in Eastern chipmunks",
     "YES", "Evolutionary ecology of chipmunk reproduction driven by tree mast-seeding."),
    ("Baynes Sound Oxygen Sensor Deployed 2024-12-11",
     "NO", "Dissolved-oxygen sensor deployment; physical/chemical oceanography, no organisms."),
    ("Baynes Sound Conductivity Temperature Depth Deployed 2022-01-25",
     "NO", "CTD instrument deployment; salinity/temperature/depth only, no organisms."),
    ("High heterogeneity in genomic differentiation between phenotypically divergent songbirds: A test of mitonuclear co-introgression",
     "YES", "Genomic differentiation and hybridization between two songbird species."),
    ("Biochar-based seed coating dramatically increases seedling germination and field establishment of arctic lupine (Lupinus arcticus)",
     "YES", "Germination and field establishment of boreal shrub/tree species for restoration."),
    ("Coho Salmon (Oncorhynchus kisutch) Conservation Units, Sites & Status",
     "YES", "Coho salmon conservation units, sites and status; species conservation data."),
    ("How does vegetation fragmentation affect urban carbon stock in City of Vancouver, Canada",
     "YES", "Borderline: carbon-focused, but measures urban vegetation cover and fragmentation (habitat structure)."),
    ("Human Activities in Ecosystems - Productive Forest Land Use",
     "YES", "Borderline: forest-industry framing, but the layer is forest ecosystem extent and land use."),
    ("Brampton Plan-Schedule 8-Energy Planning Districts",
     "NO", "Municipal energy planning districts; 'conservation' here means energy conservation."),
    ("Data from: Nowhere to hide: the impact of linear disturbances on the spatial dynamics of predator and prey in a large mammal system",
     "YES", "Predator-prey spatial dynamics of caribou, wolves and bears under linear disturbance."),
    ("Data from: Subtle shift in groundfish depth distribution within the impact range of seismic surveying along a continental slope",
     "YES", "Measures fish and zooplankton distribution response to seismic surveying."),
    ("Fraser Riverkeeper Vancouver Community-Based Water Monitoring Program",
     "NO", "Recreational water quality (E. coli) monitoring; 'Fish' is from the org name Swim Drink Fish."),
    ("Data from: Resource exploitation collapses the home range of an apex predator",
     "YES", "Movement ecology: home-range size of an apex predator vs resource density."),
    ("Neighboring edges: interacting edge effects of linear disturbances on vegetation in treed fens",
     "YES", "Edge effects of seismic lines on understory vegetation in treed fens; habitat fragmentation."),
    ("A Comprehensive Global Database of Tailings Flows",
     "NO", "Mine tailings dam failures and runout; 'plant' is from 'power plant operations'."),
    ("Apramycin resistance in bacteria isolated from animals, a systematic review and meta-analysis (dataset)",
     "NO", "Antimicrobial-resistance surveillance in livestock bacteria; veterinary/public health."),
    ("CHaNGE: Coastal Hydrodynamics and Natural Geologic Evolution",
     "NO", "Estuary hydrodynamics and geomorphic evolution modelling; physical, no biota."),
    ("Boreal predator co-occurrences reveal shared use of seismic lines in a working landscape",
     "YES", "Camera-trap survey of boreal predator co-occurrence; wildlife community ecology."),
    ("The role of cell-envelope synthesis for envelope growth and cytoplasmic density in Bacillus subtilis",
     "NO", "Bacterial cell-envelope and volume regulation; molecular cell biology, not diversity."),
    ("Replication Data for: Seismic site characterization and region-specific seismic site parameter relationships of Essex County, Ontario, Canada",
     "NO", "Geotechnical seismic site characterization (shear-wave velocity profiles)."),
    ("Data from: Natural regeneration on seismic lines influences movement behaviour of wolves and grizzly bears",
     "YES", "Wolf and grizzly movement in relation to seismic-line vegetation regeneration."),
    ("Local Appeals Body Appeals",
     "NO", "Municipal zoning and land-use appeal records."),
    ("An Overview of Relative Trisections",
     "NO", "Pure mathematics: trisections of 4-manifolds."),
    ("Corneal Laser Procedure for safety and efficacy in vision improvement",
     "NO", "Clinical ophthalmology trial of a corneal laser procedure."),
    ("Towards multiscale modeling of incommensurate 2D van der Waals heterostructures",
     "NO", "Condensed-matter physics: 2D van der Waals heterostructures."),
    ("Liquefied Natural Gas – Exports ",
     "NO", "Liquefied natural gas export volumes; energy regulation statistics."),
    ("MoEML Mayoral Shows",
     "NO", "Digital humanities anthology of early modern London pageant texts."),
    ("BC Schools - School District Funding Allocation 2008-2009",
     "NO", "School district operating grant allocations."),
    ("TRIM Surface Points",
     "NO", "TRIM topographic basemap point features; cartographic base layer."),
    ("CAGDB - Victoria Island I",
     "NO", "Airborne magnetic geophysical survey."),
    ("Quality of Life Physical Environment Indicator - Incidence of Property Crime",
     "NO", "Quality-of-life indicator: incidence of property crime."),
    ("710626SE",
     "NO", "Georeferenced RGB orthophoto tile; imagery with no biological content."),
    ("Enquête nationale auprès des diplômés, 2018 [Canada]: Promotion de 2015",
     "NO", "National Graduates Survey; education and labour-market outcomes."),
    ("Survey of 1990 Graduates, June 1992, 1992",
     "NO", "Graduate survey on school-to-work transition; labour market."),
    ("Survey results: Gender and sex information on Ontario government IDs and forms",
     "NO", "Public consultation on gender/sex fields in government IDs."),
    ("Game Hunting Areas",
     "YES", "Game hunting area boundaries under the Wildlife Act; species-specific seasons and harvest limits."),
    ("GHGSat plume dataset for article \"Global satellite survey reveals uncertainty in landfill methane emissions\"",
     "NO", "Satellite methane plume detections at landfills; greenhouse-gas emissions."),
    ("Regional Tourism Profiles",
     "NO", "Regional tourism statistics: visits, spending, hotel performance."),
    ("North American Central Plains Anomaly",
     "NO", "Location of a geological/geophysical conductivity anomaly."),
    ("National Occupational Classification (NOC) 2016 Version 1.1",
     "NO", "National Occupational Classification; occupation taxonomy, not biological taxonomy."),
    ("Innovation and business strategy, business activities undertaken",
     "NO", "Business innovation and strategy survey by industry."),
    ("Moose Abundance - Riding Mountain",
     "YES", "Annual aerial counts of moose population in Riding Mountain National Park."),
    ("Manitoba Wildlife Lands",
     "YES", "Wildlife management areas, refuges and conservation zones mapping."),
    ("Children and youth mental health: organizational and clinical data",
     "NO", "Children's mental health service and clinical assessment records."),
    ("Total count and T4 earnings of inter-jurisdictional employees by target province or territory, inactive",
     "NO", "Counts and T4 earnings of inter-jurisdictional employees."),
]

def main():
    """Score the hand-written judgments against the filter and write the CSV.

    Attaches each record's judgment and reason, works out which keyword the
    filter fired on, and prints how often the two agree -- both as the filter
    was and as it is now that the misleading phrases are blanked out.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", default="sample_for_review.csv")
    parser.add_argument("--harvest", default="harvest/lunaris_full_harvest.parquet")
    parser.add_argument("--out", default="sample_reviewed.csv")
    args = parser.parse_args()

    df = pd.read_csv(args.sample, encoding="utf-8-sig")
    by_title = {t: (v, why) for t, v, why in JUDGMENTS}
    assert len(by_title) == len(JUDGMENTS), (
        f"{len(JUDGMENTS) - len(by_title)} judgment(s) share a title with "
        "another and would quietly overwrite each other"
    )
    unlabelled = sorted(set(df["title"]) - set(by_title))
    if unlabelled:
        raise SystemExit(
            f"{len(unlabelled)} sampled record(s) have no judgment, e.g.\n  "
            + "\n  ".join(unlabelled[:5])
            + "\n\nThe sample changed. Re-review the new records and add them "
              "to JUDGMENTS, or point --sample at the reviewed sample.")

    df["semantic_judgment"] = [by_title[t][0] for t in df["title"]]
    df["semantic_reason"] = [by_title[t][1] for t in df["title"]]

    # Reload the harvest for each record's full text. The abstract in the CSV
    # is cut to 600 characters, which can hide the very keyword that fired and
    # make the filter look like it kept a record for no reason.
    harvest = pd.read_parquet(args.harvest)
    harvest["_t"] = harvest["title"].apply(as_text)
    harvest["_a"] = harvest["abstract"].apply(as_text)
    harvest["_hay"] = (harvest["_t"] + " " + harvest["subjects"].apply(as_text)
                       + " " + harvest["_a"]).str.lower()
    # Left unmasked on purpose: the comparison is about which keyword the
    # filter fired on before the misleading phrases were blanked out.

    # A record whose title is missing from the harvest would silently come back
    # with empty text, and every number below would then be wrong while looking
    # perfectly reasonable. Stop instead.
    def full_hay(row):
        """Find this record's full text in the harvest, matching on title."""
        cand = harvest[harvest["_t"] == row["title"]]
        if len(cand) > 1:  # two records share a title: the abstract separates them
            pref = str(row["abstract"])[:200]
            exact = cand[cand["_a"].str.startswith(pref, na=False)]
            if len(exact):
                cand = exact
        if not len(cand):
            raise SystemExit(
                f"'{str(row['title'])[:70]}' is not in {args.harvest}. The "
                "sample and the harvest are out of step -- point --harvest at "
                "the harvest the sample was drawn from."
            )
        return cand.iloc[0]["_hay"]

    full = [full_hay(r) for _, r in df.iterrows()]
    df["keyword_hits"] = [
        ", ".join(kw for kw, rx in KEYWORD_RES.items() if rx.search(h)) for h in full
    ]
    # The verdict with nothing blanked out, computed here rather than taken
    # from the CSV. The CSV's own column is whatever the filter said the day
    # the sample was drawn; redraw the sample and it becomes identical to the
    # masked verdict, leaving the comparison below saying nothing at all.
    df["keyword_kept_unmasked"] = [bool(STRONG_RE.search(h)) for h in full]
    # What the filter decides once the misleading phrases are blanked out.
    df["keyword_kept_fixed"] = [is_biodiversity(mask_false_positives(h)) for h in full]
    df["is_french"] = df["abstract"].fillna("").apply(looks_french)
    # Derived from the filter as it stands today, which is also the headline
    # number printed below, so the column and the printout cannot disagree.
    df["agrees"] = (df["keyword_kept_fixed"].map({True: "YES", False: "NO"})
                    == df["semantic_judgment"])

    cols = ["sampled_because", "keyword_kept", "keyword_kept_unmasked",
            "keyword_kept_fixed", "semantic_judgment", "agrees",
            "semantic_reason", "keyword_hits", "is_french",
            "title", "subjects", "abstract"]
    df[cols].to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"Wrote {args.out} ({len(df)} rows)")

    sem_yes = df["semantic_judgment"] == "YES"
    raw = df["keyword_kept_unmasked"]
    fx = df["keyword_kept_fixed"]

    # The filter as it stands today: the number that actually matters.
    print()
    print(f"agreement                {int(df['agrees'].sum())}/{len(df)}")
    print(f"keyword kept, sem NO     {int((fx & ~sem_yes).sum())}  (false positives)")
    print(f"keyword dropped, sem YES {int((~fx & sem_yes).sum())}  (missed)")

    print("\nbefore the misleading phrases are blanked out:")
    print(f"agreement                {int((raw == sem_yes).sum())}/{len(df)}")
    print(f"keyword kept, sem NO     {int((raw & ~sem_yes).sum())}  (false positives)")
    print(f"keyword dropped, sem YES {int((~raw & sem_yes).sum())}  (missed)")
    if raw.equals(fx):
        print("\n(identical: no record in this sample is affected by the masking)")


if __name__ == "__main__":
    main()
