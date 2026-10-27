"""
The final classes of the Lunaris BC filter, worked out by code from the
facts pass 2 asks the model for (version C4, geo_pass2_prompt_c4.py). The
model never picks a class itself.

The classes:

  bc_only                data only in BC.
  canada_incl_bc         several regions inside Canada including BC, but not
                         Canada-wide (BC plus one neighbouring province counts).
  all_canada             a Canada-wide dataset.
  bc_and_outside_canada  BC, or Canadian regions including BC, plus places
                         outside Canada (North America-wide or Pacific
                         Northwest-wide data that includes BC counts here).
  global                 worldwide data; a record that only says "global".
  elsewhere              the data were not collected in BC.
  unknown                the record does not say enough.

The model's facts: in_bc ("yes", "no" or "unclear"), and other_canada,
outside_canada, canada_wide and global ("yes" or "no").

Step 1, the box rule (fill_in_bc). Before the class is worked out, an in_bc
of "unclear" is read as "yes" when all of these hold:
  - at least one of the record's bounding boxes has at least
    BOX_SHARE_FOR_BC (half) of its area in BC: the share our note on the box
    shows, before it is rounded to a whole percent;
  - the model answered other_canada "no" and outside_canada "no".
The model's own answer is kept; decide returns both, and whether the rule
changed it. (Version C's prompt asked the model to do this itself; in the
trials it did not, so code does it. C4's prompt does not ask.)

Step 2, the class (class_of). The rules, checked in this order; the first
that applies decides:

  | # | global | outside_canada | canada_wide | in_bc   | other_canada | class                 |
  |---|--------|----------------|-------------|---------|--------------|-----------------------|
  | 1 | yes    | any            | any         | any     | any          | global                |
  | 2 | no     | yes            | any         | yes     | any          | bc_and_outside_canada |
  | 2 | no     | yes            | yes         | any     | any          | bc_and_outside_canada |
  | 3 | no     | any            | yes         | any     | any          | all_canada            |
  | 4 | no     | any            | no          | yes     | yes          | canada_incl_bc        |
  | 5 | no     | no             | no          | yes     | no           | bc_only               |
  | 6 | no     | any            | no          | no      | any          | elsewhere             |
  | 7 | no     | any            | no          | unclear | any          | unknown               |

(Row 5's outside_canada is "no" because row 2 has already taken "yes"; row 3
has already taken every canada_wide "yes" left after row 2.)
"""

CLASSES = ["bc_only", "canada_incl_bc", "all_canada", "bc_and_outside_canada", "global", "elsewhere", "unknown"]

# The facts and the answers each one may take.
FACTS = {
    "in_bc": ["yes", "no", "unclear"],
    "other_canada": ["yes", "no"],
    "outside_canada": ["yes", "no"],
    "canada_wide": ["yes", "no"],
    "global": ["yes", "no"],
}

# The box rule: an "unclear" in_bc is read as "yes" when a box has at least
# this share of its area in BC (and the model named no data outside BC; see
# fill_in_bc). Half, as in the prompt's own box rule.
BOX_SHARE_FOR_BC = 0.5


def class_of(facts):
    """
    Return the class for the model's facts (a dict with the keys of FACTS),
    by the rules in the module docstring, checked in order.
    """
    if facts["global"] == "yes":
        return "global"
    if facts["outside_canada"] == "yes" and (facts["in_bc"] == "yes" or facts["canada_wide"] == "yes"):
        return "bc_and_outside_canada"
    if facts["canada_wide"] == "yes":
        return "all_canada"
    if facts["in_bc"] == "yes" and facts["other_canada"] == "yes":
        return "canada_incl_bc"
    if facts["in_bc"] == "yes":
        return "bc_only"
    if facts["in_bc"] == "no":
        return "elsewhere"
    return "unknown"


def fill_in_bc(facts, box_shares):
    """
    Return True if the box rule reads the model's in_bc as "yes": in_bc is
    "unclear", other_canada and outside_canada are "no", and at least one
    box has at least BOX_SHARE_FOR_BC of its area in BC. box_shares holds
    each box's share in BC, from 0 to 1 (None for a box with no area, which
    is skipped).
    """
    if facts["in_bc"] != "unclear" or facts["other_canada"] != "no" or facts["outside_canada"] != "no":
        return False
    return any(share is not None and share >= BOX_SHARE_FOR_BC for share in box_shares)


def decide(answer, box_shares):
    """
    Work out a record's class from the model's answer and its boxes' shares
    in BC: first the box rule, then the class. Return a dict with
    model_in_bc (the model's own answer), in_bc_used (the answer the class
    is worked out from), in_bc_filled_by_box_rule (True if the box rule
    changed it) and class. The answer itself is not changed.
    """
    filled = fill_in_bc(answer, box_shares)
    facts = {fact: answer[fact] for fact in FACTS}
    if filled:
        facts["in_bc"] = "yes"
    return {"model_in_bc": answer["in_bc"], "in_bc_used": facts["in_bc"], "in_bc_filled_by_box_rule": filled,
            "class": class_of(facts)}
