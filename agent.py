"""
agent.py — The real tool-calling agent loop, using OpenAI's Responses API.
  PHASE 1 — THE TOOL-CALLING LOOP (may run several rounds):
    1. Start a conversation: [system message, user message]
    2. Ask the model to respond, giving it the list of tools it's allowed
       to call (client.responses.create(..., tools=TOOLS))
    3. Add the model's response to the conversation history
    4. Check: did the model ask to call any tools this round?
         - YES -> actually run those Python functions for real, add their
                  results to the conversation as new messages, and go
                  back to step 2 (the model gets another turn, now with
                  real data available)
         - NO  -> the model has decided it's done reasoning and has
                  written its final plain-text answer — stop looping

  PHASE 2 — STRUCTURED OUTPUT (a separate, single call):
    5. Send the finished plain-text answer to the model ONE more time,
       this time with NO tools available, and ask it to reformat that
       text into strict JSON matching DAY_BY_DAY_ITINERARY_SCHEMA.
"""

import json
from openai import OpenAI

from schema import DAY_BY_DAY_ITINERARY_SCHEMA, TRIP_PLANNING_TOOLS
from tools import geocode_destination, search_points_of_interest, get_walking_time_matrix

openai_client = OpenAI()

MODEL_NAME = "gpt-4.1"  # match whatever model string your OpenAI account has access to

MAX_TOOL_CALL_ROUNDS = 8


SYSTEM_PROMPT = """\
ROLE
You are TripMate, an expert local trip-planning assistant. You think like \
a well-traveled local friend who knows how to build a realistic, walkable \
itinerary — not a generic list of tourist highlights.

TASK
Build a complete day-by-day itinerary for the traveler's trip. You have \
three tools available and must use them in this order:

  1. geocode_destination — call this first with the destination name, to
     get its coordinates.
  2. search_points_of_interest — call this next with the coordinates from
     step 1 and the traveler's interests, to get a pool of real candidate
     places. Use max_results=30. The result includes results_per_interest,
     showing how many places were found for EACH interest separately —
     read this carefully (see UNMATCHED INTERESTS below).
  3. get_walking_time_matrix — after reviewing the candidate pool, decide
     which places you're actually considering for the itinerary (enough
     to fill the requested trip length at the requested pace), and call
     this tool with ONLY that shortlist — not the entire pool — to get
     real walking times between them.

Only after you have real walking-time data should you write your final
answer.

FINAL ANSWER FORMAT (plain text — it will be converted to JSON afterward,
by a separate step that cannot see your tool results anymore, only this
text — so nothing important can be left out of it)
Your final answer must be a complete, detailed write-up that includes, for
every single stop in every day:
  - the place's exact name, category, latitude, and longitude
  - its visit order within the day (1, 2, 3...)
  - how many minutes to spend there
  - its estimated cost per person in USD (0 if free)
  - its real walking time in minutes from the previous stop (or "first
    stop of the day" for the first one)
  - one short sentence on why it's included at that point in the day
Also include: the destination name, number of days, number of people,
total estimated cost in USD for the whole trip, a short budget summary
sentence, and a note about any unmatched interests (see below — say
explicitly "none" if every interest was matched).

UNMATCHED INTERESTS
search_points_of_interest's results_per_interest tells you exactly how
many real places were found for each interest term. If any interest
found ZERO results, that is not an error and not something to hide —
it genuinely means that interest isn't available at this destination
(e.g. "beaches" at a landlocked city). In that case:
  - Do NOT invent fake places to fill the gap.
  - Do NOT silently drop the interest without mentioning it.
  - DO state plainly, by name, which interest(s) had no results and why
    if it's obvious (e.g. the destination is landlocked), in your final
    answer's budget/notes section — this becomes unmatched_interests_note.
  - DO build the itinerary around the traveler's OTHER interests plus
    general highlights from the pool, so the trip is still complete and
    useful despite the one gap.
If every interest found at least one real result, explicitly say so
("unmatched interests: none") rather than leaving this unaddressed.

ONE-SHOT CONSTRAINT — NO CLARIFYING QUESTIONS
This is a one-shot planning request. There is no opportunity to ask the
traveler a follow-up question and get a reply — if you write a question
instead of an itinerary, no one will ever see it or answer it, and the
app will fail. If a destination or its geocoded location seems ambiguous
or uncertain, proceed with your single best reasonable interpretation
and note the assumption in your final answer. NEVER leave the day-by-day
plan empty or incomplete in order to ask something first.

CONSTRAINTS
- Only include places that came from search_points_of_interest. Never
  invent a place that wasn't in the tool's results.
- Respect the requested pace: "relaxed" = 2-3 stops/day, "moderate" = 3-4
  stops/day, "packed" = 5+ stops/day.
- Group each day's stops so they are geographically close together, based
  on the real walking-time matrix — never guess distance from place names.
- Prefer variety across the trip — avoid scheduling several near-identical
  places back to back unless the traveler's interests clearly call for it.
- If a total budget is given, prioritize free or low-cost stops and limit
  paid sit-down meals as needed to stay close to it, and explain any
  trade-off in your budget summary. If no budget is given, still estimate
  costs honestly and say so.
"""

