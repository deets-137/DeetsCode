// tileflow-engine.js — the scoring + flow runtime.
//
// One module, one job: given the current panel instances, their manifests,
// and their states, decide (a) which go to the tray, (b) which size class
// each bento panel claims, and (c) their visual order. The renderer in
// panel-shell.js applies the result by setting `style.order`, `gridColumn`,
// `gridRow`, and routing dormant-with-tray panels into the tray region.
//
// Pure-ish: flowPass takes inputs, mutates DOM (order + grid styles) on the
// elements it's given, and returns the placement decisions. No WS calls,
// no animation — the FLIP runner in panel-shell.js handles motion.
//
// See docs/tileflow.md "Scoring + flow" for the spec.

(function () {
  // ── Tunable weights ─────────────────────────────────────────────────────
  // Every knob in one object so a future settings panel can replace it
  // wholesale via harness.tileflow.setWeights(partial). All ints; signed
  // contributions sum to a single per-instance score.
  const DEFAULT_WEIGHTS = {
    // Base by current effective size class. Higher = more visual weight,
    // wins better placement. Hero is heavy; tray icons score deeply
    // negative so they sort out of the bento naturally.
    base_by_class: {
      hero:   10,
      large:   6,
      medium:  4,
      small:   2,
      icon:  -10,
    },
    // State modifier. Focused dominates; dormant sinks below tray threshold.
    state_mod: {
      focused: 8,
      active:  3,
      idle:    0,
      dormant: -10,
    },
    // Score below this → tray (provided the panel allows `tray_when_dormant`).
    tray_threshold: -3,
    // Recency: a state change refreshes a fresh timestamp; the resulting
    // bonus decays linearly over recency_window_s back to 0.
    recency_max:      5,
    recency_window_s: 30,
    // How many score points above 0 promote a panel's size class by one
    // tier. e.g. with promotion_step=3, an `active` (+3) panel jumps one
    // tier from its natural class; `focused` (+8) jumps two.
    promotion_step: 3,
  };

  let WEIGHTS = structuredClone(DEFAULT_WEIGHTS);

  // ── Class table ─────────────────────────────────────────────────────────
  // Same span table as panel-shell.js's SIZE_CLASS_SPANS, plus an ordering
  // we can step up/down by tier. `icon` is the floor; `hero` is the ceiling.
  const CLASS_ORDER = ["icon", "small", "medium", "large", "hero"];
  const STATE_ORDER = { dormant: 0, idle: 1, active: 2, focused: 3 };
  const CLASS_SPANS = {
    icon:   null,
    small:  { cols: 3,  rows: 1 },
    medium: { cols: 6,  rows: 1 },
    large:  { cols: 6,  rows: 2 },
    hero:   { cols: 12, rows: 2 },
  };

  function stepClass(cls, delta) {
    const idx = CLASS_ORDER.indexOf(cls);
    if (idx < 0) return cls;
    const next = Math.max(0, Math.min(CLASS_ORDER.length - 1, idx + delta));
    return CLASS_ORDER[next];
  }

  // Hard row floor derived from display.min.height. The class table caps
  // rows at 2, which used to squash any panel whose min.height exceeds two
  // row tracks (e.g. settings min 280px inside 252px). A panel spanning
  // `rows` rows gets `rows*rowPx + (rows-1)*gap` of track, so:
  //   rows >= (minPx + gap) / (rowPx + gap)
  const MAX_ROWS = 4;
  function rowsCeilForMin(px, gridCfg) {
    if (!px) return 1;
    const rowPx = gridCfg.rowPx || 120;
    const gap = (typeof gridCfg.gapPx === "number") ? gridCfg.gapPx : 12;
    return Math.max(1, Math.min(MAX_ROWS, Math.ceil((px + gap) / (rowPx + gap))));
  }

  // ── Natural class — derived from manifest display.{preferred,min,max} ───
  // Bucket preferred.width into the {3,6,12}-col table by measuring against
  // the live bento column width. Falls back to `small` when preferred is
  // absent. min.width pushes the floor up; max.width caps the ceiling.
  // For dimensions: `preferred` is a soft target (panels can render
  // slightly smaller with internal scrolling), so round-to-nearest. `min`
  // is a hard floor that must always be satisfied → ceil. `max` is a hard
  // ceiling → floor. This split is what stops a panel preferring 560px in
  // a 92px-col grid (=6.11 cols) from being rounded UP to 7 cols and
  // promoted to hero when medium (6 cols = 552px) would fit fine.
  function colsRoundForPref(px, gridCfg) {
    if (!px || !gridCfg.colPx) return 0;
    return Math.max(1, Math.round(px / gridCfg.colPx));
  }
  function colsCeilForMin(px, gridCfg) {
    if (!px || !gridCfg.colPx) return 0;
    return Math.max(1, Math.ceil(px / gridCfg.colPx));
  }
  function colsFloorForMax(px, gridCfg) {
    if (!px || !gridCfg.colPx) return 12;
    return Math.max(1, Math.floor(px / gridCfg.colPx));
  }

  function nearestClassForCols(needCols) {
    // Round UP to the next valid class so min-width is always satisfied.
    if (needCols <= 3) return "small";
    if (needCols <= 6) return "medium";
    return "hero";
  }

  function naturalClass(manifest, gridCfg) {
    const disp = (manifest && manifest.display) || {};
    const pref = disp.preferred || {};
    const min  = disp.min       || {};
    const max  = disp.max       || {};

    const prefCols = colsRoundForPref(pref.width, gridCfg);
    const minCols  = colsCeilForMin(min.width,  gridCfg);
    const maxCols  = max.width ? colsFloorForMax(max.width, gridCfg) : 12;

    // Width: take the preferred bucket, clamped to [min, max].
    const wantCols = Math.min(Math.max(prefCols || 3, minCols || 0), maxCols || 12);
    let cls = nearestClassForCols(wantCols);

    // Height: only promote to a 2-row class if preferred.height clearly
    // wants 2 rows (>= 1.5× row height). Otherwise content slightly over
    // one row scrolls inside the cell, which is fine for most widgets.
    const rowPx = gridCfg.rowPx || 120;
    if ((pref.height || 0) >= rowPx * 1.5 && CLASS_SPANS[cls] && CLASS_SPANS[cls].rows < 2) {
      cls = stepClass(cls, 1);
    }
    return cls;
  }

  // ── Score + class derivation ────────────────────────────────────────────
  function recencyBonus(lastStateChangeAt, nowMs) {
    if (!lastStateChangeAt) return 0;
    const elapsedS = Math.max(0, (nowMs - lastStateChangeAt) / 1000);
    const window = Math.max(1, WEIGHTS.recency_window_s);
    const decay = Math.max(0, 1 - elapsedS / window);
    return Math.round(WEIGHTS.recency_max * decay);
  }

  function instanceScore(state, naturalCls, overrides, lastStateChangeAt, nowMs) {
    const w = WEIGHTS;
    let s = 0;
    s += (w.base_by_class[naturalCls] || 0);
    s += (w.state_mod[state] || 0);
    s += (overrides && typeof overrides.priority === "number" ? overrides.priority : 0);
    s += (overrides && typeof overrides.score_bonus === "number" ? overrides.score_bonus : 0);
    s += recencyBonus(lastStateChangeAt, nowMs);
    // Floor / ceiling overrides (rare; user-driven escape hatches).
    if (overrides && typeof overrides.score_floor === "number") {
      s = Math.max(s, overrides.score_floor);
    }
    if (overrides && typeof overrides.score_ceiling === "number") {
      s = Math.min(s, overrides.score_ceiling);
    }
    return s;
  }

  // Effective class = natural class promoted by state bonus / demoted by
  // dormant penalty. Each promotion_step worth of positive state contribution
  // bumps one tier. Class is then clamped by manifest min/max widths.
  function effectiveClass(state, naturalCls, manifest, gridCfg) {
    const w = WEIGHTS;
    const stateContrib = (w.state_mod[state] || 0);
    let tiers = 0;
    if (stateContrib > 0) tiers = Math.floor(stateContrib / Math.max(1, w.promotion_step));
    else if (stateContrib < 0) tiers = -Math.floor(-stateContrib / Math.max(1, w.promotion_step));
    let cls = stepClass(naturalCls, tiers);

    // Re-clamp by manifest min/max widths so promotion can't violate them.
    const disp = (manifest && manifest.display) || {};
    const min = disp.min || {};
    const max = disp.max || {};
    const minCols = colsCeilForMin(min.width, gridCfg);
    const maxCols = max.width ? colsFloorForMax(max.width, gridCfg) : 12;
    if (minCols) {
      const floorCls = nearestClassForCols(minCols);
      if (CLASS_ORDER.indexOf(cls) < CLASS_ORDER.indexOf(floorCls)) cls = floorCls;
    }
    if (maxCols < 12) {
      const ceilCls = nearestClassForCols(maxCols);
      if (CLASS_ORDER.indexOf(cls) > CLASS_ORDER.indexOf(ceilCls)) cls = ceilCls;
    }
    return cls;
  }

  // ── flowPass: compute placements for the current layout ─────────────────
  // Input: array of { instance, manifest, state, overrides, tileflow, lastStateChangeAt }
  // gridCfg: { cols, rowPx, gapPx, bentoWidthPx } — derived from the bento DOM.
  // Returns: { decisions: [{instance, bin: "bento"|"tray", cls, score, span?}],
  //            ordered: [decisions in placement order] }
  function flowPass(items, gridCfg) {
    const nowMs = Date.now();
    const decisions = items.map((it) => {
      // User floors (layout-instance `tileflow` block, Stage 3): the floor
      // wins over runtime state. A floored panel can grow above its floor
      // but never shrink below it, and never routes to the tray.
      const tf = it.tileflow || {};
      let state = it.state;
      if (tf.never_dormant && state === "dormant") state = "idle";
      if (tf.locked_floor) {
        const cur = STATE_ORDER[state], floor = STATE_ORDER[tf.locked_floor];
        if (typeof cur === "number" && typeof floor === "number" && cur < floor) state = tf.locked_floor;
      }
      const natCls = naturalClass(it.manifest, gridCfg);
      let effCls = effectiveClass(state, natCls, it.manifest, gridCfg);
      if (tf.locked_size && CLASS_ORDER.indexOf(effCls) < CLASS_ORDER.indexOf(tf.locked_size)) {
        effCls = tf.locked_size;
      }
      const score = instanceScore(state, effCls, it.overrides, it.lastStateChangeAt, nowMs);
      // Tray routing: explicit dormant + tray_when_dormant, OR score below
      // threshold AND tray_when_dormant allowed. Floored instances stay put.
      const floored = !!(tf.locked_size || tf.never_dormant ||
        (tf.locked_floor && tf.locked_floor !== "dormant"));
      const trayOK = !floored &&
        (!it.manifest || (it.manifest.tileflow && it.manifest.tileflow.tray_when_dormant !== false));
      const wantsTray =
        (state === "dormant" && trayOK) ||
        (score <= WEIGHTS.tray_threshold && trayOK);
      const bin = wantsTray ? "tray" : "bento";
      let span = bin === "bento" ? CLASS_SPANS[effCls] : null;
      if (span) {
        // min.height is a hard floor: grow the row span past the class
        // table when the class's track height can't satisfy it.
        const disp = (it.manifest && it.manifest.display) || {};
        const minRows = rowsCeilForMin((disp.min || {}).height, gridCfg);
        if (minRows > span.rows) span = { cols: span.cols, rows: minRows };
      }
      return { instance: it.instance, bin, cls: effCls, score, span, natural_class: natCls };
    });
    // Order: bento panels by score desc; tray icons by score desc (high
    // score at top of tray too). Stable sort on instance id for tiebreaks.
    const bento = decisions.filter(d => d.bin === "bento")
      .sort((a, b) => (b.score - a.score) || a.instance.localeCompare(b.instance));
    const tray = decisions.filter(d => d.bin === "tray")
      .sort((a, b) => (b.score - a.score) || a.instance.localeCompare(b.instance));
    return { decisions, ordered: [...bento, ...tray], bento, tray };
  }

  // ── Public API surface ──────────────────────────────────────────────────
  const harness = window.harness || (window.harness = {});
  const tileflow = (harness.tileflow = harness.tileflow || {});

  tileflow.WEIGHTS = WEIGHTS;
  tileflow.DEFAULT_WEIGHTS = DEFAULT_WEIGHTS;
  tileflow.setWeights = function (partial) {
    // Shallow-merge so a settings panel can dial in one knob without
    // re-sending the whole table. Nested objects (base_by_class, state_mod)
    // get deep-merged at one level.
    if (!partial || typeof partial !== "object") return WEIGHTS;
    for (const k of Object.keys(partial)) {
      const v = partial[k];
      if (v && typeof v === "object" && !Array.isArray(v) && WEIGHTS[k] && typeof WEIGHTS[k] === "object") {
        WEIGHTS[k] = { ...WEIGHTS[k], ...v };
      } else {
        WEIGHTS[k] = v;
      }
    }
    tileflow.WEIGHTS = WEIGHTS;
    // Notify the shell to re-flow; the shell installs this on boot.
    if (typeof tileflow._onWeightsChanged === "function") tileflow._onWeightsChanged();
    return WEIGHTS;
  };
  tileflow.resetWeights = function () {
    WEIGHTS = structuredClone(DEFAULT_WEIGHTS);
    tileflow.WEIGHTS = WEIGHTS;
    if (typeof tileflow._onWeightsChanged === "function") tileflow._onWeightsChanged();
    return WEIGHTS;
  };
  tileflow.CLASS_SPANS = CLASS_SPANS;
  tileflow.naturalClass = naturalClass;
  tileflow.effectiveClass = effectiveClass;
  tileflow.score = instanceScore;
  tileflow.flowPass = flowPass;

  // Console helper: print a sorted decision table for every bento+tray
  // tile. Useful when DevTools-inspecting a panel and you want to know
  // *why* it landed where it did — `harness.tileflow.dump()` in the
  // console gives you the score breakdown live.
  tileflow.dump = function () {
    const rows = [];
    for (const el of document.querySelectorAll(".panel-instance, .tray-icon")) {
      rows.push({
        instance: el.dataset.instance,
        bin: el.classList.contains("tray-icon") ? "tray" : "bento",
        state: el.dataset.tileflowState,
        cls: el.dataset.tileflowClass,
        score: Number(el.dataset.tileflowScore),
        order: Number(el.dataset.tileflowOrder),
        col: el.style.gridColumn,
        row: el.style.gridRow,
      });
    }
    rows.sort((a, b) => b.score - a.score);
    if (console.table) console.table(rows);
    return rows;
  };
})();
