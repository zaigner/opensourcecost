// Browser port of src/oss_cost/pricing.py (price_portfolio + summarize).
// Kept formula-for-formula identical; tests/test_calculator.py checks it against Python.
(function (root) {
  "use strict";
  const TIERS = ["critical", "important", "standard"];
  const MODULES = [
    "curation_and_compliance",
    "vulnerability_response",
    "maintainer_of_last_resort",
    "bank_backports_and_support",
    "service_fixed",
    "upstream_funding",
  ];

  // data: columnar arrays {name, dl, rel, msr, nat, cop, ina, a12, a36}
  // opt: {topN, retireDormant (0..1), ticketMult, lastResortMult, allocation}
  // allocation (optional): {fixedPerLabour, inhousePerSolo} taken from a reference program,
  // so a subset (e.g. one manifest) carries fixed costs at the program's rate instead of all of them.
  function price(data, a, opt) {
    opt = opt || {};
    const n = Math.min(opt.topN || data.name.length, data.name.length);
    const retire = opt.retireDormant || 0;
    const tMult = opt.ticketMult == null ? 1 : opt.ticketMult;
    const lrMult = opt.lastResortMult == null ? 1 : opt.lastResortMult;
    const c = a.classification, sh = a.shared_hours, dh = a.dedicated_hours;
    const adv = a.advisories, uf = a.upstream_funding, eng = a.engagement;
    const rate = a.rates.vendor_hourly_cost, bankRate = a.rates.bank_hourly_cost;
    const f = a.fixed;
    const fixedFte = f.customer_success_fte + f.security_on_call_fte + f.compliance_audit_fte;
    const vendorFixed = fixedFte * a.rates.hours_per_fte * rate + f.platform_infra + f.onboarding_amortized;
    const inhouseFixed = a.inhouse.fixed_fte * a.rates.hours_per_fte * bankRate + a.inhouse.platform_infra;

    const pk = new Array(n);
    let totLabour = 0, totSolo = 0, totVendorHours = 0;
    for (let i = 0; i < n; i++) {
      const dl = data.dl[i];
      const tier = dl >= c.critical_min_downloads ? "critical"
        : dl >= c.important_min_downloads ? "important" : "standard";
      const msr = data.msr[i];
      const dormant = data.ina[i] === 1 || (msr !== null && msr >= c.dormant_months);
      const native = data.nat[i] === 1;
      const observed = adv.weight_last_12mo * data.a12[i] + (1 - adv.weight_last_12mo) * data.a36[i] / 3;
      const E = (adv.credibility * observed + (1 - adv.credibility) * adv.prior[tier]) * adv.trend_multiplier;
      const releases = Math.min(data.rel[i], sh.max_billable_releases);
      const intake = native ? sh.intake_per_release_native : sh.intake_per_release_pure;

      const hCuration = sh.monitoring + releases * intake + sh.compliance + (data.cop[i] === 1 ? sh.copyleft_review : 0);
      const hAdvisory = E * (sh.advisory_response + (dormant ? sh.advisory_own_fix : 0));
      const hLastResort = dormant ? sh.last_resort[tier] * lrMult : 0;
      const shared = hCuration + hAdvisory + hLastResort;
      const lines = dh.version_lines[tier];
      const dedicated = Math.min(E, dh.max_backport_events) * Math.max(0, lines - 1) * dh.backport_per_line
        * (native ? dh.native_backport_multiplier : 1)
        + dh.tickets_per_year[tier] * tMult * dh.hours_per_ticket + dh.evidence;

      const adoption = a.adoption.share[tier] * (dormant ? a.adoption.dormant_multiplier : 1);
      const others = Math.max(0, eng.vendor_clients * adoption - 1);
      const share = eng.bank_weight / (eng.bank_weight + others);
      const labour = (shared * share + dedicated) * rate;
      const upstream = uf.enabled && !dormant
        ? uf.commercial_share * uf.asc[tier] * uf.bank_size_weight / uf.paying_pool : 0;
      const w = dormant ? 1 - retire : 1;

      pk[i] = {
        i, tier, dormant, native, E, share, w, labour, upstream,
        solo: shared + dedicated,
        mod: {
          curation_and_compliance: hCuration * share * rate,
          vulnerability_response: hAdvisory * share * rate,
          maintainer_of_last_resort: hLastResort * share * rate,
          bank_backports_and_support: dedicated * rate,
        },
      };
      totLabour += w * labour;
      totSolo += w * (shared + dedicated);
      totVendorHours += w * (shared * share + dedicated);
    }

    const alloc = opt.allocation || {
      fixedPerLabour: totLabour ? vendorFixed / totLabour : 0,
      inhousePerSolo: totSolo ? inhouseFixed / totSolo : 0,
    };
    const margin = a.pricing.target_margin;
    const blank = () => ({ packages: 0, floor: 0, fair: 0, ceiling: 0 });
    const total = blank(), dormantAgg = blank(), byTier = {};
    TIERS.forEach((t) => (byTier[t] = blank()));
    const modules = {};
    MODULES.forEach((m) => (modules[m] = 0));

    for (const p of pk) {
      const fixed = alloc.fixedPerLabour * p.labour;
      const markup = (1 + a.pricing.risk_load[p.tier]) / (1 - margin);
      p.floor = p.labour + fixed + p.upstream;
      p.fair = (p.labour + fixed) * markup + p.upstream;
      p.ceiling = p.solo * bankRate + alloc.inhousePerSolo * p.solo + p.upstream;
      for (const m of MODULES.slice(0, 4)) modules[m] += p.w * p.mod[m] * markup;
      modules.service_fixed += p.w * fixed * markup;
      modules.upstream_funding += p.w * p.upstream;
      for (const g of [total, byTier[p.tier], p.dormant ? dormantAgg : null]) {
        if (!g) continue;
        g.packages += p.w; g.floor += p.w * p.floor; g.fair += p.w * p.fair; g.ceiling += p.w * p.ceiling;
      }
    }
    const fixedShare = vendorFixed ? (alloc.fixedPerLabour * totLabour) / vendorFixed : 0;
    return {
      packages: pk, total, byTier, dormant: dormantAgg, modules,
      vendorFte: totVendorHours / a.rates.hours_per_fte + fixedFte * fixedShare,
      allocation: {
        fixedPerLabour: totLabour ? vendorFixed / totLabour : 0,
        inhousePerSolo: totSolo ? inhouseFixed / totSolo : 0,
      },
    };
  }

  const api = { price, TIERS, MODULES };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PricingModel = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
