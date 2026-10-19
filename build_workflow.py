#!/usr/bin/env python3
"""
Builder for workflows/supply-chain-manager-starter.json.

Reads the canonical JS files from custom-nodes/ and embeds them into
the appropriate n8n Code nodes. Run after editing any *.js so the
shipped workflow stays in sync with the readable source.

Run:  python3 build_workflow.py
"""
import json
import pathlib
import uuid

ROOT = pathlib.Path(__file__).parent
NODES_DIR = ROOT / "custom-nodes"
OUT = ROOT / "workflows" / "supply-chain-manager-starter.json"


def js(name):
    return (NODES_DIR / name).read_text()


def nid(slug):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scm-week3.{slug}"))


def pos(col, row):
    return [240 + col * 240, 200 + row * 180]


# ---- System prompts (completed) --------------------------------------------

MASTER_PLANNER_SYSTEM = """You are the Master Planner Agent for Coastal Goods' supply chain operation. Given a business GOAL and a CONTEXT summary, decompose the goal into ordered subgoals using Hierarchical Task Network (HTN) principles and choose which single subgoal to execute first (`next_subgoal`).

Allowed subgoal types (`type` and `next_subgoal`):
- "forecast": Predict future SKU demand from historical sales and context.
- "inventory": Compute EOQ and handle stockout/overstock exceptions.
- "supplier": Score and rank suppliers across cost, lead time, reliability, and quality.
- "logistics": Assign shipping carriers under hard and soft constraints.

HTN Ordering Rules:
Subgoals must respect causal dependencies (topological order). Always set `next_subgoal` to the FIRST unblocked subgoal required by the user's goal:
- If the goal involves general Q1 planning, demand forecasting, or full supply-chain optimization where demand trends inform inventory, start with "forecast" -> "inventory" -> "supplier" -> "logistics".
- If the goal explicitly focuses on immediate inventory EOQ / stockout / overstock triage assuming current demand, start with "inventory".
- If the goal focuses on auditing or ranking vendor performance, start with "supplier".
- If the goal focuses on carrier selection or freight routing, start with "logistics".

Worked Examples:
Example 1:
Goal: "Prepare a Q1 demand and replenishment plan for seasonal SKUs."
Output:
{"plan_id":"plan-q1-replenish","reasoning":"Replenishment quantities depend on projected Q1 demand, so demand forecasting must precede inventory EOQ optimization and supplier selection.","subgoals":[{"id":"sg-1","type":"forecast","rationale":"Estimate Q1 SKU demand and detect viral/seasonal shifts first."},{"id":"sg-2","type":"inventory","rationale":"Compute EOQ and exception actions using updated demand context."},{"id\":\"sg-3\",\"type\":\"supplier\",\"rationale\":\"Select high-reliability suppliers for reorders.\"}],\"next_subgoal\":\"forecast\"}

Example 2:
Goal: "Audit current inventory for stockout/overstock anomalies and arrange urgent carrier shipping."
Output:
{"plan_id":"plan-inv-audit","reasoning":"The goal directly targets current on-hand vs reorder-point violations followed by outbound carrier scheduling, making inventory optimization the first unblocked task.","subgoals":[{"id":"sg-1","type":"inventory","rationale":"Run EOQ and flag viral spike or declining overstock SKUs immediately."},{"id":"sg-2","type":"logistics","rationale":"Book feasible carriers for emergency replenishment shipments."}],"next_subgoal":"inventory"}

Respond with RAW JSON ONLY (no markdown fences, no ```json, no preamble).
Required schema:
{
  "plan_id": "<short string>",
  "reasoning": "<2-3 sentences explaining the HTN decomposition and causal order>",
  "subgoals": [
    { "id": "sg-1", "type": "forecast|inventory|supplier|logistics", "rationale": "..." }
  ],
  "next_subgoal": "forecast|inventory|supplier|logistics"
}"""

