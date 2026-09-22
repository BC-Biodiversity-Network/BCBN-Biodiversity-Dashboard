"""
Work out what one LLM pass over the Lunaris records would cost, before
spending anything.

The idea is simple. The cost of an LLM call is driven by how much text you
send it. So we build exactly the text we would send for each record, count
how many tokens that is, and multiply by a price. No API calls are made and
nothing is charged.

Two scenarios are reported, because they cost very different amounts:

  candidates only   the 18,931 records that survived the keyword filter
  everything        all 123,479 harvested records

The second one matters because if the LLM is going to replace filter 1, it
has to read every record, not just the ones the keywords already kept.

Usage:

    python exploration/llm/cost_estimate.py --everything data/lunaris_full_harvest.parquet
    python exploration/llm/cost_estimate.py --everything data/lunaris_full_harvest.parquet \
                                --candidates data/lunaris_taxa.parquet

Either argument can be left out.
"""

import argparse
import sys

import pandas as pd


# ------------------------------------------------------------------ SETTINGS

# The instructions you send alongside every record. This text is charged for
# on every single call, so a long prompt is expensive when multiplied by
# 123,479. Replace this with your real prompt once you have written it, so
# the estimate stays honest.
PROMPT_TEMPLATE = """\
You are helping build a biodiversity data catalogue for British Columbia.

Below is the metadata for one archived dataset. Decide two things.

1. Is this dataset about living things, ecosystems, or biodiversity?
2. If it names a specific organism, what is it?

Answer in JSON only, with the keys "is_biodiversity" (true or false),
"organism" (a string, or null), and "confidence" (a number from 0 to 1).

TITLE:
{title}

SUBJECTS:
{subjects}

ABSTRACT:
{abstract}
"""

# Roughly how many tokens the model writes back per record. The JSON answer
# above is short, so this is small, but output tokens usually cost more per
# token than input tokens, which is why it is counted separately.
OUTPUT_TOKENS_PER_RECORD = 40

# Prices to show in the cost table, in dollars per million tokens. These are
# deliberately a spread rather than specific models, so the table does not go
# stale. Look up the model you want on its pricing page and read off the row
# closest to it.
PRICE_POINTS_PER_MILLION = [0.10, 0.30, 1.00, 3.00, 10.00, 15.00]

# Column names we look for. The first one found is used.
TITLE_COLUMNS = ["title", "titles", "dc_title"]
SUBJECT_COLUMNS = ["subjects", "subject", "keywords", "matched_keywords"]
ABSTRACT_COLUMNS = ["abstract", "description", "abstracts"]


# ------------------------------------------------------------------ HELPERS

def find_column(frame, candidates):
    """Return the first column name from candidates that exists in the frame."""
    for name in candidates:
        if name in frame.columns:
            return name
    return None


