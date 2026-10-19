# Assignment 3 Analysis: Hybrid Supply Chain Planning Agent

**Course:** UCLA Extension — Agentic AI Course (Week 3: Planning & Reasoning)  
**Model Configuration:** OpenAI `gpt-6-luna` (`reasoning_effort: "none"`) via `https://api.openai.com/v1/chat/completions`  

### Model Selection & Workflow Adaptations (Anthropic $\rightarrow$ OpenAI + n8n 2.x)
1. **Why `gpt-6-luna` instead of `claude-sonnet-4-6`:** We did not have an Anthropic account or API key, and OpenAI's `gpt-6-luna` (`reasoning_effort: "none"`) is roughly **30× cheaper** (`$0.10/1M` input tokens, `$0.50/1M` output tokens vs. `$3.00/$15.00` on Sonnet) with sub-second latency while reliably adhering to strict JSON schemas at the classical/LLM seam.
2. **API Request & Response Envelope Changes:** Without altering the 20-node HTN topology, we updated the 5 HTTP Request nodes (`Master Planner Agent`, `Forecast Context Adjuster`, `Inventory Exception Handler`, `Supplier Performance Monitor`, and `Logistics Coordinator (LLM)`) from `https://api.anthropic.com/v1/messages` (`x-api-key`) to `https://api.openai.com/v1/chat/completions` (`Authorization: Bearer` via `OpenAI Header Auth`), moved the top-level `"system"` prompt into `"messages": [{"role": "system", ...}, {"role": "user", ...}]`, and updated the JSON parsers in `Parse Plan`, `Use Classical Fallback?`, and `Final Output` to unwrap OpenAI's `$json.choices?.[0]?.message?.content` alongside Anthropic's `$json.content?.[0]?.text`.
3. **n8n 2.x File Access & `v0` Execution Order:** We added `N8N_RESTRICT_FILE_ACCESS_TO=/data` in `docker-compose.yml` so n8n 2.x permits reading `/data/*.csv`. Additionally, in n8n's default `v1` (depth-first) execution order, `Read Inventory JSON` triggered `Build Context Summary` before `Read Sales JSON` had executed unless the CSV nodes were manually pinned. Switching workflow settings to `"executionOrder": "v0"` (breadth-first) ensures all four CSV branches finish reading before `Build Context Summary` executes, with zero pinned data.

---

## 1. Algorithm Comparison (`DF-1`, `DF-2`, `DF-3`)

To evaluate the **Classical Baseline, LLM Revision** pattern (Pattern A), we compare three representative SKUs from the `Demand Forecast Engine` (`movingAverage` × `seasonalIndex` for target month `2026-01`) against the `Forecast Context Adjuster` (`gpt-6-luna`):

| SKU | Product Name | Category / Behavior | 3-Month Moving Avg (`DF-1`) | Jan Seasonal Index (`DF-2`) | Classical Forecast (`forecast_units`) | Statistical Confidence | LLM Revised Forecast (`DF-3`) | Delta (%) |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`SKU-011`** | Stainless Bottle | Evergreen / Stable | `76.0` | `0.98` | **`75` units** | `0.98` | **`75` units** | `0%` |
| **`SKU-007`** | Wool Gloves | Winter / Viral Spike | `376.7` | `0.94` | **`354` units** | `0.25` | **`496` units** | **`+40.0%`** |
| **`SKU-013`** | Wired Earbuds | Electronics / Secular Decline | `45.7` | `1.52` | **`69` units** | `0.08` | **`34` units** | **`-50.7%`** |

### Why the Classical and LLM Forecasts Diverge
1. **Well-Behaved Evergreen Baseline (`SKU-011` — Stainless Bottle):**
   - **Classical Math:** Trailing 3-month sales (`78, 76, 74`) average `76.0` units. January 2025 sales (`74`) divided by the 12-month mean (`75.25`) yield a `seasonal_index` of `0.98`, producing `75` units with `0.98` statistical confidence.
   - **LLM Revision:** Because `statistical_confidence` is `0.98` and no anomaly exists, `gpt-6-luna` leaves the classical forecast untouched (`75` units, `delta_pct: 0%`).
