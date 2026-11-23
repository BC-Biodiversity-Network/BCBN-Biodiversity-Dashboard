"""
check_duplicates.py

Read only (writes one example CSV outside the repo). How many latest
DataONE records look like the same dataset more than once, before filter 2
runs on 248,397 records. mark_versions.py only follows obsoletes and
seriesId; Evan suggested also treating records with the same title as
versions. This script measures several rules and decides nothing.

Inputs (backend/data/dataone/harvest_2026_11_18/, latest versions only):
  dataone_full_harvest_versions.parquet   the records
  dataone_groups.parquet                  to_filter2
  dataone_lunaris_match.parquet           match_type (DOI or UUID matches
                                          reuse Lunaris and skip filter 2)

Id patterns: hex32 (exactly 32 hexadecimal characters), sha256 (starts
with "sha256:"), doi (starts with "doi:"), other.

Normalised title: lower case, a trailing language tag such as "@en"
dropped (an "@" and two letters at the very end), whitespace collapsed.
The abstract is normalised the same way. Records with an empty title are
left out of the title rules.

Rules, always within one datasource; a group is two or more records:
  a  same normalised title
  b  same title and same box (the four index box values all equal, or all
     four missing). For a multi-box record the index holds only the first
     box.
  c  same title, box and normalised abstract
  c+ like c, but a record whose abstract is empty, or is only
     "This dataset is about: <its title>." (PANGAEA's filler), joins the
     group of the one real abstract in its b group. If the b group has two
     or more different real abstracts, such records are left in their own
     group (counted as unclear).
  d  same DOI, from id or seriesId (match_lunaris.dataone_dois, which uses
     dataone_common.norm_doi and the Research Workspace id form), whatever
     the title; records linked through a shared DOI form one group.
Also d across datasources: the same DOI in two member repositories.

Output: ~/Desktop/BCBN/Results/dataone_duplicates_2026-11-20/
duplicate_examples.csv, 40 example groups (see pick_examples).

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_duplicates.py
"""

import os
import re
import sys
from itertools import combinations
from urllib.parse import quote

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "dataone"))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris"))
from build_label_set import VIEW  # noqa: E402
from filter2_common import id_pattern  # noqa: E402 (shared with build_filter2_union.py)
from classify_multibox_records import HARVEST, INDEX_EDGES, SECONDS_PER_RECORD, VERSIONS  # noqa: E402
from match_lunaris import STRONG, dataone_dois  # noqa: E402

GROUPS = os.path.join(HARVEST, "dataone_groups.parquet")
MATCH = os.path.join(HARVEST, "dataone_lunaris_match.parquet")
OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_duplicates_2026-11-20")
OUT = os.path.join(OUT_DIR, "duplicate_examples.csv")
EXPECTED_FILTER2 = 248_397
SEED = 20261120
EDGES = ["north", "south", "east", "west"]
FILLER = "this dataset is about:"
RULES = ["a", "b", "c", "c+", "d"]
# Fields left out of the "one side has it, the other lacks it" comparison:
# they are added by our own harvest steps, not by the record.
OUR_FIELDS = {"harvest_date", "version_status", "old_reason", "newer_id"}

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 60)


