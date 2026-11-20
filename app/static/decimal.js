"use strict";

function decimalKey(value) {
  const raw = String(value);
  const match = /^([+-]?)(\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d+))?$/.exec(raw);
  if (!match || match[0] !== raw) return null;
  const [whole, fraction = ""] = match[2].split(".");
  const digits = (whole + fraction).replace(/^0+/, "");
  if (!digits) return "0";
  const coefficient = digits.replace(/0+$/, "");
  const power = BigInt(match[3] ?? "0") + BigInt(digits.length - coefficient.length - fraction.length);
  return (match[1] === "-" ? "-" : "") + coefficient + "e" + power;
}

function sameDecimal(left, right) {
  if (left === right) return true;
  const key = decimalKey(left);
  return key !== null && key === decimalKey(right);
}

function compareDecimal(left, right) {
  const a = decimalKey(left);
  const b = decimalKey(right);
  if (a === null || b === null) return NaN;
  if (a === b) return 0;
  const sign = key => key === "0" ? 0 : key.startsWith("-") ? -1 : 1;
  if (sign(a) !== sign(b)) return sign(a) - sign(b);
  // Compare the decimal point positions first, then the normalized significant digits.
  const [digitsA, powerA] = a.replace(/^-/, "").split("e");
  const [digitsB, powerB] = b.replace(/^-/, "").split("e");
  const placesA = BigInt(digitsA.length) + BigInt(powerA);
  const placesB = BigInt(digitsB.length) + BigInt(powerB);
  const smaller = placesA === placesB ? digitsA < digitsB : placesA < placesB;
  return sign(a) * (smaller ? -1 : 1);
}
