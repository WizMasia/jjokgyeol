"use strict";

// Keep the editable spelling in each unit. Only the metric draft enters a Style request.
function indentEditor(units, keys) {
  const unit = byId("indent-unit");
  unit.add(new Option("mm", "mm"));
  if (units) unit.add(new Option("pt", "pt"));
  const drafts = Object.fromEntries(keys.map(key => [key, {}]));

  function decimalText(coefficient, power) {
    if (coefficient === 0n) return "0";
    const sign = coefficient < 0n ? "-" : "";
    const digits = String(coefficient).replace("-", "");
    const point = BigInt(digits.length) + power;
    const significant = digits.replace(/0+$/, "");
    // Keep extreme exponents compact, including invalid drafts sent for validation.
    if (point < -6n || point > 12n) return sign + significant + "e" + (point - BigInt(significant.length));
    const i = Number(point);
    if (i <= 0) return sign + "0." + "0".repeat(-i) + significant;
    if (i >= significant.length) return sign + significant.padEnd(i, "0");
    return sign + significant.slice(0, i) + "." + significant.slice(i);
  }

  function nearest(numerator, denominator) {
    return (2n * numerator + denominator) / (2n * denominator);
  }

  function convert(raw, from, forDisplay = false) {
    const key = decimalKey(raw);
    if (key === null || key === "0") return key ?? raw;
    const [digits, exponent] = key.split("e");
    const coefficient = BigInt(digits);
    const power = BigInt(exponent);
    const sp = BigInt(units.sp_per_pt);
    const divisor = BigInt(units.sp_per_mm_denominator);
    const metric = BigInt(units.sp_per_mm_numerator);
    const points = sp * divisor;
    const [source, target] = from === "mm" ? [metric, points] : [points, metric];
    const sign = coefficient < 0n ? -1n : 1n;
    const magnitude = coefficient * sign;
    const point = BigInt(String(magnitude).length) + power;

    if (forDisplay && point >= -6n && point <= 10n) {
      const length = magnitude * source * (power > 0n ? 10n ** power : 1n);
      const scale = power < 0n ? 10n ** -power : 1n;
      const stored = nearest(length, scale * divisor);
      // For fine metric values try a simple pt spelling, otherwise expose the exact sp.
      const displayPlaces = from === "mm" && power < -4n ? 4n : 6n;
      const places = 10n ** displayPlaces;
      const short = nearest(length * places, scale * target);
      if ((short || sign > 0n) && nearest(short * target, places * divisor) === stored) {
        return decimalText(sign * short, -displayPlaces);
      }
      if (from === "mm" && (stored || sign > 0n)) {
        // A stored file's shortest mm spelling can denote any whole sp.
        const bits = BigInt(sp.toString(2).length - 1);
        return decimalText(sign * stored * 5n ** bits, -bits);
      }
    }

    // A repeating metric decimal at an exact half-sp must round away from zero.
    // Carry guard digits beyond the input's last digit, even for long near-ties.
    const scaled = magnitude * source * 10n ** 20n;
    return decimalText(sign * ((scaled + target - 1n) / target), power - 20n);
  }

  for (const key of keys) {
    const input = byId(key);
    input.addEventListener("input", () => {
      const draft = drafts[key];
      draft[unit.value] = input.value;
      if (unit.value === "pt") {
        draft.mm = convert(input.value, "pt");
        draft.metricDisplay = convert(input.value, "pt", true);
      } else {
        delete draft.metricDisplay;
        delete draft.pt;
      }
    });
  }
  function show() {
    for (const key of keys) {
      const draft = drafts[key];
      if (unit.value === "pt") {
        draft.pt ??= convert(draft.mm, "mm", true);
        byId(key).value = draft.pt;
      } else {
        byId(key).value = draft.metricDisplay ?? draft.mm;
      }
    }
  }
  unit.addEventListener("input", event => {
    event.stopPropagation();
    show();
  });

  return {
    get() { return Object.fromEntries(keys.map(key => [key, drafts[key].mm])); },
    set(values, preserveDraft) {
      for (const key of keys) {
        const mm = String(values[key]);
        if (!preserveDraft || drafts[key].mm !== mm) drafts[key] = {mm};
      }
      show();
    },
  };
}