2. **Viral Surge Anomaly (`SKU-007` — Wool Gloves):**
   - **Classical Math Failure:** Demand exploded in Q4 (`100` in Sep $\rightarrow$ `218` in Oct $\rightarrow$ `394` in Nov $\rightarrow$ `518` in Dec). While the 3-month moving average rises to `376.7`, the 12-month `seasonalIndex` for January compares pre-viral Jan 2025 (`131`) against a Q4-inflated annual mean (`139.42`), yielding an artificially depressed January index of `0.94`! Every other winter SKU (`SKU-006`, `008`, `009`, `010`) has a January seasonal index around `1.82`. Consequently, the classical model *shrinks* the forecast to `354` units—well below December's `518` units.
   - **LLM Revision:** Detecting a winter item in January with high `moving_avg` (`376.7`), low `statistical_confidence` (`0.25`), and an anomalously sub-1.0 `seasonal_index` (`0.94`), `gpt-6-luna` revises the forecast upward by **`+40%` to `496` units**.
3. **Secular Decline Anomaly (`SKU-013` — Wired Earbuds):**
   - **Classical Math Failure:** Sales declined monotonically every single month from `120` in Jan 2025 down to `38` in Dec 2025 (`moving_avg = 45.7`). However, with only 12 months of history, `seasonalIndex("2026-01")` divides Jan 2025 (`120`, the top of the linear slide) by the annual mean (`79.0`), mistaking a secular decline for a `+52%` "January seasonal boom" (`seasonal_index = 1.52`) and projecting `69` units!
   - **LLM Revision:** Recognizing a legacy electronics item with `statistical_confidence = 0.08` and a false seasonal bounce, `gpt-6-luna` overrides the classical number downward by **`-50.7%` to `34` units**, continuing the actual downward trajectory below December's `38` units.

**Which to trust for reorder sizing:** For `SKU-011` (and the 12 other well-behaved seasonal/evergreen SKUs), we trust the **classical baseline** because it is deterministic, zero-cost, and free of hallucination drift. For **`SKU-007`** and **`SKU-013`**, we trust the **LLM-revised forecast** because a single-year seasonal index fundamentally confounds non-stationary trends (viral acceleration or linear decay) with cyclical seasonality.

---

## 2. EOQ Assumption Analysis (`EOQ-1`, `EOQ-2`, `EOQ-3`)

Wilson's Economic Order Quantity ($Q^* = \sqrt{2DS/H}$) is a closed-form STRIPS-style operator whose validity depends on constant demand, short predictable lead times, non-perishability, and sufficient velocity. In `detectViolations()` (`custom-nodes/eoq-optimizer.js`), we implemented four explicit assumption checks:

| Flag Name | Threshold Implemented in `detectViolations()` | Triggered SKUs in `current_inventory.csv` | Classical `eoq_units` | Downstream LLM Exception Action (`EOQ-3`) | Agreement & Operational Rationale |
| :--- | :--- | :--- | :---: | :--- | :--- |
| **`viral_spike`** | $\text{mean}(\text{last } 3\text{m}) > 2.5 \times \text{mean}(\text{prior } 9\text{m})$ | **`SKU-007`** (Wool Gloves: $376.7 > 2.5 \times 60.3$; `on_hand: 18`, `reorder_point: 80`) | `431` | **`reorder` (`862` units)** | **Agree.** Trailing annual demand ($D = 1,673$) understates current ~500+/mo run rate, and `on_hand` (`18`) is critically below `reorder_point` (`80`). |
| **`declining`** | $\text{mean}(\text{last } 3\text{m}) < 0.5 \times \text{mean}(\text{first } 3\text{m})$ | **`SKU-013`** (Wired Earbuds: $45.7 < 0.5 \times 112.3$; `on_hand: 540`, `reorder_point: 80`) | `397` | **`liquidate` (`0` units)** | **Agree.** With `540` units on hand ($6.75\times$ the reorder point of `80`) and sales falling to `38`/mo, ordering `397` more units would trap working capital for >14 months. |
| **`long_lead_time`** | `lead_time_days > 28` **AND** demand $\text{CV} > 0.50$ | **`SKU-005`** (35d), **`SKU-006`** (30d), **`SKU-007`** (30d), **`SKU-009`** (42d) | `149` (`SKU-005`), `135` (`SKU-006`), `161` (`SKU-009`) | **`hold` (`0` units)** when `on_hand > 1.5x reorder_point` | **Agree.** While lead times exceed 28 days under seasonal volatility, current `on_hand` for `SKU-005` (`140` vs `30`), `SKU-006` (`95` vs `40`), and `SKU-009` (`75` vs `35`) exceeds $2\times$ their reorder points. |
| **`low_velocity`** | $\text{annualDemand} < 60$ units/year ($< 5$/month) | *None in demo CSV* (lowest is `SKU-005` at $D = 666$) | N/A | **`hold` / fixed 30-day demand** | **Agree.** Included to guard against square-root batch instability on sparse tail SKUs. |