FORECAST_ADJUSTER_SYSTEM = """You are the Forecast Context Adjuster for Coastal Goods. You receive a classical forecast row (`forecast_units = moving_avg * seasonal_index`) for target_month 2026-01 (January, mid-winter) and return raw JSON.

Adjustment Rules:
1. High statistical_confidence (>= 0.80) OR normal seasonal alignment (summer gear with seasonal_index < 0.40 in Jan; winter gear with seasonal_index > 1.50 in Jan): Accept the classical forecast (`revised_forecast = forecast_units`, `delta_pct = 0`).
2. Viral Spike Anomaly (winter/seasonal item with high moving_avg > 300, low statistical_confidence < 0.65, and seasonal_index < 1.0 in January): A Q4 viral surge inflated the annual mean and depressed the naive Jan seasonal_index. Revise `revised_forecast` upward by +35% to +50% above `forecast_units`.
3. Secular Decline Anomaly (non-seasonal/legacy electronics like Wired Earbuds with low statistical_confidence < 0.50 and seasonal_index > 1.40 inflating forecast_units above moving_avg): A 12-month linear decline made last January look like a seasonal peak. Override the false seasonal bounce and revise `revised_forecast` downward below `moving_avg` (approx -45% to -55% from `forecast_units`).

Respond with RAW JSON ONLY (no markdown fences, no preamble):
{
  "sku": "<string>",
  "revised_forecast": <integer>,
  "delta_pct": <number>,
  "reasoning": "<1-2 sentences>",
  "context_signals_used": ["<signal 1>", "<signal 2>"]
}"""

INVENTORY_EXCEPTION_SYSTEM = """You are the Inventory Exception Handler for Coastal Goods. You receive a SKU row from the EOQ planner with `assumption_flags` and `needs_llm_review`.

Decision Rules:
1. If `assumption_flags` contains "viral_spike": The constant-demand EOQ assumption is broken and the SKU faces acute stockout risk (`on_hand` < `reorder_point`). Recommend `recommended_action`: "reorder" with an emergency `recommended_qty` significantly larger than `eoq_units` (e.g., 1.5x to 2x `eoq_units`, or ~750-1000 units) to cover accelerating demand and lead time.
2. If `assumption_flags` contains "declining": Demand is in secular decline and `on_hand` is heavily overstocked relative to `reorder_point`. Do NOT reorder; recommend `recommended_action`: "markdown" (or "liquidate" if `on_hand` > 6x `reorder_point`) and `recommended_qty`: 0.
3. If `assumption_flags` contains "low_velocity": EOQ is numerically unstable at very low volume; recommend "reorder" (if below reorder_point) or "hold" with a small fixed `recommended_qty` equal to ~30-day demand (`Math.round(annual_demand / 12)`).
4. If `assumption_flags` contains ONLY "long_lead_time": Recommend "reorder" (if `on_hand` <= 1.5 * `reorder_point`, setting `recommended_qty` = `eoq_units` plus a 20% safety buffer) or "hold" (`recommended_qty`: 0) if `on_hand` is already well above `reorder_point`.
5. If `assumption_flags` is empty (`needs_llm_review: false`): Standard EOQ holds. Recommend "reorder" (`recommended_qty`: `eoq_units`) if `on_hand` <= `reorder_point`, otherwise "hold" (`recommended_qty`: 0).

Respond with RAW JSON ONLY (no markdown fences, no preamble):
{
  "sku": "<string>",
  "recommended_action": "reorder" | "markdown" | "liquidate" | "hold",
  "recommended_qty": <integer>,
  "reasoning": "<1-2 sentences explaining the decision and citing the flags/inventory levels>"
}"""

