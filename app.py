"""
app.py — Streamlit UI. This is the file you actually run.

    python3 -m streamlit run app.py

WHAT THIS FILE DOES: it collects trip preferences from the sidebar, hands
them to the real tool-calling agent in agent.py, and renders whatever
itinerary comes back. This file's only job is input -> hand off to the
agent -> display the result.

ONE EXCEPTION to "app.py doesn't touch tools.py's logic": check_destination()
below makes its own quick, direct call to the same geocoding provider
agent.py's first tool uses. This is NOT planning logic — it's a cheap
sanity check run before the button click is allowed to start an expensive
agent run, so a typo like "asdlkfj" fails in under a second with a
friendly message instead of ~15 seconds later with a raw error.
"""

import os
import re
import time
import requests
import streamlit as st
from dotenv import load_dotenv

from agent import run_trip_planning_agent

load_dotenv()

GEOAPIFY_KEY = st.secrets["GEOAPIFY_KEY"]  # https://myprojects.geoapify.com (free tier: 3000 req/day)

st.set_page_config(page_title="TripMate", page_icon="🗺️", layout="centered")


# ---------------------------------------------------------------------------
# English-only input check. Allows plain Latin letters, digits, spaces, and
# the punctuation actually needed for place names (comma, period,
# apostrophe, hyphen) — rejects other scripts (Arabic, Chinese, Cyrillic,
# emoji, etc). This deliberately also rejects purely-accented names like
# "México" or "Zürich" — a simplification, not a claim that those aren't
# valid places, just a literal "English characters only" rule as requested.
# ---------------------------------------------------------------------------
ENGLISH_ONLY_PATTERN = re.compile(r"^[A-Za-z0-9\s,.'\-]*$")


def is_english_only(text: str) -> bool:
    return bool(ENGLISH_ONLY_PATTERN.match(text))


def check_destination(destination_name: str, max_retries: int = 2):
    """
    Confirms the destination resolves to a real place, BEFORE spending an
    expensive multi-round agent run on a typo.

    USES GEOAPIFY, NOT NOMINATIM. Confirmed root cause: Nominatim rate-
    limits per SOURCE IP (~1 req/sec), and Streamlit Community Cloud
    apps share a small, published pool of outbound IPs across the ENTIRE
    platform — every app hosted there, from every user. A real 429
    response (confirmed from Nominatim's own response body) persisted
    for over an hour and failed even on correctly-spelled real
    destinations — ruling out "just a transient rate limit." Geoapify
    rate-limits per API KEY instead of per IP, sidestepping the shared-IP
    problem entirely.

    ALSO CHECKS CONFIDENCE, NOT JUST "DID IT FIND ANYTHING": Geoapify's
    fuzzy matching can match a short/garbled string to something real but
    wrong — e.g. "riyd" once matched a location in Morocco instead of
    Riyadh. "Found a match" and "found the RIGHT match" aren't the same
    thing. Geoapify returns properties.rank.confidence (0.0-1.0) per
    result; anything below CONFIDENCE_THRESHOLD is treated as "invalid"
    rather than blindly trusted, so ambiguous input gets rejected here
    instead of confusing the agent later.

    Caches CONFIRMED results ("valid"/"invalid") in st.session_state for
    the rest of the session, so repeat tests of the same typed destination
    don't burn another API call. Deliberately does NOT cache "unknown" —
    a transient hiccup should be retried fresh next time.

    Returns:
        (status, detail) where status is one of:
            "valid"   — a real match was found, above the confidence bar.
            "invalid" — the API reached us fine and found nothing usable
                        (either no results, or only low-confidence ones).
            "unknown" — couldn't get a real answer after retrying.
        detail is None for "valid"/"invalid", or a short diagnostic
        string for "unknown".
    """
    CONFIDENCE_THRESHOLD = 0.5

    if "destination_check_cache" not in st.session_state:
        st.session_state.destination_check_cache = {}
    cached = st.session_state.destination_check_cache.get(destination_name)
    if cached is not None:
        return cached, None

    last_detail = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                "https://api.geoapify.com/v1/geocode/search",
                params={"text": destination_name, "limit": 1, "apiKey": GEOAPIFY_KEY},
                timeout=8,
            )
            response.raise_for_status()
            features = response.json().get("features", [])

            if not features:
                result = "invalid"
            else:
                confidence = features[0].get("properties", {}).get("rank", {}).get("confidence", 0)
                result = "valid" if confidence >= CONFIDENCE_THRESHOLD else "invalid"

            st.session_state.destination_check_cache[destination_name] = result
            return result, None
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "no response"
            response_snippet = (e.response.text[:200] if e.response is not None else "")
            last_detail = f"Attempt {attempt}/{max_retries}: HTTP {status_code} — {e}\nBody: {response_snippet}"
            if status_code == 429 and attempt < max_retries:
                time.sleep(attempt)
                continue
            return "unknown", last_detail
        except requests.exceptions.RequestException as e:
            last_detail = f"Attempt {attempt}/{max_retries}: {type(e).__name__} — {e}"
            if attempt < max_retries:
                time.sleep(attempt)
                continue
            return "unknown", last_detail

    return "unknown", last_detail