**Omitted Assumption Check (Perishability):** We intentionally omitted a perishability flag in `detectViolations()` because `data/current_inventory.csv` contains only hardlines and apparel (no perishable SKUs or shelf-life column). In a production grocery or cosmetics deployment, a `shelf_life_days < (eoq_units / daily_demand)` check would be mandatory to prevent spoilage cliffs.

---

## 3. Supplier Rubric Defense (`SP-1`)

We designed our 100-point rubric in `Supplier Performance Monitor` specifically for **Coastal Goods' business model: a Direct-to-Consumer (DTC) seasonal e-commerce retailer**. In seasonal DTC retail, missing a seasonal window (selling winter gloves in March or beach towels in October) destroys revenue, and defective merchandise drives customer churn and return shipping costs.

### Dimension Weights (Total = 100 Points)
1. **Reliability (`35 / 100`):** Proxied by `on_time_rate`. Late deliveries directly cause stockouts during peak seasonal spikes (`SKU-007`).
2. **Quality (`25 / 100`):** Proxied by `1 - defect_rate`. DTC brand equity is highly sensitive to customer reviews and return rates.
3. **Lead Time (`25 / 100`):** Proxied by `avg_lead_time_days`. Shorter lead times allow agile mid-season reorders without over-committing inventory up front.
4. **Cost / Working Capital (`15 / 100`):** Proxied by `payment_terms_days` (`30`, `45`, or `60` days). While net-60 terms improve cash conversion, extended payment terms cannot compensate for late or defective inventory.

### Real Run Ranking Validation
- **Top-Ranked Supplier:** **`SUP-001` (GreenLeaf Goods, USA)** — **`93 / 100` (`preferred`)** (`reliability: 35`, `quality: 25`, `lead_time: 25`, `cost: 8`). With a 7-day lead time, 97% on-time rate, and 0.8% defect rate, it is the ideal agile domestic partner. Close second: **`SUP-006` (Bavaria Paper Mills)** at **`89 / 100` (`preferred`)** (98% on-time, 0.5% defect rate, 18-day lead time).
- **Bottom-Ranked Supplier:** **`SUP-002` (Pacific Rim Trading, China)** — **`57 / 100` (`deprioritize`)** (`reliability: 20`, `quality: 9`, `lead_time: 13`, `cost: 15`). Despite offering generous 60-day payment terms (`cost: 15/15`), its 84% on-time rate, 2.2% defect rate, and 28-day lead time make it a major operational bottleneck—especially since `SUP-002` is the sole supplier for viral `SKU-007` (Wool Gloves)!

---

## 4. Run Metrics & Demo Learnings (`gpt-6-luna`, `reasoning_effort: "none"`)

Across four end-to-end executions covering all four `next_subgoal` branches (`forecast`, `inventory`, `supplier`, `logistics`), we recorded token consumption from the OpenAI `usage` envelopes, our OpenAI Usage dashboard (`648` total requests / `468,067` total tokens = `$0.06` cumulative across all development and testing), and wall-clock latency from n8n:

