# Crapbin Customer Retention & Voice Agent Orchestrator
> **Autonomous Inactive Customer Re-engagement, Multilingual Voice AI, & Lead Orchestration System for Scrap Pickup in Hyderabad**  
> *100% Architecture Alignment for Buildathon Demo*

---

## 📖 Overview

**Crapbin** is a doorstep scrap pickup service operating in Hyderabad. This project implements an autonomous AI retention engine that proactively re-engages inactive customers through multilingual voice agents (Telugu, Hindi, English), extracts structured scrap and availability details from conversation transcripts, and deterministically orchestrates each customer into one of 4 actionable tiers with audited Next-Best Actions.

---

## 🏗️ 6-Stage Reference Architecture

```
[Stage 1: Inactive Customers]
       │  (phone, name, email, address_locality, last_pickup_date)
       ▼
[Stage 2: Bolna AI Multilingual Voice Agent]
       │  (Telugu, Hindi, English outbound calls)
       ▼
[Stage 3: FastAPI Webhook & LLM Extractor]
       │  (Token verification, Gemini 2.5 Flash / GPT-4.1 mini structured JSON)
       ▼
[Stage 4: crapbin.db & Audit Queue]
       │  (calls, customers, orchestrator_actions, messages)
       ▼
[Stage 5: Orchestrator Agent & Guardrails]
       │  (Deterministic guardrails on confidence, details, & opt-outs)
       ▼
[Stage 6: 4-Tier Lead Classification & Next-Best Action Dispatch]
       ├── Hot Lead     -> "Create pickup request for coordinator" (Queues coordinator ticket)
       ├── Warm Lead    -> "Schedule follow-up call" (Queues callback attempt)
       ├── Cold Lead    -> "Add to future nurture list" (Sets status completed)
       └── Do Not Call  -> "Mark as Do Not Call" (Opt-out guardrail, marks customer DNC)
```

---

## ⚡ Quickstart (1-Command Setup)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)
Ensure `.env` exists with your API keys:
```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash
WEBHOOK_TOKEN=your_shared_webhook_token
PORT=8000
DRY_RUN=true
```

