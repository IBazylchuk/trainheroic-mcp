"""Parameter types used by TrainHeroic set prescriptions, and unit conversion.

Every exercise has up to two tracked parameters (``param_1_type`` / ``param_2_type``),
e.g. Reps + Weight. The API stores weight-typed values in pounds regardless of the
athlete's display preference; the apps convert to kg for display.
"""

from enum import IntEnum

LB_PER_KG = 2.20462262185


class ParamType(IntEnum):
    NONE = 0
    WEIGHT = 1  # pounds
    WEIGHT_PERCENT = 2
    REPS = 3
    TIME = 4  # seconds
    DISTANCE_YARDS = 5
    DISTANCE_METERS = 6
    HEIGHT_INCHES = 7
    CALORIES = 8
    LINEAR = 9
    DISTANCE_MILES = 10
    DISTANCE_FEET = 11
    DISTANCE_INCHES = 12
    RPE = 14
    WATTS = 15
    VELOCITY = 17
    SECONDS = 18
    WEIGHT_KG = 19
    REP_RANGE = 24

    @classmethod
    def parse(cls, value: object) -> "ParamType":
        """Parse the loosely typed values the API returns ("3", 3, None, "None")."""
        try:
            return cls(int(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return cls.NONE


def kg_to_lb(kg: float) -> float:
    return round(kg * LB_PER_KG, 2)


def lb_to_kg(lb: float) -> float:
    return round(lb / LB_PER_KG, 1)


def format_number(value: float | int | str) -> str:
    """Render a number the way the API stores it: no trailing zeros, no exponent."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")