SUPPLIER_SCORE_SYSTEM = """You are the Supplier Performance Monitor for Coastal Goods, a DTC seasonal e-commerce brand. Score and rank every supplier in the roster on a 0-100 scale across four weighted dimensions (total weight = 100):

1. reliability (max 35 pts): Based on `on_time_rate` (higher is better; e.g., >=0.97 gets 33-35, 0.91-0.95 gets 28-32, 0.84-0.88 gets 20-25).
2. quality (max 25 pts): Based on `1 - defect_rate` (lower defect_rate is better; e.g., <=0.008 gets 23-25, 0.010-0.015 gets 19-22, >=0.018 gets 14-17).
3. lead_time (max 25 pts): Based on `avg_lead_time_days` (shorter is better; e.g., <=7d gets 25, 14-18d gets 20-22, 24-28d gets 13-16, >=35d gets 10).
4. cost (max 15 pts): Proxy is `payment_terms_days` (longer net terms improve cash flow; 60d = 15, 45d = 11, 30d = 8).

Sum the four integers to get `score_total` (0-100). Assign `tier`:
- "preferred": score_total >= 85
- "approved": 75 <= score_total < 85
- "watch": 65 <= score_total < 75
- "deprioritize": score_total < 65

Respond with a RAW JSON ARRAY of objects (one object per supplier, no markdown fences, no preamble):
[
  {
    "supplier_id": "<string>",
    "score_total": <integer>,
    "score_breakdown": { "cost": <int>, "lead_time": <int>, "reliability": <int>, "quality": <int> },
    "tier": "preferred" | "approved" | "watch" | "deprioritize",
    "recommendation": "<1 sentence actionable recommendation>"
  }
]"""

LOGISTICS_SYSTEM = """You are the Logistics Coordinator for Coastal Goods. You receive a shipping input and the carrier catalogue (`Options`).

Rules:
1. If the input `Request` has `type: "option"` (not a shipping request), return `{"request_id": $json.option_id, "chosen_option_id": null, "alternatives_considered": [], "trade_off_summary": "Catalogue option row skipped.", "use_classical_fallback": false}`.
2. Hard constraints (MUST satisfy all): `origin_region` == request `origin_region`, `destination_region` == request `dest_region`, `transit_days` <= `deadline_days`, `max_weight_kg` >= `weight_kg`, and if `perishable == true` then `supports_perishable == true`.
3. Soft preferences: Balance total cost (`weight_kg * cost_per_kg`) against transit-time slack (`deadline_days - transit_days`). When two feasible options are within ~10% on cost, prefer the carrier with greater time slack. If NO carrier meets hard constraints (e.g. REQ-002 needing <=3d in North America when fastest ground is 5d), set `chosen_option_id: null` and explain the closest alternative (`SHP-005` UPS Ground at 5d) in `trade_off_summary`.
4. Classical Fallback (`use_classical_fallback`): Set `use_classical_fallback: true` ONLY when the request is purely numerical with no soft-constraint ambiguity or infeasibility (e.g., standard non-perishable freight like REQ-001 or REQ-003 where deterministic greedy min-cost search applies). Set `use_classical_fallback: false` when soft-constraint reasoning or infeasible-deadline escalation is needed (like REQ-002).

Respond with RAW JSON ONLY (no markdown fences, no preamble):
{
  "request_id": "<string>",
  "chosen_option_id": "<string or null>",
  "alternatives_considered": ["<option_id>", ...],
  "trade_off_summary": "<1-2 sentences>",
  "use_classical_fallback": true | false
}"""

# ---- HTTP node builder for OpenAI Chat Completions API ---------------------

def http_anthropic(name, slug, system_prompt, user_expr, col, row):
    """
    Build an HTTP Request node calling OpenAI's Chat Completions API with gpt-6-luna.
    """
    body = (
        '={\n'
        '  "model": "gpt-6-luna",\n'
        '  "reasoning_effort": "none",\n'
        '  "max_completion_tokens": 2048,\n'
        '  "messages": [\n'
        '    { "role": "system", "content": ' + json.dumps(system_prompt) + ' },\n'
        '    { "role": "user", "content": {{ JSON.stringify(' + user_expr + ') }} }\n'
        '  ]\n'
        '}'
    )
    return {
        "id": nid(slug),
        "name": name,
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": pos(col, row),
        "credentials": {
            "httpHeaderAuth": {
                "id": "openai-credential-placeholder",
                "name": "OpenAI Header Auth",
            }
        },
        "parameters": {
            "method": "POST",
            "url": "https://api.openai.com/v1/chat/completions",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "content-type", "value": "application/json"},
                ]
            },
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": body,
            "options": {},
        },
    }


