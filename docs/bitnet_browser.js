// Q16.16 BitNet b1.58 forward pass — vanilla JavaScript port.
//
// Bit-identical with engine.bitnet_classifier.classify(). The same pair
// of drug names will produce the same severity_name + repro_hash +
// logits_q16 in this browser as in the server-side Python forward pass.
//
// No external dependencies. No JS frameworks. Loads
// engine/bitnet_weights.json via fetch(); computes everything else
// inline using BigInt for the BLAKE2b 64-bit operations and Number for
// the Q16.16 integer arithmetic (which fits comfortably inside the
// 2^53 safe-integer range).
//
// Surface (window.HistoriaClinicaBitNet):
//   loadWeights(url='engine/bitnet_weights.json')  -> Promise<weights>
//   classify(drugA, drugB, weights)                -> Promise<{
//     severity, severity_name, logits_q16, feature_hash, repro_hash,
//     weights_id, deterministic_table_match
//   }>
//
// Apache-2.0 — STARGA, Inc.

(function () {
  "use strict";

  // ─── BLAKE2b ────────────────────────────────────────────────────────────
  // Reference: RFC 7693. Compact implementation using BigInt for 64-bit
  // word arithmetic. Output is truncated to digest_size bytes.

  const BLAKE2B_IV = [
    0x6a09e667f3bcc908n, 0xbb67ae8584caa73bn,
    0x3c6ef372fe94f82bn, 0xa54ff53a5f1d36f1n,
    0x510e527fade682d1n, 0x9b05688c2b3e6c1fn,
    0x1f83d9abfb41bd6bn, 0x5be0cd19137e2179n,
  ];

  const BLAKE2B_SIGMA = [
    [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15],
    [14,10,4,8,9,15,13,6,1,12,0,2,11,7,5,3],
    [11,8,12,0,5,2,15,13,10,14,3,6,7,1,9,4],
    [7,9,3,1,13,12,11,14,2,6,5,10,4,0,15,8],
    [9,0,5,7,2,4,10,15,14,1,11,12,6,8,3,13],
    [2,12,6,10,0,11,8,3,4,13,7,5,15,14,1,9],
    [12,5,1,15,14,13,4,10,0,7,6,3,9,2,8,11],
    [13,11,7,14,12,1,3,9,5,0,15,4,8,6,2,10],
    [6,15,14,9,11,3,0,8,12,2,13,7,1,4,10,5],
    [10,2,8,4,7,6,1,5,15,11,9,14,3,12,13,0],
    [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15],
    [14,10,4,8,9,15,13,6,1,12,0,2,11,7,5,3],
  ];

  const M64 = 0xffffffffffffffffn;

  function rot64(x, n) {
    n = BigInt(n);
    return (((x >> n) | (x << (64n - n))) & M64);
  }

  function G(v, a, b, c, d, x, y) {
    v[a] = (v[a] + v[b] + x) & M64;
    v[d] = rot64(v[d] ^ v[a], 32);
    v[c] = (v[c] + v[d]) & M64;
    v[b] = rot64(v[b] ^ v[c], 24);
    v[a] = (v[a] + v[b] + y) & M64;
    v[d] = rot64(v[d] ^ v[a], 16);
    v[c] = (v[c] + v[d]) & M64;
    v[b] = rot64(v[b] ^ v[c], 63);
  }

  function blake2bCompress(h, m, t, last) {
    const v = new Array(16);
    for (let i = 0; i < 8; i++) v[i] = h[i];
    for (let i = 0; i < 8; i++) v[i + 8] = BLAKE2B_IV[i];
    v[12] = (v[12] ^ (t & M64)) & M64;
    v[13] = (v[13] ^ ((t >> 64n) & M64)) & M64;
    if (last) v[14] = (v[14] ^ M64) & M64;
    for (let i = 0; i < 12; i++) {
      const s = BLAKE2B_SIGMA[i];
      G(v, 0, 4, 8,  12, m[s[0]],  m[s[1]]);
      G(v, 1, 5, 9,  13, m[s[2]],  m[s[3]]);
      G(v, 2, 6, 10, 14, m[s[4]],  m[s[5]]);
      G(v, 3, 7, 11, 15, m[s[6]],  m[s[7]]);
      G(v, 0, 5, 10, 15, m[s[8]],  m[s[9]]);
      G(v, 1, 6, 11, 12, m[s[10]], m[s[11]]);
      G(v, 2, 7, 8,  13, m[s[12]], m[s[13]]);
      G(v, 3, 4, 9,  14, m[s[14]], m[s[15]]);
    }
    for (let i = 0; i < 8; i++) h[i] = (h[i] ^ v[i] ^ v[i + 8]) & M64;
  }

  function le64(buf, offset) {
    let x = 0n;
    for (let i = 7; i >= 0; i--) {
      x = (x << 8n) | BigInt(buf[offset + i]);
    }
    return x;
  }

  function blake2b(input, digestSize) {
    if (digestSize <= 0 || digestSize > 64) {
      throw new Error("digestSize must be 1..64");
    }
    const h = BLAKE2B_IV.slice();
    h[0] = (h[0] ^ 0x01010000n ^ BigInt(digestSize)) & M64;

    // Pad input to a multiple of 128 bytes
    const inputLen = input.length;
    const padded = new Uint8Array(Math.max(128, Math.ceil(inputLen / 128) * 128));
    padded.set(input);

    const numBlocks = Math.ceil(inputLen / 128) || 1;
    let processed = 0n;

    for (let i = 0; i < numBlocks - 1; i++) {
      processed += 128n;
      const m = new Array(16);
      for (let j = 0; j < 16; j++) m[j] = le64(padded, i * 128 + j * 8);
      blake2bCompress(h, m, processed, false);
    }
    // Final block — process the remainder; t = total input length.
    processed = BigInt(inputLen);
    const m = new Array(16);
    for (let j = 0; j < 16; j++) m[j] = le64(padded, (numBlocks - 1) * 128 + j * 8);
    blake2bCompress(h, m, processed, true);

    // Output the first digestSize bytes (little-endian per word).
    const out = new Uint8Array(digestSize);
    for (let i = 0; i < digestSize; i++) {
      const word = h[Math.floor(i / 8)];
      out[i] = Number((word >> (BigInt(i % 8) * 8n)) & 0xffn);
    }
    return out;
  }

  // ─── Drug-pair feature encoding (must match Python engine pipeline) ────

  const TRIT_LOOKUP = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, -1, -1, -1, -1];

  const NITRATE_NAMES = new Set([
    "isosorbide mononitrate",
    "isosorbide dinitrate",
    "nitroglycerin",
  ]);

  function canonicalName(name) {
    return name.trim().toLowerCase().split(/\s+/).join(" ");
  }

  // 64-trit hash encoding — bit-identical with Python
  // engine.bitnet_classifier._encode_drug_token + the v8 trainer's
  // _hash_trits().
  function encodeDrugToken(rxcuiOrName) {
    const bytes = new TextEncoder().encode(canonicalName(rxcuiOrName));
    const digest = blake2b(bytes, 16);
    const out = [];
    for (const byte of digest) {
      out.push(TRIT_LOOKUP[(byte >> 0) & 0xf]);
      out.push(TRIT_LOOKUP[(byte >> 4) & 0xf]);
      out.push(TRIT_LOOKUP[byte & 0xf]);
      out.push(TRIT_LOOKUP[(byte >> 2) & 0xf]);
    }
    return out.slice(0, 64);
  }

  // ─── V8 ATC pharmacology flag bits + pair-derived DDI rule bits ────────
  // Bit-identical with engine.bitnet_features_v8 (Python). Loaded from
  // docs/pharmacology_flags.json on first call; cached for the life of
  // the page.

  let _PHARM_FLAGS = null;

  async function loadPharmFlags(url) {
    if (_PHARM_FLAGS !== null) return _PHARM_FLAGS;
    // Default URL is the demo-deploy-relative path (Cloudflare Pages serves
    // docs/ as root, so docs/pharmacology_flags.json is reachable as
    // pharmacology_flags.json from /demo.html). Pass an explicit absolute
    // URL when calling from outside the demo.
    const u = url || "pharmacology_flags.json";
    const resp = await fetch(u, { cache: "no-cache" });
    if (!resp.ok) throw new Error(`fetch ${u}: ${resp.status}`);
    const ct = resp.headers.get("content-type") || "";
    if (!ct.includes("json")) {
      // Defensive — Cloudflare Pages serves a 200 HTML fallback on missing
      // JSON paths; without this guard JSON.parse() fails with the cryptic
      // "Unexpected token '<', '<!DOCTYPE '... is not valid JSON".
      throw new Error(`fetch ${u}: expected JSON, got ${ct}`);
    }
    _PHARM_FLAGS = await resp.json();
    return _PHARM_FLAGS;
  }

  // 26 ATC pharmacology flag bits {0,1} per drug — matches the v8
  // trainer's `_flag_bits()` and engine's `flag_bits()`.
  function flagBits(name, flagsDoc) {
    const canonical = canonicalName(name);
    const entry = (flagsDoc.drugs && flagsDoc.drugs[canonical]) || { flags: [] };
    const setFlags = new Set(entry.flags || []);
    return flagsDoc.flag_keys.map(k => setFlags.has(k) ? 1 : 0);
  }

  // 13 pair-derived DDI-rule bits {0,1} — matches Python
  // engine.bitnet_features_v8.pair_derived_flags().
  function pairDerivedFlags(da, db, flagsDoc) {
    const aNorm = canonicalName(da);
    const bNorm = canonicalName(db);
    const aFlags = (flagsDoc.drugs && flagsDoc.drugs[aNorm] && flagsDoc.drugs[aNorm].flags) || [];
    const bFlags = (flagsDoc.drugs && flagsDoc.drugs[bNorm] && flagsDoc.drugs[bNorm].flags) || [];
    const fa = new Set(aFlags);
    const fb = new Set(bFlags);

    function hasPair(x, y) {
      return (fa.has(x) && fb.has(y)) || (fa.has(y) && fb.has(x));
    }
    function bothHave(x) {
      return fa.has(x) && fb.has(x);
    }

    const pde5Nitrate =
      (fa.has("is_pde5_inhibitor") && NITRATE_NAMES.has(bNorm)) ||
      (fb.has("is_pde5_inhibitor") && NITRATE_NAMES.has(aNorm));

    return [
      hasPair("is_cyp3a4_strong_inhibitor", "is_cyp3a4_substrate") ? 1 : 0,
      hasPair("is_oatp1b1_inhibitor", "is_statin") ? 1 : 0,
      hasPair("is_p_gp_inhibitor", "is_p_gp_substrate") ? 1 : 0,
      hasPair("is_cyp2c9_inhibitor", "is_anticoagulant") ? 1 : 0,
      hasPair("is_maoi", "is_serotonergic") ? 1 : 0,
      pde5Nitrate ? 1 : 0,
      hasPair("is_iodinated_contrast", "is_metformin") ? 1 : 0,
      hasPair("is_cyp1a2_inhibitor", "is_cyp1a2_substrate") ? 1 : 0,
      hasPair("is_xanthine_oxidase_inhibitor", "is_thiopurine") ? 1 : 0,
      bothHave("is_folate_antagonist") ? 1 : 0,
      hasPair("is_tetracycline", "is_retinoid") ? 1 : 0,
      hasPair("is_ace_inhibitor", "is_neprilysin_inhibitor") ? 1 : 0,
      hasPair("is_metformin", "is_renal_state") ? 1 : 0,
    ];
  }

  // 193-dim pair encoding: 64 hash + 26 flag for drug A, same for B,
  // + 13 pair-derived = 193. Order-canonicalised (lex sort).
  function encodePairV8(da, db, flagsDoc) {
    const [a, b] = [da, db].sort();
    return [
      ...encodeDrugToken(a),
      ...flagBits(a, flagsDoc),
      ...encodeDrugToken(b),
      ...flagBits(b, flagsDoc),
      ...pairDerivedFlags(a, b, flagsDoc),
    ];
  }

  // ─── Q16.16 arithmetic ──────────────────────────────────────────────────

  // Use 2 ** 31 (or explicit literals) — `1 << 31` overflows JS's 32-bit
  // signed bitwise to -2147483648 and breaks both Q16_MIN and Q16_MAX.
  const Q16_ONE = 65536;            // 2 ** 16
  const Q16_MIN = -2147483648;      // -(2 ** 31)
  const Q16_MAX = 2147483647;       // (2 ** 31) - 1

  function q16Clamp(v) {
    if (v > Q16_MAX) return Q16_MAX;
    if (v < Q16_MIN) return Q16_MIN;
    return v;
  }

  function q16DotTernary(activations, ternaryWeights) {
    let acc = 0;
    for (let i = 0; i < activations.length; i++) {
      const w = ternaryWeights[i];
      if (w === 1) acc += activations[i];
      else if (w === -1) acc -= activations[i];
    }
    return q16Clamp(acc);
  }

  function q16Relu(v) {
    return v > 0 ? v : 0;
  }

  // ─── Weights bundle ─────────────────────────────────────────────────────

  // iter-421 Path B: optional tier-2 specialist loader. Returns null if
  // the bundle is absent (fetch 404) so the demo gracefully falls back
  // to single-bundle (A-only) inference. Same Q16.16 ternary kernels +
  // canonical-JSON bundle_id integrity primitive as A.
  async function loadWeightsB(url) {
    if (!url) url = "engine/bitnet_weights_b_specialist.json";
    let resp;
    try {
      resp = await fetch(url, { cache: "no-cache" });
    } catch (e) {
      return null;
    }
    if (!resp.ok) return null;
    return loadWeightsFromObject(await resp.json());
  }

  // Shared loader body — used by loadWeights + loadWeightsB so both
  // bundles go through the same shape + schema validation.
  function loadWeightsFromObject(payload) {
    const meta = payload._meta || {};
    const schema = meta.schema || "bitnet_classifier_v1";
    const hiddenFeatures = payload.hidden_w.length;
    const inFeatures = payload.hidden_w[0] ? payload.hidden_w[0].length : 0;
    const outFeatures = payload.output_w.length;
    if (schema !== "bitnet_classifier_v1" && schema !== "bitnet_classifier_v3_atc_flags") {
      throw new Error(`unknown bitnet schema ${schema}`);
    }
    if (payload.hidden_b.length !== hiddenFeatures) {
      throw new Error(`hidden_b length ${payload.hidden_b.length} != hidden_features ${hiddenFeatures}`);
    }
    if (outFeatures !== 5) throw new Error(`output_w must be 5 rows, got ${outFeatures}`);
    if (payload.output_w.some(row => row.length !== hiddenFeatures)) {
      throw new Error(`output_w cols must be hidden_features ${hiddenFeatures}`);
    }
    if (payload.output_b.length !== 5) throw new Error("output_b must be 5");
    const expectedIn = schema === "bitnet_classifier_v1" ? 128 : 193;
    if (inFeatures !== expectedIn) {
      throw new Error(`schema ${schema} expects in_features=${expectedIn}, got ${inFeatures}`);
    }
    return {
      hidden_w: payload.hidden_w,
      hidden_b: payload.hidden_b,
      output_w: payload.output_w,
      output_b: payload.output_b,
      bundle_id: meta.bundle_id || "",
      schema: schema,
      in_features: inFeatures,
      hidden_features: hiddenFeatures,
      out_features: outFeatures,
    };
  }

  async function loadWeights(url) {
    if (!url) url = "engine/bitnet_weights.json";
    const resp = await fetch(url, { cache: "no-cache" });
    if (!resp.ok) throw new Error(`fetch ${url}: ${resp.status}`);
    const payload = await resp.json();

    // Iter-275 v8 promotion: dim-dynamic + schema-aware loader.
    // Mirrors engine/bitnet_classifier.py post-iter-275.
    const meta = payload._meta || {};
    const schema = meta.schema || "bitnet_classifier_v1";
    const hiddenFeatures = payload.hidden_w.length;
    const inFeatures = payload.hidden_w[0] ? payload.hidden_w[0].length : 0;
    const outFeatures = payload.output_w.length;

    if (schema !== "bitnet_classifier_v1" && schema !== "bitnet_classifier_v3_atc_flags") {
      throw new Error(`unknown bitnet schema ${schema}`);
    }
    if (payload.hidden_b.length !== hiddenFeatures) {
      throw new Error(`hidden_b length ${payload.hidden_b.length} != hidden_features ${hiddenFeatures}`);
    }
    if (outFeatures !== 5) throw new Error(`output_w must be 5 rows, got ${outFeatures}`);
    if (payload.output_w.some(row => row.length !== hiddenFeatures)) {
      throw new Error(`output_w cols must be hidden_features ${hiddenFeatures}`);
    }
    if (payload.output_b.length !== 5) throw new Error("output_b must be 5");

    const expectedIn = schema === "bitnet_classifier_v1" ? 128 : 193;
    if (inFeatures !== expectedIn) {
      throw new Error(`schema ${schema} expects in_features=${expectedIn}, got ${inFeatures}`);
    }

    return {
      hidden_w: payload.hidden_w,
      hidden_b: payload.hidden_b,
      output_w: payload.output_w,
      output_b: payload.output_b,
      bundle_id: meta.bundle_id || "",
      schema: schema,
      in_features: inFeatures,
      hidden_features: hiddenFeatures,
      out_features: outFeatures,
    };
  }

  // ─── SHA-256 helpers (canonical-JSON hash; matches Python json.dumps
  // sort_keys=True, separators=(",", ":")) ────────────────────────────────

  async function sha256Hex(text) {
    const buf = new TextEncoder().encode(text);
    const hash = await crypto.subtle.digest("SHA-256", buf);
    const bytes = new Uint8Array(hash);
    let out = "";
    for (const b of bytes) out += b.toString(16).padStart(2, "0");
    return out;
  }

  // Canonical JSON matches Python's json.dumps(sort_keys=True,
  // separators=(",",":")) for the values we emit (ints, strings, lists
  // of ints, sorted-key dicts).
  function canonicalJson(value) {
    if (value === null) return "null";
    if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(value);
    if (typeof value === "string") return JSON.stringify(value);
    if (typeof value === "boolean") return value ? "true" : "false";
    if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]";
    const keys = Object.keys(value).sort();
    return "{" + keys.map(k => JSON.stringify(k) + ":" + canonicalJson(value[k])).join(",") + "}";
  }

  // ─── Forward pass ───────────────────────────────────────────────────────

  // Iter-275 v8 promotion: vocab aligned with the corpus / cache /
  // engine `_SEVERITY_NAMES`. Pre-v8 used (none, minor, moderate, major,
  // contraindicated) — the engine's first-era vocab.
  const SEVERITY_NAMES_V1 = ["none", "minor", "moderate", "major", "contraindicated"];
  const SEVERITY_NAMES_V8 = ["none", "moderate", "serious", "major", "contraindicated"];

  // iter-421 Path B helper: forward pass through B with constrained
  // argmax over classes {1, 2, 3} = {moderate, serious, major}.
  // Mirrors engine/bitnet_classifier.py::_classify_constrained_b.
  async function _classifyConstrainedB(a, b, weightsB) {
    let pair;
    if (weightsB.schema === "bitnet_classifier_v3_atc_flags") {
      const flagsDoc = await loadPharmFlags();
      pair = encodePairV8(a, b, flagsDoc);
    } else {
      pair = encodeDrugToken(a).concat(encodeDrugToken(b));
    }
    const activations = pair.map(v => v * Q16_ONE);
    const H = weightsB.hidden_features;
    const hiddenPre = new Array(H);
    for (let j = 0; j < H; j++) {
      hiddenPre[j] = q16Clamp(q16DotTernary(activations, weightsB.hidden_w[j]) + weightsB.hidden_b[j]);
    }
    const hidden = hiddenPre.map(q16Relu);
    const logits = new Array(5);
    for (let k = 0; k < 5; k++) {
      logits[k] = q16Clamp(q16DotTernary(hidden, weightsB.output_w[k]) + weightsB.output_b[k]);
    }
    // Constrained argmax over {1, 2, 3}; ties broken by lower index.
    let severity = 1;
    let best = logits[1];
    if (logits[2] > best) { best = logits[2]; severity = 2; }
    if (logits[3] > best) { best = logits[3]; severity = 3; }
    return { severity, logits };
  }

  async function classify(drugA, drugB, weights, weightsB) {
    // Lex-sort the pair.
    const [a, b] = [drugA, drugB].sort();

    // Iter-275: dispatch on schema. v3_atc_flags requires the live
    // pharmacology_flags.json; v1 uses hash-only encoding.
    let pair;
    if (weights.schema === "bitnet_classifier_v3_atc_flags") {
      const flagsDoc = await loadPharmFlags();
      pair = encodePairV8(a, b, flagsDoc);
    } else {
      pair = encodeDrugToken(a).concat(encodeDrugToken(b));
    }
    if (pair.length !== weights.in_features) {
      throw new Error(`pair features length ${pair.length} != in_features ${weights.in_features}`);
    }

    // feature_hash = SHA-256 over bytes((v + 1) for v in pair)
    const featureBytes = new Uint8Array(pair.map(v => v + 1));
    const featureHashBuf = await crypto.subtle.digest("SHA-256", featureBytes);
    const featureHash = Array.from(new Uint8Array(featureHashBuf))
      .map(b => b.toString(16).padStart(2, "0")).join("");

    // Scale ternary → Q16.16
    const activations = pair.map(v => v * Q16_ONE);

    // First linear: in_features → hidden_features, plus bias, then ReLU
    const H = weights.hidden_features;
    const hiddenPre = new Array(H);
    for (let j = 0; j < H; j++) {
      hiddenPre[j] = q16Clamp(q16DotTernary(activations, weights.hidden_w[j]) + weights.hidden_b[j]);
    }
    const hidden = hiddenPre.map(q16Relu);

    // Second linear: hidden_features → 5
    const logits = new Array(5);
    for (let k = 0; k < 5; k++) {
      logits[k] = q16Clamp(q16DotTernary(hidden, weights.output_w[k]) + weights.output_b[k]);
    }

    // Argmax — ties broken by lower index.
    let severity = 0;
    let best = logits[0];
    for (let k = 1; k < 5; k++) {
      if (logits[k] > best) { best = logits[k]; severity = k; }
    }

    // iter-421 Path B cascade: when a tier-2 specialist bundle is
    // supplied, A's contraindicated verdict (severity==4) ALWAYS wins
    // (compuerta de contraindicación de grado clínico (congelada)). For all other A predictions, B's
    // constrained argmax over {1,2,3} replaces A's serious-class call.
    // Mirrors engine/bitnet_classifier.py::classify post-iter-421.
    let weightsIdForAudit = weights.bundle_id;
    let logitsB = null;
    let bundleIdB = null;
    if (weightsB && severity !== 4) {
      const r = await _classifyConstrainedB(a, b, weightsB);
      severity = r.severity;
      logitsB = r.logits;
      bundleIdB = weightsB.bundle_id;
      weightsIdForAudit = `${weights.bundle_id}+${weightsB.bundle_id}`;
    }

    // repro_hash = SHA-256 over canonical JSON. iter-421 ensemble
    // appends bundle_id_b + logits_q16_b to the payload so verifiers
    // with both bundles can replay the cascade decision exactly.
    const reproPayload = {
      feature_hash: featureHash,
      logits_q16: logits,
      severity: severity,
      weights_id: weightsIdForAudit,
    };
    if (logitsB !== null) {
      reproPayload.logits_q16_b = logitsB;
      reproPayload.bundle_id_b = bundleIdB;
    }
    const reproHash = await sha256Hex(canonicalJson(reproPayload));

    const vocab = weights.schema === "bitnet_classifier_v3_atc_flags"
      ? SEVERITY_NAMES_V8
      : SEVERITY_NAMES_V1;

    return {
      drug_a: a,
      drug_b: b,
      severity: severity,
      severity_name: vocab[severity],
      logits_q16: logits,
      feature_hash: featureHash,
      repro_hash: reproHash,
      weights_id: weightsIdForAudit,
    };
  }

  // ─── Self-test (for the iter-69 pin: warfarin + ibuprofen must produce
  // the same repro_hash the Python forward pass produces). The expected
  // hash is captured in tests/test_engine/test_browser_bitnet_pin.py and
  // checked at integration time. ──────────────────────────────────────────

  async function selfTest() {
    const w = await loadWeights();
    const r = await classify("warfarin", "ibuprofen", w);
    return r;
  }

  // ─── Public API ─────────────────────────────────────────────────────────

  window.HistoriaClinicaBitNet = {
    loadWeights,
    loadWeightsB,
    loadPharmFlags,
    classify,
    encodeDrugToken,
    encodePairV8,
    flagBits,
    pairDerivedFlags,
    blake2b,
    selfTest,
    _internals: { canonicalJson, q16DotTernary, q16Clamp, Q16_ONE },
  };

})();
