"""
app.py — Streamlit UI. This is the file you actually run.

    python3 -m streamlit run app.py

WHAT THIS FILE DOES: it collects trip preferences from the
sidebar, hands them to the real tool-calling agent in agent.py, and renders
whatever itinerary comes back. 
This file's only job is input -> hand off to the agent -> display the result.

WHY THIS MATTERS FOR THE "AGENT" DESIGN: in an earlier version of this
project, app.py called tools.py's three functions directly, in a fixed
order, and only used the LLM at the very end to do the day-by-day
reasoning. That was a deterministic pipeline with an LLM step bolted onto
the end of it — NOT a tool-calling agent. Now, app.py doesn't call
tools.py at all for actual planning. It only imports and calls
run_trip_planning_agent(), and the model itself decides when (and whether)
each underlying tool gets used. The on_tool_call callback below exists
purely so this file can still show live progress in the UI even though it
no longer controls the order those tool calls happen in.

ONE EXCEPTION to "app.py doesn't touch tools.py": destination_looks_valid()
below makes its own quick, direct call to the same free geocoding service
the agent's first tool uses. This is NOT planning logic — it's a cheap
sanity check run before the button click is allowed to start an expensive
agent run, so a typo like "asdlkfj" fails in under a second with a
friendly message instead of ~15 seconds later with a raw Python traceback.
"""

import time
import requests
import streamlit as st
from dotenv import load_dotenv

from agent import run_trip_planning_agent

# Load .env so OPENAI_API_KEY / SERPAPI_KEY / ORS_KEY are available as
# environment variables before anything else runs. (tools.py also calls
# this, but calling it again here is harmless — load_dotenv() is safe to
# call multiple times, and this makes app.py's own dependency on having
# those variables set explicit and self-contained.)
load_dotenv()

st.set_page_config(page_title="TripMate", page_icon="🗺️", layout="centered")


def check_destination(destination_name: str, max_retries: int = 3):
    """
    Confirms the destination resolves to a real place via Nominatim (the
    same free geocoding service agent.py's first tool call uses) BEFORE
    spending an expensive multi-round agent run on a typo.

    Deliberately separate from the agent's own tool-calling loop — this is
    input validation, not planning, and it never touches OpenAI/SerpApi/ORS.

    WHY THREE OUTCOMES INSTEAD OF TRUE/FALSE: an earlier version of this
    function failed OPEN on persistent 429s — if Nominatim stayed
    rate-limited through every retry, it just let the destination through
    unverified. In practice this meant testing two destinations back to
    back could exhaust the retry budget on the second one and silently
    wave real gibberish (e.g. "ghhghh") straight into a full agent run,
    defeating the entire point of validating first. Now a persistent 429
    is reported as "unknown" rather than silently treated as "valid", so
    the caller can tell the user to wait a moment instead of either
    blocking forever OR quietly letting garbage through.

    Returns:
        "valid"   — Nominatim found a real match.
        "invalid" — Nominatim reached us fine and found nothing.
        "unknown" — couldn't get a real answer (rate-limited on every
                    retry, or a network/timeout issue). The caller should
                    ask the user to wait and try again, NOT proceed as if
                    it were valid.
    """
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": destination_name, "format": "json", "limit": 1},
                headers={"User-Agent": "TripMate-Validator/1.0"},
                timeout=8,
            )
            response.raise_for_status()
            return "valid" if response.json() else "invalid"
        except requests.exceptions.HTTPError as e:
            is_rate_limited = e.response is not None and e.response.status_code == 429
            if is_rate_limited and attempt < max_retries:
                time.sleep(attempt)
                continue
            return "unknown"
        except requests.exceptions.RequestException:
            if attempt < max_retries:
                time.sleep(attempt)
                continue
            return "unknown"

    return "unknown"


