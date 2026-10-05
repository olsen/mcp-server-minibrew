"""Human-readable labels for MiniBrew's numeric codes.

Process states come from the minibrew/enduser-docker-server dashboard
(backend/state_engine.py, observed on devices); user actions, session and device
codes from the Brewery Portal API notes in that repo. Gaps were filled from the
enums of MiniBrew's compiled protobuf client as copied by stuartp44/pymbrewclient
(process types and phases, more states and actions). Unknown codes are passed
through as numbers by the caller.
"""

DEVICE_TYPES = {0: "brewer", 1: "keg"}

# current_state on /devices/.
DEVICE_STATES = {0: "idle", 1: "running", 2: "waiting for user"}

# connection_status on /devices/.
CONNECTION_STATUSES = {0: "offline", 1: "online", 2: "not responding"}

# process_type on /devices/ and in live telemetry.
PROCESS_TYPES = {
    0: "idle",
    1: "brewing",
    2: "cleaning",
    3: "keg cleaning",
    4: "fermentation",
    5: "serving",
    6: "acid cleaning",
}

# process_phase in live telemetry.
PROCESS_PHASES = {
    1: "brew: preparing",
    2: "brew: mashing",
    3: "brew: boiling",
    4: "brew: chilling",
    5: "fermentation: primary",
    6: "fermentation: secondary",
    7: "fermentation: clarification",
    8: "serving",
    9: "clean: preparing",
    10: "clean: cleaning",
    11: "clean: rinsing",
    12: "fermentation: preparing",
    13: "fermentation: carbonating",
    14: "acid clean: soaking",
    15: "acid clean: cleaning",
    16: "acid clean: rinsing",
}

SESSION_TYPES = {0: "brew", 2: "clean"}

SESSION_STATUSES = {1: "active", 2: "completed", 4: "completed", 6: "cancelled"}

PROCESS_STATES = {
    0: "Idle",
    5: "Manual control",
    6: "Prepare rinse",
    7: "Check keg",
    8: "Check water",
    9: "Rinse flow recovery",
    10: "Pump priming",
    11: "Check flow",
    12: "Heat-up rinse",
    13: "Clean ball valve",
    14: "Rinse boiling path",
    15: "Rinse mashing path",
    16: "Rinse done",
    17: "Fill machine",
    18: "Rinse cool",
    20: "Hydrate mash bed: heating",
    21: "Hydrate mash bed: flow",
    22: "Hydrate mash bed",
    23: "Hydrate mash bed: settle",
    24: "Mash in",
    30: "Mash heat-up",
    31: "Mashing",
    32: "Mash rest",
    33: "Mash line flow recovery",
    39: "Sparging",
    40: "Lautering",
    43: "Replace mash",
    50: "Boil heat-up",
    51: "Boiling",
    52: "Secondary lautering",
    53: "Boil line flow recovery",
    59: "Connect water",
    60: "Cool wort",
    61: "Filter / cold crash",
    65: "Write keg",
    70: "Brew done",
    71: "Brew failed",
    74: "Pitch cooling",
    75: "Prepare fermentation",
    76: "Place airlock",
    77: "Remove airlock",
    78: "Place trub container",
    80: "Fermentation temperature control",
    81: "Remove trub",
    82: "Fermentation: add ingredient",
    83: "Fermentation: remove ingredient",
    84: "Fermentation failed",
    85: "Pair gravity sensor",
    88: "Prepare serving",
    90: "Cool before serving",
    91: "Mount tap",
    92: "Serving temperature control",
    93: "Serving failed",
    94: "Carbonating",  # observed Oct 2026: keg in stage "Carbonating"
    101: "Prepare CIP",
    102: "Drain",
    103: "CIP heat-up",  # the dashboard calls it "Backflush"; the protobuf enum CIP_HEATUP
    105: "Place CIP accessories",
    108: "CIP done",
    109: "CIP failed",
    111: "Circulate boiling path",
    112: "Circulate mashing path",
    113: "Rinse counterflow (boil)",
    114: "Rinse counterflow (mash tun)",
    115: "Cleaning flow recovery",
    116: "CIP soak",
    117: "Prepare acid clean",
    118: "Acid soak",
    119: "Acid: circulate boiling path",
    120: "Clean filter",
    121: "Acid: circulate mashing path",
    122: "Acid: rinse boiling path",
    123: "Acid: rinse mashing path",
    124: "Acid clean done",
    125: "Acid clean failed",
    126: "Acid clean flow recovery",
}

PHASES = {
    "brewing": (20, 21, 22, 23, 24, 30, 31, 32, 33, 39, 40, 43, 50, 51, 52, 53, 59, 60, 61, 65)
    + (70, 71, 74),
    "fermentation": (75, 76, 77, 78, 80, 81, 82, 83, 84, 85),
    "serving": (88, 90, 91, 92, 93, 94),
    "cleaning": (
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        101,
        102,
        103,
        105,
        108,
        109,
        111,
        112,
        113,
        114,
        115,
        116,
    ),
    "acid cleaning": tuple(range(117, 127)),
}
STATE_TO_PHASE = {state: phase for phase, states in PHASES.items() for state in states}

FAILED_STATES = {71, 84, 93, 109, 125}

# pending_command_error: the last command sent to the device did not go through.
COMMAND_ERRORS = {3: "unknown error", 4: "command failed / device unresponsive"}

USER_ACTIONS = {
    0: None,
    1: "Add brew water",
    2: "Prepare cleaning",
    3: "Check activity started",
    4: "Add ingredient",
    5: "Remove ingredient",
    6: "Connect water",
    7: "Place keg",
    8: "Remove trub",
    9: "Check activity stopped",
    10: "Mount tap",
    11: "Remove keg",
    12: "Needs cleaning",
    13: "Brewing failed",
    15: "Prepare rinsing",
    16: "Add rinse water",
    17: "Clean the ball valve",
    18: "Empty keg",
    19: "Fill mash tun",
    20: "Fill carousel",
    21: "Start brewing",
    22: "Manual sparge",
    23: "Replace mash",
    24: "Place carousel",
    25: "Resume brewing",
    26: "Prepare fermentation",
    28: "Place airlock",
    29: "Remove airlock",
    30: "Place trub container",
    31: "Connect pressurizer",
    32: "Start cleaning",
    33: "Fermentation failed",
    34: "Serving failed",
    35: "Cleaning failed",
    36: "Pair gravity sensor",
    37: "CIP finished",
    38: "Keg needs cleaning",
    39: "Check keg's presence",
    40: "Check the ball valve",
    41: "Check rinse connector",
    42: "Check inlet water connection",
    43: "Check the keg is empty",
    44: "Check rinse flow",
    45: "Check the mash tun",
    46: "Check the carousel",
    47: "Check mash line flow",
    48: "Check boil line flow",
    49: "Check the CIP container",
    50: "Check the CIP lines",
    51: "Check the cleaning flow",
    52: "Check the keg is placed correctly",
    53: "Place blow-off",
    54: "Remove blow-off",
    55: "Check the carousel for a jam",
    56: "Prepare acid cleaning",
    57: "Clean the CIP filter",
    58: "Check the acid cleaning flow",
    59: "Acid cleaning failed",
    60: "Finish acid cleaning",
    61: "Brew failed: acid clean procedure",
    62: "CIP failed: acid clean procedure",
    63: "Needs acid cleaning",
    64: "Keg needs a firmware update",
    65: "Check carbonation",
    66: "Replace the mash tun",
    67: "Max cooling elapsed",
}


def label(table: dict, code):
    """The label for ``code``, or the code itself when it is unknown."""
    return table.get(code, code) if code is not None else None