def as_text(value):
    """
    Turn one cell into the plain text we would send to the model.

    Some columns hold a list (subjects is usually a list of keywords) and some
    hold a plain string. Missing values become an empty string so the token
    count is not thrown off by the word "nan".
    """
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def get_token_counter():
    """
    Return a function that counts tokens in a string, and a label saying how
    it is counting.

    If tiktoken is installed we use it, which is accurate for OpenAI-style
    models and close enough for the others. If it is not installed we fall
    back to dividing the character count by four, which is the usual rule of
    thumb and is good to within about ten percent for English prose.
    """
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        return (lambda text: len(encoding.encode(text)), "tiktoken cl100k_base")
    except Exception:
        return (lambda text: max(1, len(text) // 4), "rough estimate, characters divided by 4")


def build_texts(frame, count_tokens):
    """
    Build the full prompt for every row and count its tokens.

    Returns a pandas Series of token counts, one per record, in the same order
    as the frame.
    """
    title_col = find_column(frame, TITLE_COLUMNS)
    subject_col = find_column(frame, SUBJECT_COLUMNS)
    abstract_col = find_column(frame, ABSTRACT_COLUMNS)

    print(f"    using columns: title={title_col}, subjects={subject_col}, abstract={abstract_col}")
    if title_col is None and abstract_col is None:
        print("    WARNING: neither a title nor an abstract column was found.")
        print(f"    columns present: {list(frame.columns)}")

    counts = []
    for _, row in frame.iterrows():
        prompt = PROMPT_TEMPLATE.format(
            title=as_text(row[title_col]) if title_col else "",
            subjects=as_text(row[subject_col]) if subject_col else "",
            abstract=as_text(row[abstract_col]) if abstract_col else "",
        )
        counts.append(count_tokens(prompt))
    return pd.Series(counts)


def describe(name, counts, frame):
    """Print the size of one scenario and the spread of its record lengths."""
    total_in = int(counts.sum())
    total_out = len(counts) * OUTPUT_TOKENS_PER_RECORD

    print()
    print("=" * 70)
    print(f"{name}")
    print("=" * 70)
    print(f"  records                {len(counts):>12,}")
    print(f"  input tokens, total    {total_in:>12,}")
    print(f"  output tokens, total   {total_out:>12,}  (assuming {OUTPUT_TOKENS_PER_RECORD} per record)")
    print()
    print("  input tokens per record")
    print(f"    mean                 {counts.mean():>12,.0f}")
    print(f"    median               {counts.median():>12,.0f}")
    print(f"    90th percentile      {counts.quantile(0.90):>12,.0f}")
    print(f"    99th percentile      {counts.quantile(0.99):>12,.0f}")
    print(f"    longest              {counts.max():>12,.0f}")
    print()

    # A handful of very long abstracts can quietly dominate the bill, so it is
    # worth seeing whether that is happening here.
    top = counts.nlargest(10)
    share = top.sum() / total_in * 100
    print(f"  the 10 longest records are {share:.2f}% of all input tokens")
    title_col = find_column(frame, TITLE_COLUMNS)
    if title_col:
        for idx, value in top.items():
            title = as_text(frame.iloc[idx][title_col])[:60]
            print(f"    {value:>7,} tokens  {title}")

    return total_in, total_out


def cost_table(total_in, total_out):
    """Print what the scenario would cost across a spread of prices."""
    print()
    print("  cost at different prices, dollars per million tokens")
    print(f"    {'input $/M':>10}  {'output $/M':>10}  {'total cost':>12}")
    for price in PRICE_POINTS_PER_MILLION:
        # Output tokens usually cost more than input tokens. Three times is a
        # common ratio, so it is used here as a stand-in. Put the real numbers
        # in once you have picked a model.
        out_price = price * 3
        cost = total_in / 1_000_000 * price + total_out / 1_000_000 * out_price
        print(f"    {price:>10.2f}  {out_price:>10.2f}  {'$' + format(cost, ',.2f'):>12}")


# ------------------------------------------------------------------ MAIN

def main():
    """Read the parquet files given on the command line and report on each."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--everything", help="parquet of all harvested records")
    parser.add_argument("--candidates", help="parquet of records that passed filter 1")
    args = parser.parse_args()

    if not args.everything and not args.candidates:
        parser.error("give at least one of --everything or --candidates")

    count_tokens, how = get_token_counter()
    print(f"counting tokens with: {how}")
    print(f"prompt template is {count_tokens(PROMPT_TEMPLATE):,} tokens before any record text")

    scenarios = []
    if args.candidates:
        scenarios.append(("CANDIDATES ONLY, after the keyword filter", args.candidates))
    if args.everything:
        scenarios.append(("EVERYTHING, if the LLM replaces the keyword filter", args.everything))

    results = {}
    for name, path in scenarios:
        print()
        print(f"reading {path}")
        frame = pd.read_parquet(path).reset_index(drop=True)
        counts = build_texts(frame, count_tokens)
        total_in, total_out = describe(name, counts, frame)
        cost_table(total_in, total_out)
        results[name] = total_in + total_out

    if len(results) == 2:
        values = list(results.values())
        print()
        print("=" * 70)
        print(f"running over everything is {values[1] / values[0]:.1f}x the tokens of "
              f"running over the candidates only")
        print("=" * 70)

    print()
    print("Reminder: this is an estimate from token counts. It does not include")
    print("retries, and it assumes the prompt above is the one you actually use.")


if __name__ == "__main__":
    sys.exit(main())