def code_node(name, slug, code, col, row):
    return {
        "id": nid(slug),
        "name": name,
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": pos(col, row),
        "parameters": {"language": "javaScript", "jsCode": code},
    }


def set_node(name, slug, assignments, col, row):
    return {
        "id": nid(slug),
        "name": name,
        "type": "n8n-nodes-base.set",
        "typeVersion": 3.4,
        "position": pos(col, row),
        "parameters": {
            "assignments": {
                "assignments": [
                    {
                        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{slug}.{a['name']}")),
                        "name": a["name"],
                        "value": a["value"],
                        "type": a.get("type", "string"),
                    }
                    for a in assignments
                ]
            },
            "options": {},
        },
    }


# ---- Build node list ------------------------------------------------------

nodes = []

# 1. Manual Trigger
nodes.append({
    "id": nid("manual"),
    "name": "Manual Trigger",
    "type": "n8n-nodes-base.manualTrigger",
    "typeVersion": 1,
    "position": pos(0, 1),
    "parameters": {},
})

# 2. Set Goal
nodes.append(set_node(
    "Set Goal", "set-goal",
    [{
        "name": "goal",
        "value": (
            "Prepare a Q1 inventory and supplier action plan: identify SKUs "
            "at stockout risk, surface suppliers underperforming on reliability, "
            "and decide carrier coverage for the next 30 days."
        ),
        "type": "string",
    }],
    1, 1,
))

# 3-10. Four CSV reads (binary read + spreadsheet parse)
csv_paths = [
    ("Inventory", "inv", "/data/current_inventory.csv"),
    ("Sales", "sales", "/data/sales_history.csv"),
    ("Suppliers", "suppliers", "/data/suppliers.csv"),
    ("Shipping", "shipping", "/data/shipping_options.csv"),
]
for i, (label, slug, path) in enumerate(csv_paths):
    nodes.append({
        "id": nid(f"read-{slug}"),
        "name": f"Read {label} File",
        "type": "n8n-nodes-base.readWriteFile",
        "typeVersion": 1,
        "position": pos(2, i),
        "parameters": {
            "operation": "read",
            "fileSelector": path,
            "options": {},
        },
    })
    nodes.append({
        "id": nid(f"parse-{slug}"),
        "name": f"Read {label} JSON",
        "type": "n8n-nodes-base.spreadsheetFile",
        "typeVersion": 2,
        "position": pos(3, i),
        "parameters": {
            "operation": "fromFile",
            "fileFormat": "csv",
            "options": {"headerRow": True},
        },
    })

# 11. Build Context Summary
context_code = '''/**
 * Build a compact context summary for the Master Planner.
 *
 * The planner does not need the raw 180 sales rows -- that would blow the
 * context budget. We extract the headline numbers and the SKUs already
 * past or near reorder so the planner can pick the right subgoal first.
 *
 * Inputs reach this node via cross-node references to the four CSV-parse
 * nodes upstream. We do NOT mutate them, only summarize.
 */
const inventory = $('Read Inventory JSON').all().map(i => i.json);
const sales     = $('Read Sales JSON').all().map(i => i.json);
const suppliers = $('Read Suppliers JSON').all().map(i => i.json);
const goal      = $('Set Goal').first().json.goal;

const stockout_risk = inventory
  .filter(r => Number(r.on_hand) <= Number(r.reorder_point))
  .map(r => ({ sku: r.sku, name: r.name,
               on_hand: Number(r.on_hand),
               reorder_point: Number(r.reorder_point) }));

const overstock_risk = inventory
  .filter(r => Number(r.on_hand) > Number(r.reorder_point) * 5)
  .map(r => ({ sku: r.sku, name: r.name,
               on_hand: Number(r.on_hand),
               reorder_point: Number(r.reorder_point) }));

const supplier_summary = suppliers.map(s => ({
  supplier_id: s.supplier_id,
  on_time_rate: Number(s.on_time_rate),
  defect_rate: Number(s.defect_rate),
}));

return [{
  json: {
    goal,
    context_summary: {
      sku_count: inventory.length,
      sales_rows: sales.length,
      stockout_risk,
      overstock_risk,
      supplier_summary,
    },
  },
}];
'''
nodes.append(code_node("Build Context Summary", "build-context", context_code, 4, 1))