### 3. Launch the Server
```bash
python webhook_receiver.py
```
* **Interactive Live Dashboard**: Open [http://localhost:8000/](http://localhost:8000/) in your browser.
* **Interactive Swagger Docs**: Open [http://localhost:8000/docs](http://localhost:8000/docs).

---

## 🎬 Buildathon Video Recording Script (Zero Code Runs During Video)

Follow this 2-minute recording guide to record your demo smoothly without switching windows or running code in between.

### Before Starting Recording (Setup Once)
1. In your terminal, run:
   ```bash
   python webhook_receiver.py
   ```
2. Open your browser to `http://localhost:8000/`.
3. If you previously tested simulated calls, click **`↺ Reset to 11 Baseline Calls`** at the top right to start with the clean dataset.
4. Open your screen recorder (OBS, Loom, or Windows Game Bar `Win + G`).

---

### Step-by-Step Recording Script (2 Minutes)

#### 0:00 - 0:30 | The Problem & Architecture Overview
* **Visual**: Show the Crapbin Dashboard at `http://localhost:8000/`.
* **Talking Points**:
  > *"Doorstep scrap pickup in Hyderabad faces a major retention bottleneck: thousands of customers churn after one or two pickups. We built an autonomous customer retention and voice agent orchestrator that re-engages inactive customers.*
  > *Our architecture follows 6 stages: from customer CRM records, outbound multilingual voice calls via Bolna AI in Telugu, Hindi, and English, down to our FastAPI backend, Gemini-powered structured extraction, and a deterministic Orchestrator agent that assigns the exact Next-Best Action."*

#### 0:30 - 1:00 | The 4-Tier Lead Intelligence
* **Visual**: Point your cursor at the 4 Metric Cards (Hot Lead, Warm Lead, Cold Lead, Do Not Call).
* **Talking Points**:
  > *"The Orchestrator classifies every call into 4 strict tiers:*
  > *1. Hot Lead: Customer confirmed scrap details and near-term availability. Next-Best Action: Create pickup request for coordinator.*
  > *2. Warm Lead: Customer requested a callback or call was not answered. Next-Best Action: Schedule follow-up call.*
  > *3. Cold Lead: Polite decline for now. Next-Best Action: Add to future nurture list.*
  > *4. Do Not Call: Explicit refusal or wrong number. We automatically enforce customer opt-out."*

#### 1:00 - 1:40 | Live One-Click Webhook Simulation
* **Visual**: Click the **`🔥 Hot Lead`** button in the Live Simulator.
* **Talking Points**:
  > *"Let's simulate a live outbound call right now. Suresh Kumar in Kukatpally spoke with our agent in a mix of Telugu and English, offering 15kg newspapers and an iron almirah tomorrow morning.*
  > *With one click, Bolna posts the webhook to our FastAPI backend. The LLM extracts the materials, locality, and availability, and the Orchestrator classifies it as a Hot Lead."*
* **Visual**: Scroll down to the table to show the new lead, then click **`View`** to reveal the modal showing the bilingual transcript and structured JSON insights.

#### 1:40 - 2:05 | Coordinator Dispatch & Outbound Queue
* **Visual**: Click on the **`📋 Audit Action Queue`** tab, then the **`✉️ Outbound Message Queue`** tab.
* **Talking Points**:
  > *"Every decision is recorded in our dedicated `orchestrator_actions` audit table. Under the Outbound Message Queue, you can see that a coordinator dispatch ticket was automatically created with full scrap details, digital weighment instructions, and customer availability, while customer-facing WhatsApp follow-ups were prepared."*

#### 2:05 - 2:30 | Conclusion & Impact
* **Visual**: Show the full dashboard and swagger docs at `/docs`.
* **Talking Points**:
  > *"This enables Crapbin to recover thousands of inactive customers with zero human calling overhead, ensuring coordinators only spend time on high-intent pickups while respecting customer opt-outs."*

---

## 🛠️ CLI Utilities & Verification Suite

If you want to inspect or test via terminal:

| Command | Description |
| :--- | :--- |
| `python followup.py --report` | Displays the full 4-tier terminal report with lead breakdown and audit queues |
| `python followup.py --backfill` | Migrates DB and backfills existing calls without calling the LLM |
| `python followup.py --reprocess` | Clears and re-runs full LLM extraction & classification on all calls |
| `python send_test_webhook.py --hot` | Simulates a Hot Lead call from CLI |
| `python send_test_webhook.py --warm` | Simulates a Warm Lead (callback) call from CLI |
| `python send_test_webhook.py --cold` | Simulates a Cold Lead call from CLI |
| `python send_test_webhook.py --dnc` | Simulates an Opt-out / Do Not Call call from CLI |
| `python send_test_webhook.py --no-answer` | Simulates an unanswered call from CLI |
| `python test_verification.py` | Runs the automated 4-stage verification suite |

---

## 📡 REST API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Web Dashboard for demo and visualization |
| `GET` | `/health` | Service status, LLM provider, and active model |
| `GET` | `/summary` | Tier counts, action totals, and message metrics |
| `GET` | `/calls` | All calls with parsed insights and classifications |
| `GET` | `/actions` | Audit log rows from `orchestrator_actions` (filterable via `?classification=`) |
| `GET` | `/messages` | Queued and dispatched messages (coordinator emails, WhatsApp) |
| `POST` | `/webhook/bolna` | Bolna voice agent webhook receiver (`?token=` or header auth) |
| `POST` | `/api/simulate/{tier}` | One-click simulation of `hot`, `warm`, `cold`, `dnc`, or `no-answer` |
| `POST` | `/api/reset-demo` | Resets database back to the 11 baseline calls |

---

## 👥 Authors
* Developed for Crapbin Buildathon Demo.

