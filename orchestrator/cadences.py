"""Shared UTC interval catalogue used by validation, scheduling and UI."""

CADENCES = (
    (60, "1 minute"),
    (120, "2 minutes"),
    (300, "5 minutes"),
    (600, "10 minutes"),
    (900, "15 minutes"),
    (1800, "30 minutes"),
    (3600, "1 hour"),
    (7200, "2 hours"),
    (10800, "3 hours"),
    (14400, "4 hours"),
    (21600, "6 hours"),
    (28800, "8 hours"),
    (43200, "12 hours"),
    (86400, "24 hours"),
)
INTERVALS = frozenset(seconds for seconds, _ in CADENCES)


def cadence_label(seconds):
    label = dict(CADENCES).get(seconds, f"{seconds} seconds")
    if seconds == 60:
        return "Every minute"
    if seconds == 3600:
        return "Every hour"
    return "Every " + label


# Recommended intervals for new schedules; legacy intervals remain readable.
SCHEDULE_CADENCES = tuple(
    (seconds, label)
    for seconds, label in CADENCES
    if seconds in (3600, 7200, 14400, 21600, 43200, 86400)
)