def pick_emoji_for_category(category_text: str) -> str:
    """
    Small COSMETIC helper: pick a fun emoji for a stop based on its
    category text, purely so the itinerary is easier to scan at a glance
    in the UI. This has zero effect on the actual planning logic — it's
    pure presentation, computed fresh every time the UI renders a stop.

    HOW IT WORKS: does a simple case-insensitive substring check against a
    small list of keyword -> emoji mappings, in order, and returns the
    first match. Falls back to a generic pin emoji if nothing matches
    (e.g. an unusual category Google Maps returned that isn't in our list).

    Args:
        category_text: whatever category string came back from the model's
                        final answer for this stop (originally sourced from
                        Google Maps via search_points_of_interest).

    Returns:
        A single emoji character/string.
    """
    category_text = (category_text or "").lower()
    keyword_to_emoji = {
        "museum": "🏛️", "art": "🖼️", "gallery": "🖼️",
        "restaurant": "🍽️", "food": "🍽️", "cafe": "☕",
        "park": "🌳", "garden": "🌳", "nature": "🌳",
        "church": "⛪", "cathedral": "⛪", "temple": "⛩️", "historic": "🏰",
        "view": "🌅", "viewpoint": "🌅", "lookout": "🌅",
        "beach": "🏖️", "market": "🛍️", "shopping": "🛍️",
        "bar": "🍸", "nightlife": "🌙", "zoo": "🦁", "aquarium": "🐠",
    }
    for keyword, emoji in keyword_to_emoji.items():
        if keyword in category_text:
            return emoji
    return "📍"  # default: a plain map pin for anything unmatched


# Friendly, human-readable labels shown in the live progress log for each
# tool name the agent might decide to call. Purely cosmetic — translates
# raw Python function names (which the agent actually calls) into
# something a non-technical user would find reassuring to read while
# waiting, rather than seeing "search_points_of_interest" verbatim.
TOOL_NAME_TO_FRIENDLY_LABEL = {
    "geocode_destination": "📍 Looking up the destination's coordinates...",
    "search_points_of_interest": "🔎 Searching for places matching your interests...",
    "get_walking_time_matrix": "🚶 Calculating real walking times...",
}


st.title("🗺️ TripMate")
st.caption("Tell me where and how long — I'll build a real, walkable day-by-day plan around what you actually like.")

# ---------------------------------------------------------------------------
# A curated list of popular destinations for the dropdown. This is just a
# starting point for convenience — "Other (type my own)" falls through to
# a free-text box, so the destination is never actually LIMITED to this
# list, just defaulted to it for a faster, guided experience.
# ---------------------------------------------------------------------------
DESTINATION_OPTIONS = [
    "Lisbon, Portugal",
    "Barcelona, Spain",
    "Rome, Italy",
    "Paris, France",
    "Amsterdam, Netherlands",
    "Prague, Czech Republic",
    "Athens, Greece",
    "Vienna, Austria",
    "Berlin, Germany",
    "Istanbul, Turkey",
    "Marrakech, Morocco",
    "Bangkok, Thailand",
    "Tokyo, Japan",
    "Other (type my own)",
]

# ---------------------------------------------------------------------------
# A curated list of common interest categories for the checkboxes. These
# are just LABELS shown in the UI — whichever ones the user checks get
# joined into a comma-separated string at the bottom, so everything
# downstream (agent.py, tools.py) still receives interests_text exactly
# the same way it always did. No other file needs to change for this.
# ---------------------------------------------------------------------------
INTEREST_OPTIONS = [
    "Museums", "Viewpoints", "Local food", "Parks & nature",
    "Historic sites", "Art & galleries", "Nightlife", "Shopping", "Beaches",
]

