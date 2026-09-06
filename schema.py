"""
schema.py — Schemas for TripMate

WHAT LIVES IN THIS FILE AND WHY:

LLMs return free-form text by default. That's fine for a chat answer, but
useless for a program that needs to loop over "days" and "stops" in Python
and render them as Streamlit tabs. So we force the model's output into an
exact, predictable shape using JSON Schema either as tool "parameters"
(so tool calls always have the arguments we expect) or as the final
structured-output format (so the finished answer always has the fields our
UI code expects).

TWO KINDS OF SCHEMA LIVE HERE:
1. TRIP_PLANNING_TOOLS: flat function-tool definitions,
   TOOLS list: {"type": "function", "name": ..., "parameters":
   {...}, "strict": True}. 

2. DAY_BY_DAY_ITINERARY_SCHEMA: the raw JSON schema object.
   response_schema is defined once as a plain schema, then wrapped inline
   wherever it's passed to the API.
"""

# ---------------------------------------------------------------------------
# TRIP_PLANNING_TOOLS: the functions the agent can choose to call.
#
# IMPORTANT: the model decides for itself which of these three tools to
# call, and in what order, based on the instructions in agent.py's
# SYSTEM_PROMPT. 
# ---------------------------------------------------------------------------
TRIP_PLANNING_TOOLS = [

    # -------------------------------------------------------------------
    # TOOL 1: geocode_destination
    #
    # Purpose: turn a place name into coordinates. This has to run before
    # the other two tools, since they both need a latitude/longitude to
    # work from that dependency is explained to the model in the
    # "description" field below and reinforced in SYSTEM_PROMPT.
    # -------------------------------------------------------------------
    {
        "type": "function",

        "name": "geocode_destination",

        # This description is the ONLY information the model has about what
        # this tool does and when to use it
        "description": (
            "Turn a destination name (e.g. 'Lisbon, Portugal') into latitude/"
            "longitude coordinates. Call this first, before searching for places."
        ),

        # "parameters" is a JSON Schema describing the arguments the model
        # must supply when it calls this tool. The model will generate a
        # JSON object matching this shape as the tool call's "arguments".
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

    # -------------------------------------------------------------------
    # TOOL 2: search_points_of_interest
    #
    # Purpose: search Google Maps (via SerpApi under the hood) for real
    # candidate places near a coordinate, using the traveler's own stated
    # interests as the literal search terms.
    # -------------------------------------------------------------------
    {
        "type": "function",
        "name": "search_points_of_interest",
        "description": (
            "Search for candidate places near a coordinate, using the traveler's "
            "own interests as the search terms. Call this after geocode_destination, "
            "using the coordinates it returned. Use max_results=30 unless you have "
            "a specific reason not to."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                # These two come from the PREVIOUS tool's result
                # (geocode_destination's output) the model is expected to
                # read that result and pass it along here, which is exactly
                # what "tool calling" means: results from one call feeding
                # into the next call's arguments, decided by the model.
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},

                "interests_text": {
                    "type": "string",
                    "description": "Comma-separated traveler interests, e.g. 'museums, viewpoints, local food'."
                },

                # We ask the model to explicitly pass this every time (strict
                # mode requires it to be in "required") rather than relying
                # on a Python-side default, so the model's choice is always
                # visible in the tool call itself — easier to debug.
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

    # -------------------------------------------------------------------
    # TOOL 3: get_walking_time_matrix
    #
    # Purpose: get REAL walking times between a set of places, so the
    # itinerary is grounded in actual distances instead of the model
    # guessing "these two museums are probably close together" from their
    # names alone.
    # -------------------------------------------------------------------
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
                            # These fields mirror exactly what
                            # search_points_of_interest returns for each
                            # place, so the model can copy entries straight
                            # out of that tool's earlier result into this
                            # tool's "places" argument without reshaping them.
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


# ---------------------------------------------------------------------------
# DAY_BY_DAY_ITINERARY_SCHEMA the raw JSON schema for the FINAL answer.
#
# WHEN THIS IS USED: this is NOT sent to the model during the tool-calling
# loop at all. It's used exactly once, in agent.py's SECOND
# client.responses.create(...) call — the one that runs AFTER the
# tool-calling loop has finished and the model has already written its
# complete plain-text answer. That second call's only job is to reformat
# the finished text into this exact JSON shape; it has no tools, and no
# ability to look anything up again. This mirrors your notebook's
# "Structured Output" step at the end of complete_agent().
#
# WHY THIS MATTERS FOR PROMPTING: because this conversion step can't see
# the raw tool results anymore (only the finished text), agent.py's
# SYSTEM_PROMPT has to explicitly instruct the model to include every
# field this schema needs — exact coordinates, costs, walking times, etc.
# — directly in its plain-text final answer. If a field ever comes back
# empty or wrong, the first place to look is whether SYSTEM_PROMPT asked
# for that detail clearly enough.
# ---------------------------------------------------------------------------
DAY_BY_DAY_ITINERARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {

        # --- Trip-level fields: appear ONCE, describing the whole trip ---
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

        # --- Day-level fields: one array entry PER DAY of the trip ---
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

                    # --- Stop-level fields: one array entry PER PLACE visited that day ---
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
                                # This field uses a TYPE UNION (["integer", "null"])
                                # instead of being left optional, because strict
                                # mode requires every property to be in "required".
                                # A union with null is how you say "this value is
                                # sometimes legitimately absent" while still
                                # satisfying that rule.
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
        "total_estimated_cost_usd", "budget_summary", "days"
    ]
}