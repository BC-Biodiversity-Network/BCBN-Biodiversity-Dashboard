"""Tests for reading the area text (read_area_shapes in api/area.py): one or
more shapes separated by ";", "b:west,south,east,north" for a box and
"p:lng,lat,..." for a polygon, an old link's "west,south,east,north" as one
box, and every rule that refuses an area, each with its message. The
routes do not use this yet, so the single-box area search is unchanged
(tests/test_area.py)."""

import math

import pytest

from api.area import MAX_AREA_POINTS, AreaRequestError, AreaShape, read_area_shapes


def refused(text):
    """Return the message read_area_shapes refuses text with."""
    with pytest.raises(AreaRequestError) as error:
        read_area_shapes(text)
    return str(error.value)


def circle(points, lng=-123.0, lat=49.0, radius=0.5):
    """Return the area text of a polygon of this many points around a
    circle (never crossing itself)."""
    numbers = []
    for i in range(points):
        angle = 2 * math.pi * i / points
        numbers += [f"{lng + radius * math.cos(angle):.4f}", f"{lat + radius * math.sin(angle):.4f}"]
    return "p:" + ",".join(numbers)


# --- What is read ------------------------------------------------------------------


def test_a_box_is_read_as_its_ring_and_edges():
    assert read_area_shapes("b:-120.6,50.55,-120.1,50.8") == [AreaShape(
        ring=((-120.6, 50.55), (-120.1, 50.55), (-120.1, 50.8), (-120.6, 50.8)), is_box=True,
        box=(-120.6, 50.55, -120.1, 50.8))]


def test_an_old_link_with_no_prefix_is_one_box():
    assert read_area_shapes("-120.6,50.55,-120.1,50.8") == read_area_shapes("b:-120.6,50.55,-120.1,50.8")
    # Spaces around the text and the numbers are allowed, as before.
    assert read_area_shapes(" -120.6, 50.55 ,-120.1,50.8 ")[0].box == (-120.6, 50.55, -120.1, 50.8)


def test_a_polygon_is_read_in_the_order_given():
    shapes = read_area_shapes("p:-123,49,-122,49,-122.5,50")
    assert shapes == [AreaShape(ring=((-123.0, 49.0), (-122.0, 49.0), (-122.5, 50.0)), is_box=False, box=None)]


def test_a_repeated_closing_point_is_dropped():
    closed = read_area_shapes("p:-123,49,-122,49,-122.5,50,-123,49")
    assert closed == read_area_shapes("p:-123,49,-122,49,-122.5,50")
    # Written another way (trailing zeros, a plus sign), it is still the same point.
    assert read_area_shapes("p:-123,49,-122,49,-122.5,50,-123.00,+49.0")[0].ring == closed[0].ring


def test_a_point_given_twice_in_a_row_is_kept_once():
    assert read_area_shapes("p:-123,49,-122,49,-122,49,-122.5,50")[0].ring == (
        (-123.0, 49.0), (-122.0, 49.0), (-122.5, 50.0))


def test_several_shapes_in_order_and_may_overlap():
    shapes = read_area_shapes("b:-121,50,-120,51;p:-120.5,50.5,-119,50.5,-119.5,52;b:-121,50,-120,51")
    assert [shape.is_box for shape in shapes] == [True, False, True]
    assert shapes[0] == shapes[2]


def test_a_polygon_may_be_concave_or_clockwise():
    # A "C" shape, clockwise.
    c_shape = "p:0,0,0,3,3,3,3,2,1,2,1,1,3,1,3,0"
    assert len(read_area_shapes(c_shape)[0].ring) == 8
    # Three points on a line inside a longer edge are not a crossing.
    assert len(read_area_shapes("p:0,0,1,0,2,0,2,2")[0].ring) == 4


# --- Refused: the text ---------------------------------------------------------------


@pytest.mark.parametrize("text", [None, "", "   "])
def test_an_empty_area_is_refused(text):
    assert refused(text) == ('Give the area as area=..., one or more shapes separated by ";": '
                             "b:west,south,east,north for a box, or p:lng,lat,lng,lat,... for a polygon.")


def test_an_empty_shape_is_refused():
    assert refused("b:0,0,1,1;;b:2,2,3,3") == 'Shape 2 is empty: remove the extra ";".'
    assert refused("b:0,0,1,1;") == 'Shape 2 is empty: remove the extra ";".'


