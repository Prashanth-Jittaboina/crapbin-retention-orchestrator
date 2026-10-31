# Product Specification & Architecture Document
## Crapbin Customer Retention & Voice Agent Orchestrator
**Document Version:** 1.0.0  
**Target Audience:** Enterprise Clients, Operations Leadership, Technical Evaluators, Buildathon Committee  
**System Status:** Production-Ready / Demo-Verified  

---

## 1. Executive Summary & Business Objective

### The Core Problem
**Crapbin** operates a doorstep scrap collection and recycling service across Hyderabad. In traditional operations, doorstep collection businesses experience a steep customer churn curve: up to **65% to 75% of households churn after 1 or 2 pickups**. While these households continuously generate recyclable household scrap (newspapers, cardboard, electronic waste, iron appliances, plastics), manual tele-calling is:
1. **Cost-prohibitive:** High staff overhead per outbound minute.
2. **Linguistically fragmented:** Hyderabad customers speak Telugu, Hindi, English, and Dakhni/mixed dialects.
3. **Inconsistent in data capture:** Tele-callers fail to consistently capture exact scrap categories, weight estimates, and pickup time slots.
4. **Poor in customer experience:** Aggressive manual callbacks trigger customer annoyance and compliance/DNC violations.

### The Solution: Autonomous Multilingual Retention & Orchestrator Agent
The Crapbin Autonomous Retention System replaces manual calling with an end-to-end, closed-loop voice automation platform:
- **Multilingual Voice AI (Bolna AI):** Initiates natural, low-latency conversational calls in Telugu, Hindi, and English to inactive customers.
- **FastAPI Webhook Gateway:** Ingests live call telemetry, recordings, and raw transcripts with token-based cryptographic authentication.
- **Structured Intelligence Extraction (Gemini 2.5 Flash / GPT-4.1 mini):** Extracts granular customer intent, scrap materials, quantities, locality, and preferred pickup windows.
- **Deterministic Orchestrator Agent:** Applies strict business guardrails over LLM inferences, categorizing every lead into 4 clear tiers (**Hot Lead**, **Warm Lead**, **Cold Lead**, **Do Not Call**).
- **Automated Dispatch & Operations Dashboard:** Automatically registers audit logs in SQLite, queues coordinator tickets for digital weighment pickup, drafts WhatsApp/email notifications, and displays live metrics on an interactive operations dashboard.

---

## 2. End-to-End 6-Stage Reference Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   END-TO-END SYSTEM PIPELINE                                     │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘

   [ STAGE 1: Inactive Customer Data Lake / CRM ]
          │  Phone, Name, Email, Address/Locality, Last Pickup Date, Historical Volumes
          ▼
   [ STAGE 2: Bolna AI Multilingual Voice Agent ]
          │  Outbound Telephony Dialing (Exotel / Twilio / Plivo)
          │  Real-Time Conversational AI (Telugu / Hindi / English Speech Synthesis & STT)
          │  Checks scrap availability, quantifies items, gathers locality & time slot
          ▼
   [ STAGE 3: FastAPI Webhook Receiver & LLM Extraction Engine ]
          │  Token Authentication (?token=... or X-Webhook-Token / Bearer)
          │  Payload Normalization (Telephony metadata + Conversation Transcript)
          │  LLM Extraction (Gemini 2.5 Flash / OpenAI GPT-4.1 mini)
          │  Extracts: Materials, Quantity, Locality, Availability, Outcome, Confidence
          ▼
   [ STAGE 4: crapbin.db Shared Database & Audit Infrastructure ]
          │  `calls`: Full call metadata, transcript, JSON insights, classification
          │  `customers`: Master record with locality, pickup history, and DNC flag
          │  `orchestrator_actions`: Immutable audit trail of every automated decision
          │  `messages`: Outbound coordinator tickets & customer messaging queue
          ▼
   [ STAGE 5: Orchestrator Agent & Deterministic Guardrails ]
          │  Evaluates LLM Confidence (>= 0.7) and detail completeness
          │  Applies Regex & Semantic DNC Opt-Out Guardrails ("don't call", "wrong number")
          │  Routes non-answered / dropped calls into intelligent callback loops
          ▼
   [ STAGE 6: 4-Tier Lead Classification & Next-Best Action Dispatch ]
          ├── 1. Hot Lead    ──► Next-Best Action: "Create pickup request for coordinator"
          │                      (Queues coordinator dispatch ticket + WhatsApp confirmation)
          ├── 2. Warm Lead   ──► Next-Best Action: "Schedule follow-up call"
          │                      (Queues callback attempt; preserves re-engagement cooldown)
          ├── 3. Cold Lead   ──► Next-Best Action: "Add to future nurture list"
          │                      (Marked completed; scheduled for 60-90 day re-engagement)
          └── 4. Do Not Call ──► Next-Best Action: "Mark as Do Not Call"
                                 (Marks customer `do_not_call = 1`; blocks all outbound messaging)