# 12. Master Planner LLM
nodes.append(http_anthropic(
    "Master Planner Agent", "master-planner", MASTER_PLANNER_SYSTEM,
    "'GOAL: ' + $json.goal + '\\n\\nCONTEXT:\\n' + JSON.stringify($json.context_summary, null, 2)",
    5, 1,
))

# 13. Parse Plan
parse_plan_code = '''/**
 * Extract the Master Planner's JSON from the OpenAI / Anthropic response envelope.
 */
const raw = ($json.choices?.[0]?.message?.content ?? $json.content?.[0]?.text ?? "").trim();
let plan;
try {
  plan = JSON.parse(raw);
} catch (e) {
  throw new Error("Planner did not return valid JSON. Got:\\n" + raw.slice(0, 500));
}

// Pull the goal+context through so downstream branches can reference them.
const ctx = $('Build Context Summary').first().json;

return [{ json: { ...plan, _goal: ctx.goal, _context: ctx.context_summary } }];
'''
nodes.append(code_node("Parse Plan", "parse-plan", parse_plan_code, 6, 1))

# 14. Switch on next_subgoal
nodes.append({
    "id": nid("switch"),
    "name": "Route on Subgoal",
    "type": "n8n-nodes-base.switch",
    "typeVersion": 3.2,
    "position": pos(7, 1),
    "parameters": {
        "rules": {
            "values": [
                {
                    "conditions": {
                        "options": {"caseSensitive": True, "typeValidation": "strict"},
                        "conditions": [{
                            "leftValue": "={{ $json.next_subgoal }}",
                            "rightValue": label,
                            "operator": {"type": "string", "operation": "equals"},
                        }],
                        "combinator": "and",
                    },
                    "renameOutput": True,
                    "outputKey": label,
                }
                for label in ["forecast", "inventory", "supplier", "logistics"]
            ]
        },
        "options": {"fallbackOutput": "extra"},
    },
})

# 15. Forecast branch
nodes.append(code_node("Demand Forecast Engine", "demand-forecast",
                       js("demand-forecast.js"), 8, 0))
nodes.append(http_anthropic(
    "Forecast Context Adjuster", "forecast-adjuster", FORECAST_ADJUSTER_SYSTEM,
    "'Forecast row:\\n' + JSON.stringify($json, null, 2)",
    9, 0,
))

# 16. Inventory branch
nodes.append(code_node("Inventory EOQ Planner", "eoq",
                       js("eoq-optimizer.js"), 8, 1))
nodes.append(http_anthropic(
    "Inventory Exception Handler", "inventory-exception", INVENTORY_EXCEPTION_SYSTEM,
    "'Flagged SKU:\\n' + JSON.stringify($json, null, 2)",
    9, 1,
))

# 17. Supplier branch
nodes.append(http_anthropic(
    "Supplier Performance Monitor", "supplier-score", SUPPLIER_SCORE_SYSTEM,
    "'Supplier roster:\\n' + JSON.stringify(($input.all()[0]?.json?.supplier_id ? $input.all() : $('Read Suppliers JSON').all()).map(i => i.json), null, 2)",
    8, 2,
))