def norm_text(value):
    """Lower case, drop a trailing "@xx" language tag, collapse whitespace; "" for a missing value."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.lower().split())
    text = re.sub(r"@[a-z]{2}$", "", text).strip()
    return text




def is_filler(abstract_norm, title_norm):
    """True if a normalised abstract is only "this dataset is about: <title>" (with or without a final full stop)."""
    if not abstract_norm.startswith(FILLER):
        return False
    rest = abstract_norm[len(FILLER):].strip().rstrip(".").strip()
    return rest == title_norm.rstrip(".").strip()


def present(value):
    """True if a field holds something: not missing, not an empty string, not an empty list."""
    if value is None:
        return False
    if isinstance(value, float):
        return value == value
    if isinstance(value, str):
        return value.strip() != ""
    if hasattr(value, "__len__"):
        return len(value) > 0
    return True


def read_records():
    """
    Return the latest records with the fields this check needs, plus
    pattern, normalised title and abstract, box key, DOIs, to_filter2 and
    whether filter 2 will really run on the record (no DOI or UUID match
    to Lunaris).
    """
    table = pq.read_table(VERSIONS)
    table = table.filter(pc.equal(table.column("version_status"), "latest"))
    keep = ["id", "seriesId", "datasource", "formatId", "title", "abstract", "dateUploaded", "dateModified"]
    df = pd.DataFrame({c: table.column(c).to_pandas() for c in keep})
    for edge, field in INDEX_EDGES.items():
        df[edge] = table.column(field).to_pandas()
    df["datasource"] = df["datasource"].str.replace("urn:node:", "", regex=False)
    df["pattern"] = df["id"].map(id_pattern)
    df["title_n"] = df["title"].map(norm_text)
    df["abstract_n"] = df["abstract"].map(norm_text)
    df["filler"] = [is_filler(a, t) for a, t in zip(df["abstract_n"], df["title_n"])]
    df["wild"] = (df["abstract_n"] == "") | df["filler"]
    df["box"] = [",".join("" if v != v else repr(float(v)) for v in row)
                 for row in df[EDGES].itertuples(index=False)]
    df["dois"] = [sorted(dataone_dois(i, s)) for i, s in zip(df["id"], df["seriesId"])]
    groups = pd.read_parquet(GROUPS, columns=["id", "to_filter2"]).set_index("id")["to_filter2"]
    match = pd.read_parquet(MATCH, columns=["id", "match_type"]).set_index("id")["match_type"]
    df["to_filter2"] = df["id"].map(groups).fillna(False).astype(bool)
    df["match_type"] = df["id"].map(match).fillna("")
    df["filter2_runs"] = df["to_filter2"] & ~df["match_type"].isin(STRONG)
    if int(df["filter2_runs"].sum()) != EXPECTED_FILTER2:
        sys.exit(f"STOPPED: {int(df['filter2_runs'].sum()):,} records for filter 2, not {EXPECTED_FILTER2:,}")
    return df, table


def components(n, pairs_by_key):
    """
    Return a component number for each of n records, joining every set of
    records that share a key (union-find); a record with no key is alone.
    """
    parent = np.arange(n)

    def find(i):
        """Return the root of record i, shortening the path on the way."""
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for members in pairs_by_key:
        root = find(members[0])
        for other in members[1:]:
            r = find(other)
            if r != root:
                parent[r] = root
    return np.array([find(i) for i in range(n)])


def add_group_keys(df):
    """Add one group key column per rule (a, b, c, c+, d, d_across); a missing key means no title."""
    titled = df["title_n"] != ""
    df["key_a"] = np.where(titled, df["datasource"] + "|" + df["title_n"], None)
    df["key_b"] = np.where(titled, df["key_a"].astype(str) + "|" + df["box"], None)
    df["key_c"] = np.where(titled, df["key_b"].astype(str) + "|" + df["abstract_n"], None)
    df["key_c+"] = df["key_c"]
    df["unclear"] = False
    # c+: within each b group, the records with an empty or filler abstract
    # join the one real abstract; with two or more real abstracts they stay apart.
    sub = df[titled]
    real = sub[~sub["wild"]].groupby("key_b")["abstract_n"].nunique()
    wild_rows = sub[sub["wild"]]
    one_real = real[real == 1]
    real_text = sub[~sub["wild"] & sub["key_b"].isin(one_real.index)].groupby("key_b")["abstract_n"].first()
    join = wild_rows["key_b"].isin(real_text.index)
    df.loc[wild_rows.index[join], "key_c+"] = (wild_rows.loc[join, "key_b"] + "|"
                                               + wild_rows.loc[join, "key_b"].map(real_text))
    n_real = wild_rows["key_b"].map(real).fillna(0)
    no_real = n_real == 0
    df.loc[wild_rows.index[no_real], "key_c+"] = wild_rows.loc[no_real, "key_b"] + "|<no real abstract>"
    many = n_real >= 2
    df.loc[wild_rows.index[many], "unclear"] = True
    # d: shared DOI within a datasource, and across datasources.
    exploded = df[["datasource", "dois"]].explode("dois").dropna()
    within = exploded.groupby(exploded["datasource"] + "|" + exploded["dois"]).groups
    across = exploded.groupby("dois").groups
    df["key_d"] = components(len(df), [list(v) for v in within.values() if len(v) > 1])
    df["key_d_across"] = components(len(df), [list(v) for v in across.values() if len(v) > 1])
    # A record without a DOI belongs to no d group.
    has_doi = df["dois"].map(len) > 0
    df["key_d"] = df["key_d"].astype(object).where(has_doi, None)
    df["key_d_across"] = df["key_d_across"].astype(object).where(has_doi, None)
    return df


def group_stats(df, key, by_datasource=True):
    """
    Return, for one rule, the groups of two or more records: per datasource
    (or overall) the number of groups, the records in them, and the groups
    that mix a hex32 id with a sha256 id.
    """
    sub = df[df[key].notna()]
    size = sub.groupby(key)[key].transform("size")
    sub = sub[size > 1].assign(is_hex=lambda d: d["pattern"] == "hex32", is_sha=lambda d: d["pattern"] == "sha256")
    per_group = sub.groupby(key).agg(datasource=("datasource", "first"), records=("id", "size"),
                                     hex32=("is_hex", "max"), sha256=("is_sha", "max"))
    per_group["mixed"] = per_group["hex32"] & per_group["sha256"]
    if not by_datasource:
        return per_group
    table = per_group.groupby("datasource").agg(groups=("records", "size"), records=("records", "sum"),
                                                mixed_hex_sha=("mixed", "sum"))
    return table.sort_values("records", ascending=False)


def report_patterns(df):
    """Point 1: id patterns by datasource, and the formatId values of each pattern."""
    print("1. ID PATTERNS BY DATASOURCE (latest records)")
    table = pd.crosstab(df["datasource"], df["pattern"])
    table["total"] = table.sum(axis=1)
    table = table.sort_values("total", ascending=False)
    print(table.head(25).to_string())
    rest = table.iloc[25:]
    print(f"   ... and {len(rest)} more datasources with {int(rest['total'].sum()):,} records "
          f"(patterns: {rest.drop(columns='total').sum().to_dict()})")
    print(f"   total: {table.drop(columns='total').sum().to_dict()}")
    print("\n   formatId by pattern (top 6; records)")
    for pattern, part in df.groupby("pattern"):
        top = part["formatId"].value_counts().head(6)
        print(f"   {pattern} ({len(part):,}): " + "; ".join(f"{f} {n:,}" for f, n in top.items()))
    print("   formatId by pattern, PANGAEA only")
    for pattern, part in df[df["datasource"] == "PANGAEA"].groupby("pattern"):
        print(f"   {pattern} ({len(part):,}): " + "; ".join(
            f"{f} {n:,}" for f, n in part["formatId"].value_counts().head(4).items()))
    print("   'other' ids, first 3 examples per datasource of the top 5:")
    other = df[df["pattern"] == "other"]
    for ds in other["datasource"].value_counts().head(5).index:
        print(f"   {ds}: " + " | ".join(other.loc[other["datasource"] == ds, "id"].head(3)))


def report_rules(df):
    """Point 2: groups and records under each rule, by datasource, with hex32 + sha256 mixes."""
    print("\n2. SAME-DATASET GROUPS (two or more records, same datasource)")
    print(f"   records without a title (left out of a, b, c, c+): {int((df['title_n'] == '').sum()):,}; "
          f"without a DOI (left out of d): {int((df['dois'].map(len) == 0).sum()):,}")
    totals = []
    for rule in RULES:
        table = group_stats(df, f"key_{rule}")
        totals.append({"rule": rule, "groups": int(table["groups"].sum()), "records": int(table["records"].sum()),
                       "mixed_hex_sha_groups": int(table["mixed_hex_sha"].sum())})
        print(f"\n   rule {rule}, top 8 datasources")
        print(table.head(8).to_string())
    print("\n   TOTALS")
    print(pd.DataFrame(totals).to_string(index=False))
    titled = df[df["title_n"] != ""]
    in_b = titled[titled.groupby("key_b")["id"].transform("size") > 1]
    wild = in_b[in_b["wild"]]
    joined = df[(df["key_c+"] != df["key_c"]) & df["key_c+"].notna()]
    print(f"\n   in b groups: {len(wild):,} records have an empty ({int((wild['abstract_n'] == '').sum()):,}) "
          f"or filler ({int(wild['filler'].sum()):,}) abstract; {len(joined):,} of them join a group under c+; "
          f"{int(df['unclear'].sum()):,} unclear (two or more real abstracts in the b group)")
    child = df["abstract_n"].str.contains("please consult parent dataset", regex=False)
    print(f"   abstracts saying 'Please consult parent dataset': {int(child.sum()):,} "
          f"({df.loc[child, 'datasource'].value_counts().head(3).to_dict()})")


def pair_table(df, key):
    """Return the hex32 / sha256 pairs of PANGAEA: groups under `key` with exactly one of each and nothing else."""
    pang = df[(df["datasource"] == "PANGAEA") & df[key].notna()]
    groups = pang.groupby(key)
    shape = groups["pattern"].agg(lambda p: tuple(sorted(p)))
    good = shape[shape == ("hex32", "sha256")].index
    pairs = pang[pang[key].isin(good)].sort_values([key, "pattern"])
    hexes = pairs[pairs["pattern"] == "hex32"].set_index(key)
    shas = pairs[pairs["pattern"] == "sha256"].set_index(key)
    return hexes.join(shas, lsuffix="_hex", rsuffix="_sha")


def report_pangaea(df, table):
    """Point 3: PANGAEA hex32 records with a sha256 partner, and how the two sides of a pair differ."""
    print("\n3. PANGAEA hex32 AND sha256")
    pang = df[df["datasource"] == "PANGAEA"]
    hexes = pang[pang["pattern"] == "hex32"]
    for key, label in (("key_c", "c"), ("key_c+", "c+"), ("key_d", "d")):
        has_sha = pang[pang["pattern"] == "sha256"].dropna(subset=[key])[key].unique()
        partnered = hexes[key].isin(set(has_sha))
        print(f"   rule {label}: {int(partnered.sum()):,} of {len(hexes):,} hex32 records have a sha256 partner")
    pairs = pair_table(df, "key_d")
    print(f"\n   clean d pairs (a DOI group of exactly one hex32 and one sha256): {len(pairs):,}")
    same = pd.DataFrame({
        "title (normalised)": pairs["title_n_hex"] == pairs["title_n_sha"],
        "box": pairs["box_hex"] == pairs["box_sha"],
        "abstract (normalised)": pairs["abstract_n_hex"] == pairs["abstract_n_sha"],
        "abstract, or one side empty/filler": (pairs["abstract_n_hex"] == pairs["abstract_n_sha"])
                                              | pairs["wild_hex"] | pairs["wild_sha"],
        "title exactly as harvested": pairs["title_hex"] == pairs["title_sha"]})
    print("   in those pairs, the share that is the same: " + "; ".join(
        f"{c} {v.mean():.1%}" for c, v in same.items()))
    print(f"   abstract: hex empty {pairs['abstract_n_hex'].eq('').mean():.1%}, filler {pairs['filler_hex'].mean():.1%}; "
          f"sha empty {pairs['abstract_n_sha'].eq('').mean():.1%}, filler {pairs['filler_sha'].mean():.1%}")
    for field in ("dateUploaded", "dateModified"):
        h, s = pd.to_datetime(pairs[f"{field}_hex"], utc=True, errors="coerce"), \
            pd.to_datetime(pairs[f"{field}_sha"], utc=True, errors="coerce")
        print(f"   {field}: hex newer {(h > s).sum():,}; sha newer {(s > h).sum():,}; same {(h == s).sum():,}; "
              f"missing on a side {(h.isna() | s.isna()).sum():,}; hex range {h.min()} .. {h.max()}; "
              f"sha range {s.min()} .. {s.max()}")
    formats = (pairs["formatId_hex"] + "  +  " + pairs["formatId_sha"]).value_counts().head(4)
    print("   formatId pairs: " + "; ".join(f"{f} {n:,}" for f, n in formats.items()))
    series = pd.Series({"both have seriesId": (pairs["seriesId_hex"].map(present) & pairs["seriesId_sha"].map(present)).sum(),
                        "same DOI in seriesId": sum(norm_doi_set(a) == norm_doi_set(b) and norm_doi_set(a) != set()
                                                    for a, b in zip(pairs["seriesId_hex"], pairs["seriesId_sha"])),
                        "seriesId text identical": (pairs["seriesId_hex"] == pairs["seriesId_sha"]).sum()})
    print("   seriesId: " + "; ".join(f"{k} {v:,}" for k, v in series.items()))
    # Field presence: which fields one side has and the other lacks, over every field of the harvest.
    ids = set(pairs["id_hex"]) | set(pairs["id_sha"])
    full = table.filter(pc.is_in(table.column("id"), value_set=pa_array(ids))).to_pandas().set_index("id")
    fields = [c for c in full.columns if c not in OUR_FIELDS and c != "id"]
    hex_has = full.loc[pairs["id_hex"], fields].apply(lambda col: col.map(present)).to_numpy()
    sha_has = full.loc[pairs["id_sha"], fields].apply(lambda col: col.map(present)).to_numpy()
    presence = pd.DataFrame({"field": fields, "hex only": (hex_has & ~sha_has).sum(axis=0),
                             "sha only": (sha_has & ~hex_has).sum(axis=0), "both": (hex_has & sha_has).sum(axis=0)})
    presence = presence[(presence["hex only"] > 0) | (presence["sha only"] > 0)]
    print("   fields one side has and the other lacks (pairs):")
    print(presence.sort_values("field").to_string(index=False))
    n_hex, n_sha = hex_has.sum(axis=1), sha_has.sum(axis=1)
    print(f"   filled fields per record: hex mean {n_hex.mean():.1f}, sha mean {n_sha.mean():.1f}; "
          f"hex has more {int((n_hex > n_sha).sum()):,}, sha more {int((n_sha > n_hex).sum()):,}, "
          f"equal {int((n_hex == n_sha).sum()):,}")
    kw_h = full.loc[pairs["id_hex"], "keywords"].map(lambda v: len(v) if present(v) else 0).to_numpy()
    kw_s = full.loc[pairs["id_sha"], "keywords"].map(lambda v: len(v) if present(v) else 0).to_numpy()
    ab_h = pairs["abstract_hex"].fillna("").str.len().to_numpy()
    ab_s = pairs["abstract_sha"].fillna("").str.len().to_numpy()
    print(f"   keywords per record: hex mean {kw_h.mean():.1f}, sha mean {kw_s.mean():.1f}; "
          f"abstract length: hex longer {(ab_h > ab_s).sum():,}, sha longer {(ab_s > ab_h).sum():,}, "
          f"same {(ab_h == ab_s).sum():,}")
    print(f"   to_filter2: both {int((pairs['to_filter2_hex'] & pairs['to_filter2_sha']).sum()):,}, "
          f"hex only {int((pairs['to_filter2_hex'] & ~pairs['to_filter2_sha']).sum()):,}, "
          f"sha only {int((~pairs['to_filter2_hex'] & pairs['to_filter2_sha']).sum()):,}")
    return pairs


def norm_doi_set(series_id):
    """Return the set of normalised DOIs in a seriesId (empty set for none)."""
    return set(dataone_dois("", series_id))


def pa_array(values):
    """Return a pyarrow string array of the given values (for an is_in filter)."""
    import pyarrow as pa
    return pa.array(sorted(values), type=pa.string())


def report_across(df):
    """Point 4: DOIs that appear in more than one datasource, and the top 10 datasource pairs."""
    print("\n4. THE SAME DOI IN MORE THAN ONE DATASOURCE")
    exploded = df[["id", "datasource", "dois"]].explode("dois").dropna()
    per_doi = exploded.groupby("dois")["datasource"].agg(lambda s: tuple(sorted(set(s))))
    multi = per_doi[per_doi.map(len) > 1]
    records = exploded[exploded["dois"].isin(multi.index)]["id"].nunique()
    print(f"   DOIs in more than one datasource: {len(multi):,} (of {len(per_doi):,} DOIs); records holding them: {records:,}")
    pairs = pd.Series([p for combo in multi for p in combinations(combo, 2)]).value_counts().head(10)
    print("   top 10 datasource pairs (DOIs shared):")
    for (a, b), n in pairs.items():
        print(f"   {a} + {b}: {n:,}")
    across = group_stats(df, "key_d_across", by_datasource=False)
    print(f"   rule d across datasources: {len(across):,} groups, {int(across['records'].sum()):,} records")


def report_filter2(df):
    """Point 5: of the records filter 2 will run on, how many are in a group, and how many are left keeping one per group."""
    print(f"\n5. EFFECT ON FILTER 2 ({EXPECTED_FILTER2:,} records: to_filter2, no DOI or UUID match to Lunaris)")
    runs = df[df["filter2_runs"]]
    rows = []
    for rule in RULES + ["d_across"]:
        key = f"key_{rule}"
        sub = runs[runs[key].notna()]
        size = sub.groupby(key)[key].transform("size")
        grouped = sub[size > 1]
        removed = len(grouped) - grouped[key].nunique()
        # Records whose group (over all latest records) has a member that does not run filter 2.
        everyone = df[df[key].notna()]
        outside_keys = set(everyone.loc[~everyone["filter2_runs"], key])
        lunaris_keys = set(everyone.loc[everyone["match_type"].isin(STRONG), key])
        rows.append({"rule": rule, "in_a_group": len(grouped), "groups": grouped[key].nunique(),
                     "left_one_per_group": len(runs) - removed, "removed": removed,
                     "hours_saved": round(removed * SECONDS_PER_RECORD / 3600, 1),
                     "with_partner_matched_to_lunaris": int(sub[key].isin(lunaris_keys).sum()),
                     "with_partner_not_in_filter2": int(sub[key].isin(outside_keys - lunaris_keys).sum())})
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"   (hours at {SECONDS_PER_RECORD} s per record, one slice; the partner columns count records whose "
          f"group also holds a record that skips filter 2)")
    pang = runs[runs["datasource"] == "PANGAEA"]
    print(f"   PANGAEA among them: {len(pang):,} ({pang['pattern'].value_counts().to_dict()})")


def pick_examples(df):
    """
    Return 40 example groups: 15 PANGAEA c+ groups mixing hex32 and sha256,
    5 rule-a groups whose boxes differ (same title, not the same box), and
    20 c+ groups from other datasources taken one datasource at a time.
    At most 8 records are shown per group.
    """
    rng = np.random.default_rng(SEED)
    stats = group_stats(df, "key_c+", by_datasource=False)
    mixed = stats[(stats["datasource"] == "PANGAEA") & stats["mixed"]]
    chosen = [("c+", k) for k in rng.choice(mixed.index, 15, replace=False)]
    a_stats = group_stats(df, "key_a", by_datasource=False)
    titled = df[df["key_a"].isin(a_stats.index)]
    boxes = titled.groupby("key_a")["box"].nunique()
    differ = boxes[boxes > 1]
    differ_ds = titled.drop_duplicates("key_a").set_index("key_a").loc[differ.index, "datasource"]
    picks = []
    for ds in differ_ds.value_counts().index:
        if len(picks) == 5:
            break
        options = differ_ds[differ_ds == ds].index
        options = [k for k in options if a_stats.at[k, "records"] <= 8] or list(options)
        picks.append(rng.choice(options))
    while len(picks) < 5:
        picks.append(rng.choice(differ.index))
    chosen += [("a only: boxes differ", k) for k in picks]
    others = stats[~stats.index.isin([k for _, k in chosen])]
    others = others[(others["datasource"] != "PANGAEA") | ~others["mixed"]]
    order = others["datasource"].value_counts().index
    queues = {ds: list(rng.permutation(others.index[others["datasource"] == ds])) for ds in order}
    rest = []
    while len(rest) < 20 and any(queues.values()):
        for ds in order:
            if queues[ds] and len(rest) < 20:
                rest.append(queues[ds].pop(0))
    chosen += [("c+", k) for k in rest]
    return chosen


def write_examples(df, chosen):
    """Write the example groups to OUT, one row per record, at most 8 per group."""
    rows = []
    for number, (rule, key) in enumerate(chosen, start=1):
        column = "key_a" if rule.startswith("a") else "key_c+"
        members = df[df[column] == key]
        for _, r in members.head(8).iterrows():
            rows.append({"group": number, "rule": rule, "group_size": len(members), "id": r["id"],
                         "id_pattern": r["pattern"], "datasource": r["datasource"], "formatId": r["formatId"],
                         "title": r["title"], "abstract_first_300": r["abstract"][:300] if isinstance(r["abstract"], str) else "",
                         "box": "" if r["box"] == ",,," else
                         "N {} S {} E {} W {}".format(*[f"{r[e]:g}" for e in EDGES]),
                         "seriesId": r["seriesId"], "dateUploaded": r["dateUploaded"],
                         "dateModified": r["dateModified"], "goes_to_filter2": r["filter2_runs"],
                         "link": VIEW + quote(r["id"], safe="")})
    out = pd.DataFrame(rows)
    os.makedirs(OUT_DIR, exist_ok=True)
    out.to_csv(OUT + ".tmp", index=False, encoding="utf-8-sig")
    os.replace(OUT + ".tmp", OUT)
    print(f"\n6. EXAMPLES: wrote {out['group'].nunique()} groups, {len(out)} rows -> {OUT}")
    print("   " + str(out.drop_duplicates("group").groupby("rule")["datasource"].value_counts().to_dict()))


def main():
    """Read the records, build the group keys, print points 1 to 5 and write the examples."""
    df, table = read_records()
    print(f"latest records: {len(df):,}; to_filter2: {int(df['to_filter2'].sum()):,}; "
          f"filter 2 will run on: {int(df['filter2_runs'].sum()):,}\n")
    df = add_group_keys(df)
    report_patterns(df)
    report_rules(df)
    report_pangaea(df, table)
    report_across(df)
    report_filter2(df)
    write_examples(df, pick_examples(df))
    return 0


if __name__ == "__main__":
    sys.exit(main())