```

---

## 3. Detailed Component Deep-Dive

### Stage 1: Inactive Customer Repository (`customers` table & `import_csv.py`)
- **Data Attributes:**
  - `phone`: Primary key normalized to E.164 standard (`91XXXXXXXXXX`).
  - `name`: Customer full name.
  - `email`: Customer email address.
  - `address_locality`: Area/suburb in Hyderabad (e.g. Kukatpally, Madhapur, Secunderabad).
  - `last_pickup_date`: Date of previous scrap pickup.
  - `pickup_history`: JSON/text record of previous scrap categories and weights.
  - `do_not_call`: Binary flag (0 = active, 1 = permanently opted out).
- **Ingestion:** Supports batch onboarding through `import_csv.py` with column matching and idempotent upserting.

---

### Stage 2: Bolna AI Multilingual Voice Agent (`normalize.py`)
- **Agent Role:** Conversational AI Voice Assistant representing the Crapbin Customer Support Team.
- **Language Coverage:**
  - **Telugu:** `"Namaste! Crapbin nunchi Priya matladutunnanu. Mi daggara scrap unda?"`
  - **Hindi:** `"Namaste, Crapbin se Priya bol rahi hoon. Kya aapke paas bechne ke liye kabad ya raddi hai?"`
  - **English:** `"Hi, this is Priya calling from Crapbin customer support. Would you like to schedule a scrap pickup?"`
  - **Mixed / Code-Switching:** Seamlessly understands mixed dialects common in urban Hyderabad.
- **Information Elicited:**
  1. Interest in doorstep scrap pickup.
  2. Scrap materials available (newspapers, carton boxes, plastic, iron/steel, appliances, e-waste).
  3. Approximate weight or volume (e.g. 15 kg, 2 cartons, 1 almirah).
  4. Current locality/colony.
  5. Preferred pickup day and time window (e.g. tomorrow morning, Saturday evening).
- **Status Normalization:** Maps diverse telephony carrier signals (`completed`, `call-disconnected`, `no-answer`, `busy`, `failed`) into canonical system events.

---

### Stage 3: FastAPI Webhook Receiver & LLM Extraction Engine (`webhook_receiver.py` & `llm.py`)
- **Security & Ingestion:**
  - Endpoint: `POST /webhook/bolna`
  - Authentication: Validates `?token=...`, `X-Webhook-Token`, or `Authorization: Bearer <token>`.
  - Archival: Persists pristine JSON payloads to `calls_json/<call_id>.json` before asynchronous background execution.
- **Dual LLM Architecture:**
  - **Default Engine:** Google Gemini 2.5 Flash (`gemini-2.5-flash`) via `google-genai` SDK with native Pydantic schema enforcement.
  - **Alternative Engine:** OpenAI GPT-4.1 mini (`gpt-4.1-mini`) via OpenAI API structured JSON outputs.
  - Switchable via `.env`: `LLM_PROVIDER=gemini|openai`.
- **Structured Pydantic Extraction Schema (`CallInsights`):**
  ```python
  class CallInsights(BaseModel):
      outcome: Literal[
          "interested",
          "callback_later",
          "not_interested_now",
          "do_not_call",
          "wrong_number",
          "no_conversation",
      ]
      orchestrator_classification: Literal[
          "Hot Lead",
          "Warm Lead",
          "Cold Lead",
          "Do Not Call",
      ]
      next_best_action: Literal[
          "Create pickup request for coordinator",
          "Schedule follow-up call",
          "Add to future nurture list",
          "Mark as Do Not Call",
      ]
      confidence: float
      reason: str
      customer_name: Optional[str]
      scrap_materials: List[str]
      approx_quantity: Optional[str]
      locality: Optional[str]
      availability: Optional[str]
      customer_mentioned_date: Optional[str]
      language: Literal["english", "hindi", "telugu", "mixed"]
      summary: str
      follow_up_email: Optional[str]
  ```

---

### Stage 4: Shared Database & Audit Infrastructure (`db.py` / `crapbin.db`)
- **Engine:** SQLite with WAL mode (`PRAGMA journal_mode=WAL`) enabled for concurrent web reading and background worker writing.
- **Idempotent Migration Engine:** Automatically detects table schemas on startup via `PRAGMA table_info` and executes non-destructive `ALTER TABLE` operations to guarantee 100% backward compatibility with existing databases.
- **Core Tables:**
  1. `calls`: Master record of every call, telephony duration, recording URL, raw transcript, AI summary, LLM insights JSON, orchestrator classification, reasoning, and execution timestamps.
  2. `customers`: Customer CRM profile, locality, pickup history, and DNC opt-out state.
  3. `orchestrator_actions`: Immutable audit trail. Records every decision made by the system (including web-test calls where phone number is null), ensuring regulatory compliance and operational visibility.
  4. `messages`: Queue for coordinator emails and customer WhatsApp notifications with channel deduplication (`UNIQUE(call_id, channel)`), send-window constraints, and delivery status tracking.

---

### Stage 5 & 6: Orchestrator Agent & 4-Tier Action Engine (`orchestrator.py` & `followup.py`)
The Orchestrator Agent bridges unstructured AI conversation and deterministic operational dispatch by applying hard rules over model outputs:

| Tier | Customer Intent & Signals | Deterministic Guardrails | Exact Next-Best Action | Automated Operational Impact |
| :--- | :--- | :--- | :--- | :--- |
| **🔥 Hot Lead** | Customer wants pickup, gave scrap details (materials/quantity/locality), available in near term. | `outcome == "interested"` AND `confidence >= 0.7` AND (materials OR quantity OR locality present). | **Create pickup request for coordinator** | Queues coordinator dispatch ticket with scrap summary, digital weighment instructions, and generates customer WhatsApp confirmation. |
| **📞 Warm Lead** | Interested but needs to verify with spouse/family, requested callback, or call was unanswered/busy. | `outcome == "callback_later"` OR low confidence interested (`< 0.7`) OR `status == "no-answer"` OR customer didn't speak. | **Schedule follow-up call** | Enters customer into automated callback loop with 7-day re-engagement cooldown protection. |
| **❄️ Cold Lead** | Polite decline: cleared scrap recently, no scrap at home right now, but did not opt out. | `outcome in ("not_interested_now", "not_interested")` without opt-out keywords. | **Add to future nurture list** | Flags call as completed; schedules customer profile for re-engagement in 60-90 days. |
| **🛑 Do Not Call** | Explicit opt-out, firm refusal, hostile decline, or wrong number. | Transcript contains opt-out phrases (`"don't call"`, `"stop calling"`, `"wrong number"`) OR `outcome in ("do_not_call", "wrong_number")`. | **Mark as Do Not Call** | Immediately sets `customers.do_not_call = 1`; permanently purges pending messages and blocks future dialing. |

---

## 4. Operational Dashboard & Management Interface (`dashboard.html`)

The system includes a single-page web operations console served directly from the FastAPI root (`http://localhost:8000/`):