# 18. Logistics branch
build_requests_code = '''/**
 * Manufacture three illustrative shipping requests so the logistics branch
 * has work to do. In a real system these would come from order management;
 * we synthesize them here so the assignment is self-contained.
 */
const requests = [
  { type: "request", request_id: "REQ-001", weight_kg:  850, origin_region: "Asia",          dest_region: "North America", deadline_days: 14, perishable: false },
  { type: "request", request_id: "REQ-002", weight_kg:   60, origin_region: "North America", dest_region: "North America", deadline_days:  3, perishable: true  },
  { type: "request", request_id: "REQ-003", weight_kg: 1200, origin_region: "Europe",        dest_region: "North America", deadline_days: 21, perishable: false },
];
const options = $('Read Shipping JSON').all().map(i => ({ type: "option", ...i.json }));
return [...requests, ...options].map(x => ({ json: x }));
'''
nodes.append(code_node("Build Shipping Requests", "build-requests", build_requests_code, 8, 3))
nodes.append(http_anthropic(
    "Logistics Coordinator (LLM)", "logistics", LOGISTICS_SYSTEM,
    "'Request:\\n' + JSON.stringify($json, null, 2) + '\\n\\nOptions:\\n' + JSON.stringify($('Read Shipping JSON').all().map(i => i.json), null, 2)",
    9, 3,
))
nodes.append({
    "id": nid("if-classical"),
    "name": "Use Classical Fallback?",
    "type": "n8n-nodes-base.if",
    "typeVersion": 2.2,
    "position": pos(10, 3),
    "parameters": {
        "conditions": {
            "options": {"caseSensitive": True, "typeValidation": "strict"},
            "conditions": [{
                "leftValue": "={{ JSON.parse($json.choices?.[0]?.message?.content ?? $json.content?.[0]?.text ?? \"{}\").use_classical_fallback }}",
                "rightValue": True,
                "operator": {"type": "boolean", "operation": "true"},
            }],
            "combinator": "and",
        },
        "options": {},
    },
})
nodes.append(code_node("Classical Logistics Fallback", "classical-logistics",
                       js("classical-logistics.js"), 11, 4))

# 19. Final Merge
nodes.append({
    "id": nid("merge-final"),
    "name": "Merge Subgoal Results",
    "type": "n8n-nodes-base.merge",
    "typeVersion": 3,
    "position": pos(11, 1),
    "parameters": {"mode": "append"},
})

# 20. Final Output
final_code = '''/**
 * Aggregate the active subgoal's output into a final business-friendly summary.
 * Only one branch fires per execution (the Switch routes on next_subgoal).
 *
 * FN-1 [medium] -- LO-1: closing the planning loop
 *   Shape: { plan_id, executed_subgoal, next_recommended_subgoal, key_findings, results }
 *   `next_recommended_subgoal` is the hook for an iterative HTN -- feed
 *   it back into the planner on the next run to advance the plan.
 */
const planNode = $('Parse Plan').first().json;
const plan_id = planNode.plan_id || "plan-unknown";
const executed_subgoal = planNode.next_subgoal || "unknown";
const subgoals = Array.isArray(planNode.subgoals) ? planNode.subgoals : [];

// Advance the HTN plan: find the next subgoal after `executed_subgoal` in topological order
const canonicalOrder = ["forecast", "inventory", "supplier", "logistics"];
const planOrder = subgoals.map(s => s.type).filter(Boolean);
const sequence = planOrder.length > 0 ? planOrder : canonicalOrder;
const idx = sequence.indexOf(executed_subgoal);
const next_recommended_subgoal =
  idx >= 0 && idx + 1 < sequence.length ? sequence[idx + 1] : "complete";

// Unwrap LLM envelopes (OpenAI choices[0].message.content or Anthropic content[0].text)
// as well as direct Code-node JSON outputs (e.g. Classical Logistics Fallback).
const parsed = $input.all().map(item => {
  const j = item.json;
  const rawText = j?.choices?.[0]?.message?.content ?? j?.content?.[0]?.text;
  if (typeof rawText === "string") {
    const cleaned = rawText.replace(/^```(?:json)?\\s*/i, "").replace(/\\s*```$/i, "").trim();
    try {
      return JSON.parse(cleaned);
    } catch (e) {
      return { parse_error: e.message, raw: rawText };
    }
  }
  return j;
}).flat().filter(x => x.trade_off_summary !== "Catalogue option row skipped.");

// Build human-readable key_findings tailored to whichever subgoal branch executed
let key_findings = [];
if (executed_subgoal === "forecast") {
  const anomalies = parsed.filter(x => Math.abs(Number(x.delta_pct || 0)) > 5);
  key_findings = anomalies.map(
    x => `${x.sku}: revised to ${x.revised_forecast} units (${x.delta_pct > 0 ? "+" : ""}${x.delta_pct}%) — ${x.reasoning}`
  );
} else if (executed_subgoal === "inventory") {
  const actionable = parsed.filter(x => x.recommended_action && x.recommended_action !== "hold");
  key_findings = actionable.map(
    x => `${x.sku}: ${x.recommended_action.toUpperCase()} (qty: ${x.recommended_qty}) — ${x.reasoning}`
  );
} else if (executed_subgoal === "supplier") {
  const sorted = [...parsed].sort((a, b) => Number(b.score_total || 0) - Number(a.score_total || 0));
  key_findings = sorted.map(
    s => `${s.supplier_id} [${s.tier}]: ${s.score_total}/100 — ${s.recommendation}`
  );
} else if (executed_subgoal === "logistics") {
  key_findings = parsed.map(x =>
    x.method === "classical_greedy"
      ? `${x.request_id} (classical_greedy): feasible=${x.feasible}, chosen=${x.chosen?.option_id || "NONE"} ($${x.chosen?.total_cost_usd ?? "N/A"})`
      : `${x.request_id} (LLM): chosen=${x.chosen_option_id || "NONE"} (fallback=${x.use_classical_fallback}) — ${x.trade_off_summary}`
  );
}

return [{
  json: {
    plan_id,
    executed_subgoal,
    next_recommended_subgoal,
    key_findings,
    results: parsed,
  },
}];
'''
nodes.append(code_node("Final Output", "final-output", final_code, 12, 1))