@pytest.mark.parametrize("text, message", [
    ("x:0,0,1,1", "The area starts with 'x:', which is not a kind of shape: start it with b: for a box or p: "
                  "for a polygon."),
    ("B:0,0,1,1", "The area starts with 'B:', which is not a kind of shape: start it with b: for a box or p: "
                  "for a polygon."),
    ("b:0,0,1,1;0,0,1,1", "Shape 2 does not say what kind of shape it is: start it with b: for a box or p: for a "
                          "polygon."),
    # A missing ":" is not read as an old-style box.
    ("p0,0,1,0,1,1", "The area does not say what kind of shape it is: start it with b: for a box or p: for a "
                     "polygon."),
    ("p", "The area does not say what kind of shape it is: start it with b: for a box or p: for a polygon."),
])
def test_an_unknown_prefix_is_refused(text, message):
    assert refused(text) == message


@pytest.mark.parametrize("text, message", [
    ("b:0,0,1,abc", "The box has 'abc' where a number of degrees should be."),
    ("b:0,,1,1", "The box has an empty value where a number of degrees should be."),
    ("p:0,0,1,0,nan,1", "The polygon has 'nan' where a number of degrees should be."),
    ("p:0,0,1,0,inf,1", "The polygon has 'inf' where a number of degrees should be."),
    ("b:0,0,1e1,1", "The box has '1e1' where a number of degrees should be."),
    ("b:0,0,1_0,1", "The box has '1_0' where a number of degrees should be."),
    ("b:0,0,1 0,1", "The box has '1 0' where a number of degrees should be."),
    ("b:0,0,1", "The box needs 4 numbers, west,south,east,north; it has 3."),
    ("0,0,1,1,2", "The box needs 4 numbers, west,south,east,north; it has 5."),
    ("p:0,0,1,0,1", "The polygon needs its numbers in pairs, longitude then latitude; it has 5 numbers."),
])
def test_numbers_that_cannot_be_read_are_refused(text, message):
    assert refused(text) == message


@pytest.mark.parametrize("text, message", [
    ("b:", "The box is empty: give it as b:west,south,east,north."),
    ("p:", "The polygon is empty: give it as p:lng,lat,lng,lat,..."),
    ("p: ;b:0,0,1,1", "Shape 1 (a polygon) is empty: give it as p:lng,lat,lng,lat,..."),
    # A trailing comma is an empty value, not an extra number.
    ("p:0,0,1,0,1,1,", "The polygon has an empty value where a number of degrees should be."),
    ("p:1", "The polygon needs its numbers in pairs, longitude then latitude; it has 1 number."),
    # Shapes joined with a space instead of ";".
    ("b:0,0,1,1 p:2,2,3,2,3,3", "The box has '1 p:2' where a number of degrees should be (separate shapes with "
                                '";").'),
])
def test_an_empty_shape_or_a_slip_gets_a_clear_message(text, message):
    assert refused(text) == message


def test_a_long_value_is_cut_short_in_the_message():
    assert refused("b:0,0,1," + "9" * 5000) == "The box: its north edge is " + "9" * 30 + "..., outside -90 to 90 degrees."
    assert refused("b:0,0,1,x" + "9" * 5000) == "The box has 'x" + "9" * 29 + "...' where a number of degrees should be."


# --- Refused: ranges and box edges -----------------------------------------------------


@pytest.mark.parametrize("text, message", [
    ("b:-180.5,0,1,1", "The box: its west edge is -180.5, outside -180 to 180 degrees."),
    ("b:0,0,181,1", "The box: its east edge is 181, outside -180 to 180 degrees."),
    ("b:0,-91,1,1", "The box: its south edge is -91, outside -90 to 90 degrees."),
    ("b:0,0,1,90.01", "The box: its north edge is 90.01, outside -90 to 90 degrees."),
    ("p:0,0,1,0,200,1", "The polygon: its point 3's longitude is 200, outside -180 to 180 degrees."),
    ("p:0,0,1,95,1,1", "The polygon: its point 2's latitude is 95, outside -90 to 90 degrees."),
])
def test_numbers_out_of_range_are_refused(text, message):
    assert refused(text) == message