1. **Light & Dark Theme Switcher:** Instant, zero-latency toggle with localStorage persistence. Light mode provides high-contrast typography designed for operational day-shifts and clean executive demo recordings.
2. **Real-Time Telephony Metrics Bar:**
   - **🗣️ Answered & Spoke:** Count and percentage of calls where the customer engaged in dialog.
   - **📴 Not Answered / Dropped:** Count of calls where the line rang out, was busy, or dropped before speech.
   - **📊 Total Outbound Calls & Connect Rate %:** Live operational conversion indicator.
3. **4-Tier Metric Cards:** Real-time counters showing distribution across Hot Leads, Warm Leads, Cold Leads, and Do Not Call.
4. **Live Buildathon Webhook Simulator:** One-click simulation buttons (`🔥 Hot`, `📞 Warm`, `❄️ Cold`, `🛑 DNC`, `📴 No Answer`) enabling real-time demonstration of all 4 pipeline branches without running external scripts.
5. **Multi-Tab Workspace:**
   - **Tab 1: Call Transcripts & Classifications:** Searchable list of calls with status badges, extracted scrap details, and a modal showing bilingual transcripts and raw JSON insights.
   - **Tab 2: Audit Action Queue:** Real-time view of `orchestrator_actions` entries ensuring every decision is auditable.
   - **Tab 3: Outbound Message Queue:** Inspection table for queued, sent, and dry-run WhatsApp and coordinator dispatch messages.
