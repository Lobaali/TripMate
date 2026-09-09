# TripMate

🔗 **Try it now (no setup needed):** https://tripmateagent.streamlit.app/

Want to look at, modify, or run the code yourself instead? Clone the repo and set it up locally — see **Setup** and **Running it** below.

TripMate turns "I'm going to Lisbon for 3 days" into a real, walkable, budget-aware day-by-day itinerary  built by an AI agent.

---

## How it works
TripMate hands the model **three tools** and lets it decide for itself when (and whether) to use each one. 

```
PHASE 1 — the tool-calling loop (runs until the model is done reasoning)
┌─────────────────────────────────────────────────────────────────┐
│  You describe the trip → model decides what to do next          │
│                                                                 │
│  Model asks to call:  geocode_destination                       │
│       → Python actually calls Nominatim, sends back coordinates │
│                                                                 │
│  Model asks to call:  search_points_of_interest                 │
│       → Python actually calls SerpApi, sends back real places   │
│                                                                 │
│  Model asks to call:  get_walking_time_matrix                   │
│       → Python actually calls OpenRouteService, sends back      │
│         real walking times                                      │
│                                                                 │
│  Model has no more tool calls → writes a complete, detailed     │
│  plain-text answer covering every stop, cost, and day           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
PHASE 2 — structured output (one separate, final call, no tools)
┌─────────────────────────────────────────────────────────────────┐
│  "Convert that finished text into this exact JSON shape,        │
│   don't change what it says"                                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                 Streamlit renders it as day-by-day tabs
```

---

## What each file does

| File | Role |
|---|---|
| **`app.py`** | The Streamlit UI. Collects your inputs in the sidebar, calls `run_trip_planning_agent()`, and renders whatever itinerary comes back. Has no planning logic of its own and never calls the tools directly. |
| **`agent.py`** | The actual agent: the tool-calling loop (Phase 1) plus the structured-output conversion call (Phase 2). |
| **`tools.py`** | The three real functions the agent can call: `geocode_destination()` (Nominatim, free), `search_points_of_interest()` (SerpApi/Google Maps), `get_walking_time_matrix()` (OpenRouteService).|
| **`schema.py`** | Two things: `TRIP_PLANNING_TOOLS` (the flat tool definitions the model sees during Phase 1) and `DAY_BY_DAY_ITINERARY_SCHEMA` (the raw JSON schema the final answer must match, used only in Phase 2). |
| **`requirements.txt`** | The Python packages the project needs (`streamlit`, `requests`, `openai`, `python-dotenv`). |
| **`.env.example`** | A template showing which environment variables (API keys) you need. Copy it to a real `.env` and fill in your own keys|

---

## Setup (do this once)

### 1. Install Python
You need Python 3.9+. Check with:
```bash
python3 --version
```
If missing, get it from [python.org/downloads](https://www.python.org/downloads/) (Mac: `brew install python` also works).

### 2. Get the project files
If you're cloning from GitHub:
```bash
git clone https://github.com/Lobaali/TripMate.git
cd TripMate
```
Otherwise, just put all 7 files in one folder.

### 3. Create a virtual environment
This keeps TripMate's Python packages separate from the rest of your system.
```bash
python3 -m venv venv
```

### 4. Activate it
```bash
# Mac/Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```
You'll know it worked because your terminal prompt now starts with `(venv)`. **Do this every time you open a new terminal to work on the project.**

### 5. Install the packages
With `(venv)` showing in your prompt:
```bash
pip install -r requirements.txt
```

### 6. Get your three free API keys

| Key | Where to get it | Free tier |
|---|---|---|
| `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | Pay-per-token, but pennies per itinerary |
| `GEOAPIFY_KEY` | [myprojects.geoapify.com](https://myprojects.geoapify.com) | 3,000 requests/day |
| `SERPAPI_KEY` | [serpapi.com/manage-api-key](https://serpapi.com/manage-api-key) | 100 searches/month |
| `ORS_KEY` | [openrouteservice.org/dev/#/signup](https://openrouteservice.org/dev/#/signup) | 2,000 requests/day |

### 7. Set up your `.env` file
```bash
cp .env.example .env
```
Open `.env` and paste your three real keys in, one per line, no quotes:
```
OPENAI_API_KEY=sk-...
SERPAPI_KEY=...
ORS_KEY=...
```

### 8. Check your model name
Open `agent.py` and check the `MODEL_NAME` variable near the top. It needs to be a model your OpenAI account actually has access to that supports **both** function calling and structured text output via the Responses API (e.g. `"gpt-4.1"`, `"gpt-4o"`, or `"gpt-5"` if your account has it).

---

## Running it

Every time you want to run TripMate:

```bash
cd TripMate                        # go to the project folder
source venv/bin/activate           # Mac/Linux (or venv\Scripts\activate on Windows)
python3 -m streamlit run app.py    # start the app
```

It should open automatically in your browser at `http://localhost:8501`. If it doesn't, copy that URL from the terminal into your browser manually.

---