def test_the_ends_of_the_ranges_are_allowed():
    assert read_area_shapes("b:-180,-90,-179,90")[0].box == (-180.0, -90.0, -179.0, 90.0)
    assert read_area_shapes("p:179,89,180,89,180,90")[0].ring[2] == (180.0, 90.0)


@pytest.mark.parametrize("text, message", [
    ("b:1,0,1,1", "The box: its west edge (1) must be less than its east edge (1)."),
    ("b:2,0,1,1", "The box: its west edge (2) must be less than its east edge (1)."),
    ("b:0,1,1,1", "The box: its south edge (1) must be less than its north edge (1)."),
    ("b:0,2,1,1", "The box: its south edge (2) must be less than its north edge (1)."),
])
def test_a_box_whose_edges_are_out_of_order_is_refused(text, message):
    assert refused(text) == message


def test_messages_name_the_shape_when_there_are_several():
    assert refused("b:0,0,1,1;b:2,0,1,1") == (
        "Shape 2 (a box): its west edge (2) must be less than its east edge (1).")
    assert refused("b:0,0,1,1;p:0,0,1,1") == "Shape 2 (a polygon) has 2 different points; a polygon needs at least 3."


# --- Refused: polygons -------------------------------------------------------------------


@pytest.mark.parametrize("text, count", [
    ("p:0,0", 1),
    ("p:0,0,1,1", 2),
    ("p:0,0,1,1,0,0", 2),            # the closing copy is not a third point
    ("p:0,0,1,1,1,1,0,0,1,1", 2),
])
def test_a_polygon_with_fewer_than_3_different_points_is_refused(text, count):
    word = "point" if count == 1 else "points"
    assert refused(text) == f"The polygon has {count} different {word}; a polygon needs at least 3."


def test_a_bow_tie_polygon_is_refused():
    assert refused("p:0,0,1,1,1,0,0,1") == (
        "The polygon crosses or touches itself: the edge from point 1 to point 2 meets the edge from point 3 "
        "to point 4. Draw it so that no two edges cross or touch.")
    # The same bow-tie closed by repeating its first point.
    assert "crosses or touches itself" in refused("p:0,0,1,1,1,0,0,1,0,0")


@pytest.mark.parametrize("text, edges", [
    # A corner of one edge lying on another edge (touching, not crossing).
    ("p:0,0,4,0,4,4,2,0,0,4", "the edge from point 1 to point 2 meets the edge from point 3 to point 4"),
    # The same corner used twice, not as neighbours (a figure of eight
    # touching at one point).
    ("p:0,0,2,2,4,0,4,4,2,2,0,4", "the edge from point 1 to point 2 meets the edge from point 4 to point 5"),
    # An edge running back along the one before it.
    ("p:0,0,2,0,1,0,1,1", "the edge from point 1 to point 2 meets the edge from point 2 to point 3"),
    # Three different points on one line: no area at all.
    ("p:0,0,1,0,2,0", "the edge from point 1 to point 2 meets the edge from point 3 to point 1"),
    # Two edges lying along each other, not neighbours (edge 5-6 lies on
    # edge 1-2; the edge before it, ending on edge 1-2, is found first).
    ("p:0,0,3,0,3,1,2,1,2,0,1,0,1,1,0,1",
     "the edge from point 1 to point 2 meets the edge from point 4 to point 5"),
])
def test_a_polygon_that_touches_itself_is_refused(text, edges):
    assert refused(text) == (f"The polygon crosses or touches itself: {edges}. Draw it so that no two edges "
                             "cross or touch.")


def test_a_crossing_on_the_closing_edge_is_found():
    # The edge from the last point back to the first crosses edge 2-3.
    assert refused("p:0,0,0,1,1,0,1,1") == (
        "The polygon crosses or touches itself: the edge from point 2 to point 3 meets the edge from point 4 "
        "to point 1. Draw it so that no two edges cross or touch.")