def run_tool(tool_name: str, arguments: dict):
    """
    WHAT THIS DOES: takes the name and arguments the MODEL chose (parsed
    from its tool-call request) and routes them to the matching real
    Python function in tools.py. This is the one place in the whole
    system where a "tool call" (which is really just a JSON object the
    model generated) turns into an actual HTTP request to a real API.

    WHY WRAP THE RETURN VALUES: tools.py's functions return whatever shape
    is most natural in Python (e.g. geocode_destination returns a plain
    tuple). But everything sent back to the model has to be a
    JSON-serializable object, so each branch below wraps the raw return
    value into a small dict with clear key names before handing it back.

    Args:
        tool_name: the function name the model asked to call, e.g.
                   "geocode_destination" — must match one of the "name"
                   values in schema.py's TRIP_PLANNING_TOOLS.
        arguments: a dict of the arguments the model supplied, already
                   parsed from the tool call's JSON string by the caller.

    Returns:
        A JSON-serializable dict with the tool's result, ready to be sent
        back to the model as a function_call_output message.
    """
    if tool_name == "geocode_destination":
        latitude, longitude = geocode_destination(**arguments)
        return {"latitude": latitude, "longitude": longitude}

    if tool_name == "search_points_of_interest":
        found_places = search_points_of_interest(**arguments)
        return {"places": found_places}

    if tool_name == "get_walking_time_matrix":
        matrix_seconds = get_walking_time_matrix(**arguments)
        return {"walking_time_matrix_seconds": matrix_seconds}

    return {"error": "unknown_tool", "tool_name": tool_name}