6. **Live Auto-Sync:** Automated background polling every 3 seconds to reflect inbound Bolna webhooks instantaneously.

---

## 5. Security, Compliance, & Guardrails

- **Webhook Cryptographic Authentication:** Every inbound Bolna post is verified against `WEBHOOK_TOKEN` passed via query parameters or secure headers. Unauthorized requests return HTTP 401.
- **Strict Privacy & Do Not Call Compliance:** Unlike naive LLM pipelines that may repeatedly call frustrated users, the Orchestrator enforces a hard regex and keyword filter. Any customer requesting removal is instantly marked `do_not_call = 1`, and all pending messages are discarded.
- **Time-Windowed Messaging (Anti-Spam):** Outbound customer messages are restricted to compliant daylight hours (`SEND_WINDOW_START` to `SEND_WINDOW_END`, default 9 AM to 8 PM IST). Messages queued outside this window are automatically deferred until the next business morning.
- **Coordinator Guardrail for Unclear Leads:** If a customer says "yes" but provides no scrap materials or locality, the lead is automatically downgraded from Hot Lead to Warm Lead (`needs_review`) to prevent coordinators from wasting travel time on unverified addresses.

---

## 6. System Deployment & API Specification

### Environment Variables (`.env`)
```env
# LLM Engine Configuration
LLM_PROVIDER=gemini                   # "gemini" or "openai"
GEMINI_API_KEY=AIzaSy...              # Google GenAI API Key
GEMINI_MODEL=gemini-2.5-flash
OPENAI_API_KEY=sk-...                 # Optional OpenAI Key
OPENAI_MODEL=gpt-4.1-mini

# Webhook Security & Server
WEBHOOK_TOKEN=WbaAEaaIbW2k6layxLjtaXPQ
PORT=8000
FOLLOWUP_INTERVAL_MIN=10

# Operational Guardrails
DRY_RUN=true                          # Prevents live SMS/Email dispatch during demos
SEND_WINDOW_START=9                   # 9 AM IST
SEND_WINDOW_END=20                    # 8 PM IST
REENGAGE_COOLDOWN_DAYS=7
COORDINATOR_EMAIL=coordinator@crapbin.local
```

### Core API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Web Operations Dashboard (Light/Dark mode) |
| `GET` | `/health` | System health check, active LLM provider, and dry-run status |
| `GET` | `/summary` | Aggregated metrics for telephony, 4 tiers, actions, and messages |
| `GET` | `/calls` | Complete call list with transcripts, classifications, and parsed JSON |
| `GET` | `/actions` | Immutable audit log records from `orchestrator_actions` |
| `GET` | `/messages` | Outbound messaging queue (WhatsApp & coordinator emails) |
| `POST` | `/webhook/bolna` | Inbound Bolna AI voice webhook receiver |
| `POST` | `/api/simulate/{tier}` | Triggers simulated webhook for `hot`, `warm`, `cold`, `dnc`, or `no-answer` |
| `POST` | `/api/reset-demo` | Resets database back to the clean 11 baseline calls |

---

## 7. Verification & Quality Assurance Suite

The system includes an automated test harness (`test_verification.py`) validating all 4 dimensions of the target architecture:
1. **Schema & Migration Integrity:** Validates that all new columns and tables exist without impacting existing historical rows.
2. **Backward Compatibility:** Verifies that all 11 baseline historical calls (including the 4 web-test calls where `phone` is `None`) are classified with full audit actions.
3. **REST API & Token Auth:** Confirms that HTTP 401 is returned on missing tokens, and HTTP 200 is returned on valid query tokens, header tokens, and bearer tokens.
4. **4-Tier Orchestrator Logic:** Simulates all 7 edge scenarios (Hot lead with scrap, Warm lead callback, Ambiguous interested lead, Unanswered call, Cold lead polite decline, Explicit opt-out, Wrong number) and confirms 100% deterministic guardrail execution.

Run the verification suite anytime via:
```powershell
python test_verification.py
```
**Result:** `ALL 4 VERIFICATION SUITE CHECKS PASSED (100% ALIGNMENT)`