# ---- Connections ----------------------------------------------------------

connections = {}


def connect(src, dst, src_index=0, dst_index=0):
    if src not in connections:
        connections[src] = {"main": []}
    while len(connections[src]["main"]) <= src_index:
        connections[src]["main"].append([])
    connections[src]["main"][src_index].append(
        {"node": dst, "type": "main", "index": dst_index}
    )


# Trigger -> Goal -> file reads
connect("Manual Trigger", "Set Goal")
for label, _, _ in csv_paths:
    connect("Set Goal", f"Read {label} File")
    connect(f"Read {label} File", f"Read {label} JSON")
    connect(f"Read {label} JSON", "Build Context Summary")

# Plan
connect("Build Context Summary", "Master Planner Agent")
connect("Master Planner Agent", "Parse Plan")
connect("Parse Plan", "Route on Subgoal")

# Switch outputs (index order = rule order: forecast, inventory, supplier, logistics)
connect("Route on Subgoal", "Demand Forecast Engine", src_index=0)
connect("Route on Subgoal", "Inventory EOQ Planner", src_index=1)
connect("Route on Subgoal", "Supplier Performance Monitor", src_index=2)
connect("Route on Subgoal", "Build Shipping Requests", src_index=3)

# Branch tails -> Final Merge
connect("Demand Forecast Engine", "Forecast Context Adjuster")
connect("Forecast Context Adjuster", "Merge Subgoal Results")
connect("Inventory EOQ Planner", "Inventory Exception Handler")
connect("Inventory Exception Handler", "Merge Subgoal Results")
connect("Supplier Performance Monitor", "Merge Subgoal Results")
connect("Build Shipping Requests", "Logistics Coordinator (LLM)")
connect("Logistics Coordinator (LLM)", "Use Classical Fallback?")
connect("Use Classical Fallback?", "Classical Logistics Fallback", src_index=0)  # true
connect("Use Classical Fallback?", "Merge Subgoal Results", src_index=1)         # false
connect("Classical Logistics Fallback", "Merge Subgoal Results")

connect("Merge Subgoal Results", "Final Output")

# ---- Workflow envelope ----------------------------------------------------

workflow = {
    "id": "supply-chain-manager-v1",
    "name": "Supply Chain Manager (Starter)",
    "nodes": nodes,
    "connections": connections,
    "settings": {"executionOrder": "v0"},
    "pinData": {},
}

OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps(workflow, indent=2))
print(f"wrote {OUT}")
print(f"  nodes: {len(nodes)}")
print(f"  connection groups: {sum(len(v['main']) for v in connections.values())}")