# ---------------------------------------------------------------------------
# Sidebar: collect every piece of input the agent needs before it starts.
# None of these values are validated here except the checkbox-join —
# destination validity is checked separately, right after the button click,
# so the sidebar itself stays fast and simple.
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("✈️ Trip details")

    selected_destination_option = st.selectbox("📍 Destination", DESTINATION_OPTIONS)
    if selected_destination_option == "Other (type my own)":
        # Fallback free-text box, only shown when the user picks "Other" —
        # this is what keeps the dropdown from actually limiting anyone.
        destination_name = st.text_input("Type your destination", "")
    else:
        destination_name = selected_destination_option

    number_of_days = st.slider("📅 Duration (days)", 1, 7, 3)
    pace = st.select_slider("🏃 Pace", options=["relaxed", "moderate", "packed"], value="moderate")

    st.markdown("❤️ **What are you into?**")
    checked_interests = []
    for interest_label in INTEREST_OPTIONS:
        # Default a couple of sensible checkboxes to True so a first-time
        # user doesn't have to check everything themselves before generating.
        default_checked = interest_label in ("Museums", "Local food")
        if st.checkbox(interest_label, value=default_checked, key=f"interest_{interest_label}"):
            checked_interests.append(interest_label.lower())

    # This is the ONLY line that needs to exist for the rest of the app to
    # keep working unchanged — it turns the checkbox selections back into
    # the same comma-separated interests_text string the agent already expects.
    interests_text = ", ".join(checked_interests)

    number_of_people = st.number_input("👥 Number of people", min_value=1, max_value=20, value=2)
    total_budget_usd = st.number_input("💰 Total budget in USD (0 = no limit)", min_value=0, value=0, step=50)
    st.divider()

    # Guard against generating with no destination or no interests picked,
    # since both would otherwise silently produce a broken or generic trip.
    # (Destination REALNESS — as opposed to just non-empty — is checked
    # after the click, so we don't hit Nominatim on every keystroke.)
    can_generate = bool(destination_name.strip()) and bool(checked_interests)
    if not can_generate:
        st.caption("⚠️ Pick a destination and at least one interest to continue.")
    generate_button_clicked = st.button(
        "Generate itinerary", type="primary", use_container_width=True, disabled=not can_generate
    )