@pytest.mark.parametrize("text, message", [
    # Point 4 is 1e-20 above edge 1-2 as written, but on it as the number
    # every later step uses.
    ("p:0,10,4,10,4,14,2,10.00000000000000000001,0,14", "crosses or touches itself"),
    # Two points that are the same number once read count as one.
    ("p:10,10,10.0000000000000001,10,10,10.0000000000000001", "has 1 different point;"),
    ("p:100,0,100.000000000000000001,0,100,1", "has 2 different points;"),
    # A box with no width once read.
    ("b:1,0,1.00000000000000000001,1", "its west edge (1) must be less than its east edge"),
    ("b:100,50,101,50.0000000000000000001", "its south edge (50) must be less than its north edge"),
    # Under 180 degrees across as written, exactly 180 once read.
    ("b:-90.000000000000000001,0,89.99999999999999999,1", "180 or more degrees of longitude apart"),
    ("p:0,0,179.99999999999999999,0,0,1", "180 or more degrees of longitude apart"),
    ("b:-179.71,0,0.289999999999999,5", "180 or more degrees of longitude apart"),
])
def test_numbers_with_more_digits_than_kept_are_checked_as_kept(text, message):
    """Every check runs on the numbers that are returned, so a link written
    with more digits than a float holds cannot pass a check and then turn
    into a shape the check would refuse."""
    assert message in refused(text)


def test_minus_zero_is_read_as_zero():
    assert read_area_shapes("p:-0,0,1,0,1,1")[0].ring[0] == (0.0, 0.0)
    assert str(read_area_shapes("p:-0,0,1,0,1,1")[0].ring[0][0]) == "0.0"


def test_one_very_long_number_is_quick():
    import time
    star = circle(249) + ",-122.6" + "7" * 10000 + ",49.1"
    started = time.perf_counter()
    assert len(read_area_shapes(star)[0].ring) == 250
    assert time.perf_counter() - started < 0.5


def test_points_are_compared_exactly_not_rounded():
    # Nearly touching (by 0.0001 degrees) is allowed; touching is not.
    assert read_area_shapes("p:0,0,4,0,4,4,2,0.0001,0,4")
    assert "touches itself" in refused("p:0,0,4,0,4,4,2,0.0000,0,4")


# --- Refused: the 180th meridian ---------------------------------------------------------


@pytest.mark.parametrize("text, name", [
    ("b:-179,0,179,1", "The box"),
    ("-179,0,179,1", "The box"),                     # an old link too
    ("b:-90,0,90,1", "The box"),                     # exactly 180 degrees across
    ("p:170,50,-170,50,-170,51", "The polygon"),
    ("p:-170,51,-170,50,170,50", "The polygon"),     # the crossing on the closing edge
    ("b:0,0,1,1;p:179,0,-179,0,-179,1", "Shape 2 (a polygon)"),
])
def test_a_shape_across_the_180th_meridian_is_refused(text, name):
    assert refused(text) == (f"{name} has two joined corners 180 or more degrees of longitude apart, so it "
                             "would be read as crossing the 180th meridian. An area across that meridian is "
                             "not taken.")


def test_a_wide_shape_that_does_not_cross_the_meridian_is_allowed():
    assert read_area_shapes("b:-100,0,79.9,1")[0].box == (-100.0, 0.0, 79.9, 1.0)
    # Each edge spans under 180 degrees, though the shape spans more.
    assert len(read_area_shapes("p:-170,0,0,0,170,0,170,1,0,1,-170,1")[0].ring) == 6


# --- Refused: too many points --------------------------------------------------------------


def test_the_point_limit_is_250_and_counts_every_shape():
    assert MAX_AREA_POINTS == 250
    assert len(read_area_shapes(circle(250))[0].ring) == 250
    # A box counts as 4: 246 polygon points and one box make 250.
    assert len(read_area_shapes(circle(246) + ";b:0,0,1,1")) == 2
    assert refused(circle(247) + ";b:0,0,1,1") == (
        "The area has 251 points, more than the 250 an area can have (a box counts as 4). Use fewer points or "
        "fewer shapes.")
    assert refused(circle(1000)).startswith("The area has 1,000 points, more than the 250")


def test_a_repeated_closing_point_does_not_count_towards_the_limit():
    closed = circle(250) + "," + circle(250)[2:].split(",", 2)[0] + "," + circle(250)[2:].split(",", 2)[1]
    assert len(read_area_shapes(closed)[0].ring) == 250


def test_the_old_box_reading_is_unchanged():
    """The routes still read the box from west, south, east and north, so
    the single-box area search works exactly as before."""
    from api.area import read_box
    assert read_box("-120.6", "50.55", "-120.1", "50.8") == (-120.6, 50.55, -120.1, 50.8)