| Run # | Goal Focus (`next_subgoal` Routed) | Calls per Pass (`×4` CSV Runs) | Tokens per Pass (`×4` CSV Runs) | Cost per Pass (`×4` Runs) on `gpt-6-luna` | Equivalent `×4` Cost (`claude-sonnet-4-6`) | Wall-Clock Latency |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **1** | Q1 Demand & Action Plan (`forecast`) | `16` (`64` total) | `9,605` (`38,420` total) | **`$0.0017`** (`$0.0068`) | `~$0.052` (`~$0.208`) | `~6.8 s` |
| **2** | Inventory EOQ & Exception Triage (`inventory`) | `16` (`64` total) | `11,325` (`45,300` total) | **`$0.0017`** (`$0.0068`) | `~$0.051` (`~$0.204`) | `~6.4 s` |
| **3** | Vendor Reliability Audit (`supplier`) | `2` (`8` total) | `2,574` (`10,296` total) | **`$0.00055`** (`$0.0022`) | `~$0.016` (`~$0.064`) | `~2.3 s` |
| **4** | Carrier Coverage & Routing (`logistics`) | `14` (`56` total) | `20,344` (`81,376` total) | **`$0.0024`** (`$0.0096`) | `~$0.071` (`~$0.284`) | `~5.9 s` |

### Key Observations & What Surprised Us (`64` Calls per Workflow Execution!)
1. **Two Compounding Call Multipliers in the Starter Topology (`4 × 16 = 64` API calls per click):**
   - **Multiplier 1 — 4 CSV Wires into `Build Context Summary` (`4×`):** Because all four `Read ... JSON` nodes wire directly into `Build Context Summary`'s single input port without a `Merge` barrier, n8n executes `Build Context Summary` and the entire downstream workflow **4 times (`Run 1 of 4` through `Run 4 of 4`)** per click of *Execute Workflow*.
   - **Multiplier 2 — Per-Item HTTP Fan-Out (`15×` / `13×`):** Because n8n executes an HTTP Request node once per incoming item, `Demand Forecast Engine` (`15` SKUs), `Inventory EOQ Planner` (`15` SKUs—wired directly to the LLM without an `IF needs_llm_review` filter), and `Build Shipping Requests` (`3` requests + `10` catalogue option rows = `13` items) make `15`, `15`, and `13` LLM calls per pass respectively.
   - **Combined Impact:** A single click on `forecast` or `inventory` fires **$4 \times (1 \text{ Planner} + 15 \text{ SKU}) = 64\text{ LLM requests}$** (`~38K–45K` tokens), and `logistics` fires **$4 \times (1 \text{ Planner} + 13 \text{ Shipping}) = 56\text{ LLM requests}$** (`~81K` tokens). Across our entire multi-day development and testing (`648` total requests, `468,067` tokens), our cumulative OpenAI spend was only **`$0.06` total** (`<$0.01` per full `4×` execution)—whereas `468K` tokens on `claude-sonnet-4-6` would have cost `~$2.10` (`>$0.20` per `4×` execution, exceeding the `$0.10` per-run target unless deduplicated).
2. **How `Merge Subgoal Results` & the `Logistics` Branch Create `8` Runs at `Final Output`:**
   - For `forecast`, `inventory`, and `supplier`, only one Switch branch fires per run, so `Merge Subgoal Results` (`Mode: Append`, all 5 branch tails connected to `Input 1`) acts as a 5-into-1 funnel passing the 4 runs into `Final Output` (which collapses each 15-SKU run into 1 summary report).
   - On the `logistics` branch, `Use Classical Fallback?` splits each of the 4 runs across **both** its `false` wire (LLM soft-constraint handling, e.g., `REQ-002` infeasible 3-day deadline) and its `true` wire (`Classical Logistics Fallback` greedy solver for `REQ-001` and `REQ-003`). Because both wires connect into `Input 1` of `Merge Subgoal Results`, n8n processes the 4 LLM-wire runs as **`Runs 1–4 of 8`** and the 4 Classical Greedy runs as **`Runs 5–8 of 8`** in `Final Output`.
