/**
 * Inventory Optimization Planner — Economic Order Quantity (EOQ).
 *
 * Pedagogical purpose
 * -------------------
 * EOQ (Wilson, 1934) is the canonical *classical* operations-research model:
 * a closed-form solution under strict assumptions —
 *   - constant demand
 *   - instantaneous replenishment
 *   - no stockouts allowed
 *   - fixed ordering cost, fixed holding cost
 *   - no quantity discounts
 *
 * Real e-commerce violates ALL of these for at least some SKUs. Viral
 * products (looking at you, SKU-007) blow past "constant demand." Discontinued
 * SKUs (SKU-013) make "no stockouts" expensive in the wrong direction.
 * Slow-movers make the formula numerically unstable.
 *
 * This node teaches the boundary problem: classical algorithms give crisp
 * answers exactly when their assumptions hold. The two core functions are:
 *   (a) implement the formula
 *   (b) detect when its assumptions don't hold for a given SKU
 *
 * The downstream LLM exception node handles the SKUs you flag.
 *
 * Reading: docs/planning-primer.md §"STRIPS-style assumptions and where they break"
 */

// Inputs are merged upstream from two reads — inventory and sales — so this
// node sees a heterogeneous list. Partition them by which fields are present.
// NOTE: When routed via Switch in the full workflow, `$input` contains the
// planner's output item, so we fall back to reading the upstream CSV nodes.
const rawInput = $input.all().map(i => i.json);
const all = (rawInput.length > 0 && ("on_hand" in rawInput[0] || "units_sold" in rawInput[0]))
  ? rawInput
  : [
      ...$('Read Inventory JSON').all().map(i => i.json),
      ...$('Read Sales JSON').all().map(i => i.json),
    ];

// Default ordering cost (S in the EOQ formula). In production this would
// vary by supplier and channel; we hard-code a single value so the focus
// stays on the formula and its assumptions.
const ORDERING_COST_USD = 50;

// ---- Helpers (provided) ---------------------------------------------------

function partition(rows) {
  const inventory = rows.filter(r => "on_hand" in r);
  const sales = rows.filter(r => "units_sold" in r);
  return { inventory, sales };
}

function annualDemand(salesForSku) {
  // Sum units_sold across the rows we have. With 12 months of data this is
  // the trailing-12-months annual demand.
  return salesForSku.reduce((acc, r) => acc + Number(r.units_sold), 0);
}

// ---- EOQ-1 — the EOQ formula ----------------------------------------------

/**
 * Wilson's Economic Order Quantity:
 *
 *   Q* = sqrt( (2 * D * S) / H )
 *
 * @param {number} D  Annual demand        (units per year)
 * @param {number} S  Fixed cost per order (dollars per order)
 * @param {number} H  Holding cost         (dollars per unit per year)
 * @returns {number}  Optimal order quantity, rounded to nearest integer.
 *
 * Hint: Math.sqrt is your friend. Guard against D=0 and H=0 — return 0 in
 *       either case. (You can't optimally order anything for zero demand,
 *       and dividing by zero holding cost is meaningless.)
 */
function eoq(D, S, H) {
  // EOQ-1 [medium] — LO-2: classical optimization
  // Guard against zero or negative demand (D) or holding cost (H).
  if (!D || D <= 0 || !H || H <= 0) return 0;

  return Math.round(Math.sqrt((2 * D * S) / H));
}

// ---- EOQ-2 — assumption-violation detection -------------------------------

/**
 * EOQ assumes:
 *   (a) demand is roughly constant
 *   (b) lead time is short enough to re-order before stocking out
 *   (c) the SKU isn't perishable (no holding-cost cliff)
 *   (d) demand volume is high enough that an "optimal batch" is meaningful
 *
 * Return an array of human-readable flag strings for any assumption that
 * FAILS for this SKU. An empty array means "EOQ is trustworthy here, ship
 * the formula's recommendation."
 *
 * Suggested checks (refine as you wish):
 *   - "viral_spike"   : mean(last 3 months) > 2.5 × mean(prior 9 months)
 *   - "declining"     : mean(last 3 months) < 0.5 × mean(first 3 months)
 *   - "low_velocity"  : annualDemand < 60   (less than ~5/month average)
 *   - "long_lead_time": inv.lead_time_days > 28 AND demand is volatile
 *
 * Note on perishability: none of our demo SKUs are perishable, so you do
 * NOT need a perishability check. Document the omission in your analysis
 * write-up — a real implementation would need it.
 *
 * @param {object} inv         Inventory row for the SKU
 * @param {Array}  salesSeries Chronologically sorted sales rows for the SKU
 * @returns {Array<string>}    Flag names, e.g. ["viral_spike", "low_velocity"]
 *
 * Hint: implement each check as its own small block. A flagged SKU may
 *       trigger multiple flags — that's expected and useful downstream.
 */
function detectViolations(inv, salesSeries) {
  // EOQ-2 [hard] — LO-4: knowing when classical models fail
  const flags = [];
  if (!salesSeries || salesSeries.length === 0) return ["low_velocity"];

  const units = salesSeries.map(r => Number(r.units_sold));
  const mean = arr => (arr.length > 0 ? arr.reduce((a, b) => a + b, 0) / arr.length : 0);

  const last3Mean = mean(units.slice(-3));
  const prior9Mean = mean(units.slice(0, -3));
  const first3Mean = mean(units.slice(0, 3));
  const overallMean = mean(units);
  const totalAnnual = units.reduce((a, b) => a + b, 0);

  // 1. "viral_spike": recent 3-month average exceeds 2.5x prior 9-month average.
  if (prior9Mean > 0 && last3Mean > 2.5 * prior9Mean) {
    flags.push("viral_spike");
  }

  // 2. "declining": recent 3-month average dropped below 0.5x first 3-month average.
  if (first3Mean > 0 && last3Mean < 0.5 * first3Mean) {
    flags.push("declining");
  }

  // 3. "low_velocity": annual demand below 60 units (< 5 units/month average).
  if (totalAnnual < 60) {
    flags.push("low_velocity");
  }

  // 4. "long_lead_time": lead_time_days > 28 AND volatile demand (CV > 0.5).
  const variance = units.reduce((acc, u) => acc + Math.pow(u - overallMean, 2), 0) / units.length;
  const cv = overallMean > 0 ? Math.sqrt(variance) / overallMean : 0;
  if (Number(inv.lead_time_days) > 28 && cv > 0.5) {
    flags.push("long_lead_time");
  }

  return flags;
}

// ---- Main loop (provided) -------------------------------------------------

const { inventory, sales } = partition(all);
const salesBySku = sales.reduce((acc, r) => {
  (acc[r.sku] = acc[r.sku] || []).push(r);
  return acc;
}, {});
for (const sku of Object.keys(salesBySku)) {
  salesBySku[sku].sort((a, b) => a.month.localeCompare(b.month));
}

const results = [];
for (const inv of inventory) {
  const series = salesBySku[inv.sku] || [];
  const D = annualDemand(series);
  const H = Number(inv.holding_cost_per_unit_year);
  const Q = eoq(D, ORDERING_COST_USD, H);
  const flags = detectViolations(inv, series);

  results.push({
    sku: inv.sku,
    name: inv.name,
    on_hand: Number(inv.on_hand),
    reorder_point: Number(inv.reorder_point),
    annual_demand: D,
    eoq_units: Q,
    holding_cost_per_unit_year: H,
    ordering_cost_usd: ORDERING_COST_USD,
    assumption_flags: flags,
    needs_llm_review: flags.length > 0,
  });
}

return results.map(r => ({ json: r }));