if generate_button_clicked:

    # -------------------------------------------------------------------
    # STEP 0: validate the destination BEFORE spending an agent run on it.
    # This is what turns "ri" or "asdlkfj" into an instant, friendly error
    # instead of a ~15-second wait followed by a raw HTTPError traceback.
    # -------------------------------------------------------------------
    with st.spinner("Checking destination..."):
        validation_result = check_destination(destination_name)

    if validation_result == "invalid":
        st.error(
            f"⚠️ Couldn't find **\"{destination_name}\"** as a real place. "
            "Check the spelling, or try a broader name (e.g. 'Porto' instead of a specific street)."
        )
        st.stop()
    elif validation_result == "unknown":
        st.warning(
            "⏳ The geocoding service is temporarily rate-limited and we couldn't verify "
            f"**\"{destination_name}\"** just now. Please wait a few seconds and click Generate again."
        )
        st.stop()
    # validation_result == "valid" -> fall through and proceed

    # Nominatim allows ~1 request/second. The pre-check above JUST hit it,
    # and the agent's own first tool call is about to hit it again — this
    # short pause keeps the two calls from landing in the same second and
    # triggering a self-inflicted 429 (this was happening in practice on
    # Streamlit Cloud, where outbound IPs are shared across many apps).
    time.sleep(1)

    # st.status(...) creates an expandable progress box in the UI. We keep
    # it open (expanded=True) for the whole run so the user can watch each
    # tool call happen live, rather than staring at a blank spinner for
    # the 10-20 seconds a full agent run typically takes.
    with st.status("🤖 Agent is planning your trip...", expanded=True) as status:

        def show_tool_call_progress(tool_name: str):
            """
            This is the on_tool_call callback passed into
            run_trip_planning_agent(). agent.py calls this function by
            name — right before it actually executes each tool — passing
            in whichever tool_name the model decided to call. We don't
            know in advance how many times this will be called, or in
            what order, since that's entirely up to the model.
            """
            friendly_label = TOOL_NAME_TO_FRIENDLY_LABEL.get(tool_name, f"Calling {tool_name}...")
            st.write(friendly_label)

        # -----------------------------------------------------------
        # STEP 1: hand off to the agent. Wrapped in try/except as a
        # SECOND line of defense — the pre-check above catches most bad
        # destinations, but this catches anything else that can still go
        # wrong deeper in the loop (a flaky API, a search that comes back
        # empty, the model exceeding MAX_TOOL_CALL_ROUNDS, etc.) and shows
        # a plain-English message instead of a raw traceback either way.
        # -----------------------------------------------------------
        try:
            itinerary = run_trip_planning_agent(
                destination_name,
                number_of_days,
                interests_text,
                pace,
                total_budget_usd=total_budget_usd if total_budget_usd > 0 else None,
                number_of_people=number_of_people,
                on_tool_call=show_tool_call_progress,
            )
            status.update(label="✅ Your trip is ready!", state="complete")
        except Exception as e:
            status.update(label="❌ Something went wrong", state="error")
            st.error(
                "The agent ran into a problem while planning this trip. "
                "This can happen with very obscure destinations or a temporary API hiccup — try again, "
                "or try a nearby larger city."
            )
            with st.expander("Technical details (for debugging)"):
                st.code(str(e))
            st.stop()

    # -------------------------------------------------------------------
    # Display the result. Because agent.py guarantees itinerary matches
    # DAY_BY_DAY_ITINERARY_SCHEMA exactly (strict structured output), every
    # key access below (itinerary["destination_name"], stop["place_name"],
    # etc.) is safe without defensive .get() calls or existence checks —
    # the schema itself is the guarantee.
    # -------------------------------------------------------------------
    st.header(f"{itinerary['destination_name']} · {itinerary['number_of_days']} days")

    # Three headline numbers side by side, instead of burying them in a
    # sentence — quicker for the user to scan before diving into the
    # day-by-day details below.
    stat_col1, stat_col2, stat_col3 = st.columns(3)
    stat_col1.metric("👥 Travelers", itinerary["number_of_people"])
    stat_col2.metric("💵 Estimated cost", f"${itinerary['total_estimated_cost_usd']:.0f}")
    stat_col3.metric("🏃 Pace", pace.capitalize())
    st.info(f"💡 {itinerary['budget_summary']}")

    st.divider()

    # One Streamlit tab per day of the trip, so the user flips between
    # days instead of scrolling through one long page with every day
    # stacked on top of each other.
    day_tabs = st.tabs([f"Day {day['day_number']}" for day in itinerary["days"]])

    for day_tab, day in zip(day_tabs, itinerary["days"]):
        with day_tab:
            st.subheader(f"🗓️ {day['day_theme']}")

            # The model is asked to number stops via visit_order starting
            # at 1, but nothing guarantees the JSON array itself is already
            # in that order — sort defensively so display order always
            # matches the intended visiting order regardless.
            stops_in_visit_order = sorted(day["stops"], key=lambda stop: stop["visit_order"])

            for stop in stops_in_visit_order:
                walking_minutes = stop["walking_minutes_from_previous_stop"]  # None for the first stop of the day
                cost_per_person = stop["estimated_cost_per_person_usd"]
                cost_display_text = f"${cost_per_person:.0f}/person" if cost_per_person else "Free"
                emoji = pick_emoji_for_category(stop["category"])

                # Build the header text for this stop's expandable card.
                # Only mention walking time if there IS a previous stop —
                # the first stop of each day has walking_minutes as None,
                # and we don't want to print "None min away".
                card_header = f"{emoji} {stop['visit_order']}. {stop['place_name']}"
                if walking_minutes:
                    card_header += f"  ·  🚶 {walking_minutes} min away"

                # Each stop renders as its own collapsible card
                # (expanded=True by default so everything's visible at
                # once, but the user CAN collapse individual stops if they
                # want a shorter view). This keeps a busy 5-stop day
                # scannable instead of one long unbroken wall of text.
                with st.expander(card_header, expanded=True):
                    st.markdown(stop["why_this_stop"])
                    badge_col1, badge_col2, badge_col3 = st.columns(3)
                    badge_col1.markdown(f"🏷️ **{stop['category']}**")
                    badge_col2.markdown(f"⏱️ **{stop['estimated_visit_minutes']} min**")
                    badge_col3.markdown(f"💵 **{cost_display_text}**")