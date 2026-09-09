"""
schema.py — Schemas for TripMate, matching the notebook's Responses API style.

TWO KINDS OF SCHEMA LIVE HERE:

1. TRIP_PLANNING_TOOLS — flat function-tool definitions: {"type": "function",
   "name": ..., "parameters": {...}, "strict": True}.

2. DAY_BY_DAY_ITINERARY_SCHEMA — the raw JSON schema for the FINAL answer,
   used only in agent.py's second (structured-output) call.
"""

TRIP_PLANNING_TOOLS = [
    {
        "type": "function",
        "name": "geocode_destination",
        "description": (
            "Turn a destination name (e.g. 'Lisbon, Portugal') into latitude/"
            "longitude coordinates. Call this first, before searching for places."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "destination_name": {
                    "type": "string",
                    "description": "The destination as given by the traveler, e.g. 'Lisbon, Portugal'."
                }
            },
            "required": ["destination_name"],
            "additionalProperties": False
        },
        "strict": True
    },
    {
        "type": "function",
        "name": "search_points_of_interest",
        "description": (
            "Search for candidate places near a coordinate, using the traveler's "
            "own interests as the search terms. Call this after geocode_destination, "
            "using the coordinates it returned. Use max_results=30 unless you have "
            "a specific reason not to. The result tells you, per interest, how many "
            "places were found — but a place matching the search TEXT is not the same "
            "as it genuinely satisfying that interest (e.g. a restaurant named 'Beach "
            "___' is not a real beach). Critically judge each result before treating "
            "it as a real match. If, after that judgment, an interest has zero genuine "
            "matches, it's simply not available at this destination; do not treat that "
            "as an error, note it honestly in unmatched_interests_note, and build the "
            "itinerary around the traveler's other interests plus general highlights "
            "instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
                "interests_text": {
                    "type": "string",
                    "description": "Comma-separated traveler interests, e.g. 'museums, viewpoints, local food'."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of candidate places to return."
                }
            },
            "required": ["latitude", "longitude", "interests_text", "max_results"],
            "additionalProperties": False
        },
        "strict": True
    },
    {
        "type": "function",
        "name": "get_walking_time_matrix",
        "description": (
            "Get real walking times (in seconds) between every pair of places you "
            "provide. Call this AFTER you've picked the specific places you're "
            "considering including in the itinerary — pass only those, not the "
            "entire raw search pool, since a smaller list is cheaper and faster "
            "to compute distances for."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "places": {
                    "type": "array",
                    "description": "The places you want real walking distances between.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "place_id": {"type": "string"},
                            "name": {"type": "string"},
                            "category": {"type": "string"},
                            "latitude": {"type": "number"},
                            "longitude": {"type": "number"},
                            "google_rating": {"type": "number"}
                        },
                        "required": ["place_id", "name", "category", "latitude", "longitude", "google_rating"],
                        "additionalProperties": False
                    }
                }
            },
            "required": ["places"],
            "additionalProperties": False
        },
        "strict": True
    }
]


DAY_BY_DAY_ITINERARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {

        "destination_name": {
            "type": "string",
            "description": "Name of the destination city/region."
        },
        "number_of_days": {
            "type": "integer",
            "description": "Total number of days in the trip."
        },
        "number_of_people": {
            "type": "integer",
            "description": "Number of travelers this plan is built for."
        },
        "total_estimated_cost_usd": {
            "type": "number",
            "description": (
                "Estimated total cost in USD for the whole trip, for all "
                "travelers combined (entrance fees, meals, local transport). "
                "Excludes flights/hotels."
            )
        },
        "budget_summary": {
            "type": "string",
            "description": (
                "One or two sentences: how the estimated cost compares to "
                "the traveler's stated budget, and any trade-offs made to fit it."
            )
        },

        # ---------------------------------------------------------------
        # NEW FIELD: an honest, explicit place for the agent to say
        # "I looked for X and couldn't find it here" instead of silently
        # dropping an interest or (worse) inventing fake matches for it.
        # Empty string is valid and expected when every interest was
        # matched with real places.
        # ---------------------------------------------------------------
        "unmatched_interests_note": {
            "type": "string",
            "description": (
                "If one or more of the traveler's stated interests had ZERO real "
                "search results at this destination, name which ones here and say "
                "so plainly (e.g. 'No beaches were found near Riyadh, since it's "
                "landlocked — the itinerary focuses on your other interests "
                "instead.'). Leave this as an empty string if every interest was "
                "matched with real places."
            )
        },

        "days": {
            "type": "array",
            "description": "One entry per day of the trip, in visiting order.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "day_number": {"type": "integer"},
                    "day_theme": {
                        "type": "string",
                        "description": "Short label for the day, e.g. 'Old Town & Viewpoints'."
                    },
                    "stops": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "place_name": {"type": "string"},
                                "category": {"type": "string"},
                                "latitude": {"type": "number"},
                                "longitude": {"type": "number"},
                                "visit_order": {
                                    "type": "integer",
                                    "description": "Order within the day, starting at 1."
                                },
                                "estimated_visit_minutes": {
                                    "type": "integer",
                                    "description": "How long to spend at this stop."
                                },
                                "estimated_cost_per_person_usd": {
                                    "type": "number",
                                    "description": (
                                        "Estimated cost per person in USD (entrance "
                                        "fee, typical meal cost, etc). Use 0 for free stops."
                                    )
                                },
                                "walking_minutes_from_previous_stop": {
                                    "type": ["integer", "null"],
                                    "description": (
                                        "Minutes walked from the previous stop. "
                                        "Null for the first stop of the day."
                                    )
                                },
                                "why_this_stop": {
                                    "type": "string",
                                    "description": "One short sentence: why this stop, why this slot."
                                }
                            },
                            "required": [
                                "place_name", "category", "latitude", "longitude",
                                "visit_order", "estimated_visit_minutes",
                                "estimated_cost_per_person_usd",
                                "walking_minutes_from_previous_stop", "why_this_stop"
                            ]
                        }
                    }
                },
                "required": ["day_number", "day_theme", "stops"]
            }
        }
    },
    "required": [
        "destination_name", "number_of_days", "number_of_people",
        "total_estimated_cost_usd", "budget_summary",
        "unmatched_interests_note", "days"
    ]
}