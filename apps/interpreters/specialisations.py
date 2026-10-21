"""The library of specialisations an interpreter can offer.

An interpreter picks one or more of these when they sign up. Requests are still routed by one of
three contexts (General, Medical, Legal), so ``primary_for`` maps a selection onto the single
``InterpreterProfile.specialisation`` value that routing uses.
"""

from .models import Specialisation

# (value, label). Order is the order shown on the sign-up form.
SPECIALISATION_LIBRARY = (
    ("GENERAL", "General / everyday"),
    ("MEDICAL", "Medical / health"),
    ("LEGAL", "Legal / court"),
    ("EDUCATION", "Education / school"),
    ("RELIGIOUS", "Religious / church or mosque"),
    ("SECURITY", "Police / security"),
    ("GOVERNMENT", "Government / public services"),
    ("EMPLOYMENT", "Employment / workplace"),
    ("FINANCIAL", "Banking / financial"),
    ("MENTAL_HEALTH", "Mental health / counselling"),
    ("SOCIAL_SERVICES", "Social services / community"),
    ("MEDIA", "Media / broadcast"),
    ("TECHNICAL", "Technical / IT"),
    ("EVENTS", "Conferences / events"),
    ("OTHER", "Other (please specify)"),
)

LABELS = dict(SPECIALISATION_LIBRARY)
VALUES = tuple(LABELS)

# The values routing understands; the first one found in a selection becomes the primary.
_ROUTING_PRIORITY = (Specialisation.MEDICAL, Specialisation.LEGAL, Specialisation.GENERAL, Specialisation.OTHER)


def clean_selection(values):
    """Remove duplicates, keep the library order. Raises ``ValueError`` for an unknown value."""
    chosen = set(values or ())
    unknown = sorted(chosen - set(VALUES))
    if unknown:
        raise ValueError(f"Unknown specialisation: {', '.join(unknown)}")
    return [value for value in VALUES if value in chosen]


def primary_for(selected):
    """The single value used for routing and the admin list."""
    for value in _ROUTING_PRIORITY:
        if value in selected:
            return value.value
    # Only library-only specialisations (e.g. Education): they handle general requests.
    return Specialisation.GENERAL.value


def label_for(value, other_text=""):
    if value == "OTHER" and other_text:
        return other_text
    return LABELS.get(value, value)
