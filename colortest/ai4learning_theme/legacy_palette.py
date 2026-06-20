from __future__ import annotations

A4L_BG = "#0F2748"
A4L_TEXT_MAIN = "#EDF5FF"
A4L_TEXT_SUB = "#D9E7F7"

A4L_BLUE = "#3B82F6"
A4L_PINK = "#F43F5E"
A4L_PURPLE = "#8B5CF6"

PURPLE_200 = "#B1AFCE"
PURPLE_400 = "#E6BEFF"
PURPLE_600 = "#56479C"
PURPLE_900 = "#2C2860"

BLUE_100 = "#E6F1FF"
BLUE_300 = "#7DC7DB"
BLUE_500 = "#4CA4D0"
BLUE_700 = "#0183BB"
BLUE_900 = "#003E52"

CYAN_200 = "#A2CDD4"
CYAN_400 = "#7FDBFF"
CYAN_700 = "#008EB3"

GREEN_100 = "#F0FBC8"
GREEN_300 = "#8EFF5A"
GREEN_500 = "#81C7BC"
GREEN_700 = "#78BBAF"

YELLOW_300 = "#FFF98A"

PINK_200 = "#FFB3B3"
RED_300 = "#FFDBD1"
RED_500 = "#FF3B30"
RED_700 = "#ED746B"

ORANGE_200 = "#FFBEA3"
ORANGE_500 = "#FFB703"
BROWN_700 = "#84491F"

GREY_200 = "#CDDCEE"
GREY_400 = "#A6B8CC"
GREY_600 = "#8A7A95"
GREY_800 = "#6890A5"

PALETTE = {
    "PURPLE_200": PURPLE_200,
    "PURPLE_400": PURPLE_400,
    "PURPLE_600": PURPLE_600,
    "PURPLE_900": PURPLE_900,
    "BLUE_100": BLUE_100,
    "BLUE_300": BLUE_300,
    "BLUE_500": BLUE_500,
    "BLUE_700": BLUE_700,
    "BLUE_900": BLUE_900,
    "CYAN_200": CYAN_200,
    "CYAN_400": CYAN_400,
    "CYAN_700": CYAN_700,
    "GREEN_100": GREEN_100,
    "GREEN_300": GREEN_300,
    "GREEN_500": GREEN_500,
    "GREEN_700": GREEN_700,
    "YELLOW_300": YELLOW_300,
    "PINK_200": PINK_200,
    "RED_300": RED_300,
    "RED_500": RED_500,
    "RED_700": RED_700,
    "ORANGE_200": ORANGE_200,
    "ORANGE_500": ORANGE_500,
    "BROWN_700": BROWN_700,
    "GREY_200": GREY_200,
    "GREY_400": GREY_400,
    "GREY_600": GREY_600,
    "GREY_800": GREY_800,
}

PALETTE_USAGE_GUIDE = {
    "body_or_formula_base": (
        "BLUE_100",
        "GREY_200",
    ),
    "highlight_text_or_formula_term": (
        "CYAN_400",
        "GREEN_300",
        "YELLOW_300",
        "ORANGE_500",
        "PURPLE_400",
        "RED_500",
    ),
    "shape_structure": (
        "BLUE_300",
        "BLUE_500",
        "CYAN_400",
        "GREEN_500",
        "PURPLE_400",
        "ORANGE_500",
        "RED_500",
    ),
    "soft_note_or_secondary_label": (
        "GREY_400",
        "GREY_600",
        "CYAN_200",
        "GREEN_100",
        "RED_300",
        "ORANGE_200",
    ),
    "avoid_large_body_text": (
        "PURPLE_900",
        "BLUE_900",
        "CYAN_700",
        "BROWN_700",
        "GREY_800",
    ),
    "reserved_for_deep_stroke_or_decor": (
        "PURPLE_900",
        "BLUE_900",
        "CYAN_700",
        "BROWN_700",
        "GREY_800",
    ),
}

PALETTE_USAGE_NOTES = (
    "Use body_or_formula_base for long text and unhighlighted formulas.",
    "Use highlight_text_or_formula_term only for key words or selected formula terms.",
    "Use shape_structure for borders, arrows, nodes, and geometric structure.",
    "Use soft_note_or_secondary_label for muted labels, annotations, and secondary notes.",
    "Do not use avoid_large_body_text for large paragraphs or default formula color.",
)

TEXT_MAIN = A4L_TEXT_MAIN
TEXT_SUB = A4L_TEXT_SUB
TEXT_MUTED = "#AFC0D3"

HL_PRIMARY = CYAN_400
HL_SECONDARY = RED_500
HL_TERTIARY = PURPLE_400
HL_SUCCESS = GREEN_300
HL_ACCENT = YELLOW_300