def pick_emoji_for_category(category_text: str) -> str:
    """
    Small COSMETIC helper: pick a fun emoji for a stop based on its
    category text, purely so the itinerary is easier to scan at a glance
    in the UI. Has zero effect on the actual planning logic.
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
    return "📍"


TOOL_NAME_TO_FRIENDLY_LABEL = {
    "geocode_destination": "📍 Looking up the destination's coordinates...",
    "search_points_of_interest": "🔎 Searching for places matching your interests...",
    "get_walking_time_matrix": "🚶 Calculating real walking times...",
}


st.title("🗺️ TripMate")
st.caption("Tell me where and how long — I'll build a real, walkable day-by-day plan around what you actually like.")

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

INTEREST_OPTIONS = [
    "Museums", "Viewpoints", "Local food", "Parks & nature",
    "Historic sites", "Art & galleries", "Nightlife", "Shopping", "Beaches",
]

with st.sidebar:
    st.header("✈️ Trip details")

    selected_destination_option = st.selectbox("📍 Destination", DESTINATION_OPTIONS)
    if selected_destination_option == "Other (type my own)":
        destination_name = st.text_input("Type your destination", "")
    else:
        destination_name = selected_destination_option

    number_of_days = st.slider("📅 Duration (days)", 1, 7, 3)
    pace = st.select_slider("🏃 Pace", options=["relaxed", "moderate", "packed"], value="moderate")

    st.markdown("❤️ **What are you into?**")
    checked_interests = []
    for interest_label in INTEREST_OPTIONS:
        default_checked = interest_label in ("Museums", "Local food")
        if st.checkbox(interest_label, value=default_checked, key=f"interest_{interest_label}"):
            checked_interests.append(interest_label.lower())
    interests_text = ", ".join(checked_interests)

    number_of_people = st.number_input("👥 Number of people", min_value=1, max_value=20, value=2)
    total_budget_usd = st.number_input("💰 Total budget in USD (0 = no limit)", min_value=0, value=0, step=50)
    st.divider()

    destination_is_english = is_english_only(destination_name)

    can_generate = bool(destination_name.strip()) and bool(checked_interests) and destination_is_english
    if not destination_name.strip() or not checked_interests:
        st.caption("⚠️ Pick a destination and at least one interest to continue.")
    elif not destination_is_english:
        st.caption("⚠️ Please type the destination using English letters only.")
    generate_button_clicked = st.button(
        "Generate itinerary", type="primary", use_container_width=True, disabled=not can_generate
    )


if generate_button_clicked:

    with st.spinner("Checking destination..."):
        validation_result, validation_detail = check_destination(destination_name)

    if validation_result == "invalid":
        st.error(
            f"⚠️ Couldn't confidently match **\"{destination_name}\"** to a real place. "
            "Please rewrite the destination and check the spelling, then try again."
        )
        st.stop()
    elif validation_result == "unknown":
        st.warning(
            f"⏳ Couldn't verify **\"{destination_name}\"** right now — the geocoding check failed. "
            "See the technical details below for the actual cause."
        )
        with st.expander("Technical details (for debugging)", expanded=True):
            st.code(validation_detail or "No detail captured.")
        st.stop()

    with st.status("🤖 Agent is planning your trip...", expanded=True) as status:

        def show_tool_call_progress(tool_name: str):
            friendly_label = TOOL_NAME_TO_FRIENDLY_LABEL.get(tool_name, f"Calling {tool_name}...")
            st.write(friendly_label)

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

    # DEFENSIVE CHECK: strict structured output guarantees the SHAPE of
    # itinerary["days"] (a list), but not that it's non-empty. If the
    # agent got confused mid-way (e.g. an ambiguous geocode result led it
    # to want to ask a clarifying question instead of committing to a
    # plan), it can still produce a schema-valid but EMPTY days list —
    # which crashes st.tabs([]) since Streamlit requires at least one tab
    # label. Catch that here with a clear message instead of a traceback.
    if not itinerary.get("days"):
        st.warning(
            "⚠️ The agent couldn't confidently build a full itinerary for this destination — "
            "it may be too ambiguous or obscure. Try being more specific "
            "(e.g. 'Riyadh, Saudi Arabia' instead of a short abbreviation)."
        )
        st.stop()

    st.header(f"{itinerary['destination_name']} · {itinerary['number_of_days']} days")

    stat_col1, stat_col2, stat_col3 = st.columns(3)
    stat_col1.metric("👥 Travelers", itinerary["number_of_people"])
    stat_col2.metric("💵 Estimated cost", f"${itinerary['total_estimated_cost_usd']:.0f}")
    stat_col3.metric("🏃 Pace", pace.capitalize())
    st.info(f"💡 {itinerary['budget_summary']}")

    # Show which interests (if any) had no real matches at this
    # destination — e.g. "beaches" at a landlocked city. The agent is
    # instructed to fill this with an honest explanation, or leave it
    # empty when every interest was matched.
    if itinerary.get("unmatched_interests_note"):
        st.warning(f"🔍 {itinerary['unmatched_interests_note']}")

    st.divider()

    day_tabs = st.tabs([f"Day {day['day_number']}" for day in itinerary["days"]])

    for day_tab, day in zip(day_tabs, itinerary["days"]):
        with day_tab:
            st.subheader(f"🗓️ {day['day_theme']}")

            stops_in_visit_order = sorted(day["stops"], key=lambda stop: stop["visit_order"])

            for stop in stops_in_visit_order:
                walking_minutes = stop["walking_minutes_from_previous_stop"]
                cost_per_person = stop["estimated_cost_per_person_usd"]
                cost_display_text = f"${cost_per_person:.0f}/person" if cost_per_person else "Free"
                emoji = pick_emoji_for_category(stop["category"])

                card_header = f"{emoji} {stop['visit_order']}. {stop['place_name']}"
                if walking_minutes:
                    card_header += f"  ·  🚶 {walking_minutes} min away"

                with st.expander(card_header, expanded=True):
                    st.markdown(stop["why_this_stop"])
                    badge_col1, badge_col2, badge_col3 = st.columns(3)
                    badge_col1.markdown(f"🏷️ **{stop['category']}**")
                    badge_col2.markdown(f"⏱️ **{stop['estimated_visit_minutes']} min**")
                    badge_col3.markdown(f"💵 **{cost_display_text}**")