def run_trip_planning_agent(
    destination_name: str,
    number_of_days: int,
    interests_text: str,
    pace: str,
    total_budget_usd: float,
    number_of_people: int,
    on_tool_call=None,
) -> dict:
    """
    Run the full two-phase agent: the tool-calling loop, then the
    structured-output conversion call. This is the one function app.py
    calls to get a finished itinerary.

    Args:
        destination_name: e.g. "Lisbon, Portugal"
        number_of_days: trip length in days
        interests_text: free text like "museums, viewpoints, local food"
        pace: "relaxed" / "moderate" / "packed"
        total_budget_usd: total USD budget for the whole trip (all people
                           combined), or None/0 if the traveler didn't set one
        number_of_people: number of travelers, used to scale cost framing
        on_tool_call: optional callback function, called as
                      on_tool_call(tool_name) immediately before each tool
                      actually executes. app.py uses this to print live
                      progress into the Streamlit status box — this
                      function works perfectly fine without it too (just
                      pass None or omit it), it's purely for UI feedback.

    Returns:
        A dict matching DAY_BY_DAY_ITINERARY_SCHEMA exactly — safe to
        index directly (itinerary["days"], stop["place_name"], etc.)
        without defensive .get() calls, since strict-mode structured
        output guarantees every field is present.
    """
    # Build the one line describing budget/group size that gets included
    # in the user's opening message — phrased differently depending on
    # whether a budget was actually given, so the model isn't told
    # "budget: None" literally.
    if total_budget_usd:
        budget_line = (
            f"Total budget: ${total_budget_usd} USD for {number_of_people} people, "
            f"for the whole trip (excluding flights/hotels)."
        )
    else:
        budget_line = f"No specific budget given. Number of people: {number_of_people}."

    # This is the single user message that kicks off the whole
    # conversation — everything the model needs to know about the request
    # up front, before it starts deciding which tools to call.
    trip_request_message = (
        f"Destination: {destination_name}\n"
        f"Trip length: {number_of_days} days\n"
        f"Pace: {pace}\n"
        f"Traveler interests: {interests_text or 'general sightseeing, no strong preference'}\n"
        f"{budget_line}"
    )

    # input_items IS the conversation. It starts with just the system
    # prompt and the user's request, and grows every round: the model's
    # own turn gets appended (which may contain tool-call requests), and
    # then each tool's real result gets appended as its own item. By the
    # time the loop finishes, this list is a complete transcript of
    # everything that happened during planning.
    input_items = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": trip_request_message},
    ]

    # Purely for our own bookkeeping/debugging — not sent to the model.
    # Not currently used elsewhere, but handy if you want to log or
    # display which tools actually got called during a run.
    used_tools = []

    final_text = None  # will hold the model's finished plain-text answer once Phase 1 ends

    # -----------------------------------------------------------------
    # PHASE 1: THE TOOL-CALLING LOOP
    # -----------------------------------------------------------------
    for _ in range(MAX_TOOL_CALL_ROUNDS):

        # Ask the model for its next turn. Note: we pass the ENTIRE
        # conversation so far (input_items) every single time — the
        # Responses API is stateless between calls, so the full history
        # has to be resent each round or the model would have no memory
        # of what happened in earlier rounds.
        response = openai_client.responses.create(
            model=MODEL_NAME,
            input=input_items,
            tools=TRIP_PLANNING_TOOLS,
        )

        # response.output is a list of items representing everything the
        # model did this turn — this can include tool-call requests,
        # reasoning, and/or a text message. We append the WHOLE thing to
        # our running conversation, exactly as the API returned it, so
        # the model's own turn is preserved in history for next round.
        input_items += response.output

        # Scan this turn's output for any items where the model is asking
        # to call a tool. A single turn can contain MULTIPLE tool calls
        # (e.g. the model might decide to call two tools back to back),
        # which is why this is a list comprehension, not a single check.
        tool_calls = [item for item in response.output if item.type == "function_call"]

        # If there are no tool-call requests in this turn, the model has
        # decided it's done gathering information and has written its
        # final plain-text answer instead. response.output_text is a
        # convenience property that extracts just the text content from
        # the turn — this is Phase 1's finish line.
        if not tool_calls:
            final_text = response.output_text
            break

        # Otherwise, actually execute every tool the model asked for, one
        # at a time, and feed each real result back into the conversation
        # before looping back to give the model another turn.
        for call in tool_calls:
            tool_name = call.name

            # The model provides arguments as a JSON-encoded string (not
            # a Python dict directly) — we have to parse it ourselves.
            arguments = json.loads(call.arguments)

            # Let app.py know a tool is about to run, so it can update the
            # Streamlit UI with a friendly progress message. This has zero
            # effect on the agent's actual behavior — it's purely a hook
            # for the caller to observe what's happening.
            if on_tool_call:
                on_tool_call(tool_name)

            # THIS is the line where a "tool call" stops being just a JSON
            # object the model generated and becomes a REAL network
            # request to Nominatim, SerpApi, or OpenRouteService.
            result = run_tool(tool_name, arguments)
            used_tools.append(tool_name)

            # Send the real result back to the model as a
            # "function_call_output" item. The call_id here MUST match
            # the id from the model's original tool-call request (call.call_id)
            # — that's how the model knows which of its (possibly several)
            # simultaneous tool calls this particular result answers.
            input_items.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result, ensure_ascii=False),
            })
        # Falls through to the top of the for-loop here, giving the model
        # another turn — now with real tool results available to reason over.

    # If we exhausted MAX_TOOL_CALL_ROUNDS without ever seeing a
    # no-tool-call turn, something went wrong (likely a confused model
    # repeatedly re-requesting the same tool). Fail loudly rather than
    # silently returning an incomplete or garbage result.
    if final_text is None:
        raise RuntimeError(
            f"Agent didn't finish within {MAX_TOOL_CALL_ROUNDS} tool-call rounds — "
            "something may be looping. Check the conversation history (input_items) for clues."
        )

    # -----------------------------------------------------------------
    # PHASE 2: STRUCTURED OUTPUT (a separate, single, tool-free call)
    #
    # This call has NO access to TRIP_PLANNING_TOOLS and cannot look
    # anything up again — its only input is final_text, the plain-text
    # answer Phase 1 already finished writing. Its entire job is to
    # reformat that text into DAY_BY_DAY_ITINERARY_SCHEMA's exact shape
    # without changing what it says. This mirrors your notebook's final
    # "Convert the provided agent answer into the required JSON structure"
    # step exactly.
    # -----------------------------------------------------------------
    final_response = openai_client.responses.create(
        model=MODEL_NAME,
        input=[
            {
                "role": "system",
                "content": "Convert the provided agent answer into the required JSON structure. Do not change the meaning of the answer."
            },
            {
                "role": "user",
                "content": f"Agent answer:\n{final_text}"
            }
        ],
        # text={"format": {...}} is how the Responses API's structured
        # output works: "type": "json_schema" tells it to enforce a
        # schema, "schema" is the actual JSON Schema object (imported from
        # schema.py), and "strict": True enforces the additionalProperties/
        # required rules at the API level rather than just hoping the
        # model follows them.
        text={
            "format": {
                "type": "json_schema",
                "name": "DayByDayItinerary",  # an internal label for this schema, not shown to the user
                "schema": DAY_BY_DAY_ITINERARY_SCHEMA,
                "strict": True
            }
        }
    )

    # Because of strict structured output, final_response.output_text is
    # GUARANTEED to be a JSON string matching DAY_BY_DAY_ITINERARY_SCHEMA
    # exactly — json.loads() turns it into a plain Python dict that app.py
    # can index directly (itinerary["days"], stop["place_name"], etc.)
    # without needing any defensive .get() calls or validation.
    return json.loads(final_response.output_text)