3. **Automatic Prompt Caching:** In Runs 3 and 4, OpenAI's `prompt_tokens_details` reported `cache_write_tokens` (`1,059`–`1,443` tokens) because the shared system prompt and shipping options table formed a static prefix across parallel item evaluations.

---

## 5. Reflection on the Four Primer Questions (`docs/planning-primer.md` §8)

1. **Where does a classical algorithm produce a wrong answer that the LLM catches, and where does the LLM produce a wrong answer (or defer) that the classical layer handles?**
   - *Classical wrong, LLM catches:* In Forecasting (`DF-2`), the 1-year `seasonalIndex` mistakes `SKU-013`'s linear decline (`120` in Jan $\rightarrow$ `38` in Dec) for a `1.52x` January seasonal spike (`69` units), and in Inventory (`EOQ-1`), Wilson's EOQ recommends ordering `397` more units of `SKU-013` despite `540` units already sitting on hand. Both times, the downstream LLM (`DF-3` and `EOQ-3`) overrides the math (`34` units forecast; `liquidate` with `0` reorder).
   - *LLM defers/caught by classical:* In Logistics (`LG-1`/`LG-2`), when evaluating `REQ-003` (`1200 kg` Europe $\rightarrow$ North America, 21d deadline), the LLM sets `use_classical_fallback: true` and hands execution to `pickCheapestFeasible()`, which deterministically verifies all 5 hard constraints across the request set, computing exact dollar totals (`$10,030` for `REQ-001`, infeasible `null` for `REQ-002`'s impossible 3-day North America ground deadline, and `$1,980` for `REQ-003`) without arithmetic hallucination.
2. **If your Master Planner emitted `next_subgoal: "audit_warehouse"` (unknown to the Switch), what happens and how would you prevent silent failure?**
   - Currently, `Route on Subgoal` has `fallbackOutput: "extra"`, which has **no downstream wire** connected in the starter topology—so the execution would terminate silently with 0 items reaching `Final Output`! To make the agent safe, we should wire the Switch node's **`extra` fallback output** to an explicit error/re-prompt handler (or a `Stop and Error` node / validation loop) and enforce an explicit JSON Schema `enum: ["forecast", "inventory", "supplier", "logistics"]` via OpenAI `response_format: { type: "json_schema" }`.
3. **Why does EOQ give a confident, wrong answer for `SKU-013` (Wired Earbuds), and what flag in `detectViolations()` prevents it from reaching production?**
   - EOQ sums trailing 12-month sales ($D = 948$) and assumes stationary, perpetual demand ($Q^* = \sqrt{(2 \times 948 \times 50)/0.60} = 397$ units), completely blind to the fact that monthly sales fell from `120` to `38` and that current `on_hand` (`540`) already exceeds $6.75\times$ the reorder point (`80`). Our **`"declining"`** flag ($\text{last3Mean } 45.7 < 0.5 \times \text{first3Mean } 112.3$) catches this non-stationarity and routes `SKU-013` to the exception handler for liquidation.
4. **Why is supplier scoring an LLM job and EOQ a classical job? What would change if we swapped them?**
   - EOQ is a convex single-variable optimization problem with a closed-form analytical minimum ($O(1)$ compute, exact, zero cost), whereas supplier evaluation requires synthesizing multi-dimensional trade-offs (e.g., weighing `SUP-002`'s generous 60-day payment terms against its poor 84% on-time rate and 2.2% defect rate) and generating actionable natural-language negotiation recommendations. Swapping them would mean paying API latency and token costs for an LLM to approximate square roots inaccurately while forcing a brittle hard-coded formula to generate nuanced vendor management prose.

---

## Demo video

- **Video Link:** [https://youtu.be/5nLFskf031g](https://youtu.be/5nLFskf031g)

