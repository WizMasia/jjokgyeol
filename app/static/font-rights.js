"use strict";

globalThis.FontRights = (() => {
  // Keep the policy in sync with app/font_rights.py. Inspection never follows a font-provided URL.
  const officialUrl = String.raw`https?://(?:(?:www\.)?openfontlicense\.org(?:/open-font-license-official-text|/documents/OFL\.txt)?` +
    String.raw`|scripts\.sil\.org/(?:OFL|cms/scripts/page\.php\?item_id=OFL_web)` +
    String.raw`|(?:www\.)?spdx\.org/licenses/OFL-1\.1\.html|opensource\.org/licenses?/OFL-1\.1` +
    String.raw`|dev\.naver\.com/wiki/nanumfont/index\.php/OpenFontLicense)/?`;
  const oflUrl = new RegExp("^" + officialUrl + "$", "i");
  const faqText = "This license is (?:copied below,? and is also )?available with a FAQ at:? " + officialUrl + "\\.?";
  const faq = new RegExp("^" + faqText + "$", "i");
  const ofl = new RegExp("(?:(?:this font(?: software)? is )?licen[sc]ed under (?:the )?)?" +
    "(?:SIL Open Font License,? (?:Version |v)?1\\.1|(?:SPDX-License-Identifier:\\s*)?OFL-1\\.1)\\.?" +
    "(?: " + faqText + ")?$", "i");
  const heading = "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007";
  const oflTextHash = "70006bea3c19ca31505acc2ab97296f48cccbf972825a66d81321f1474e30ebf";
  // Keep holder names, contact details and reserved names separate from legal prose.
  const nameWord = "[A-Z][a-z]+(?:['’-][A-Z][a-z]+)*";
  const properName = "(?:" + nameWord + "(?: " + nameWord + ")?|[가-힣]+|[A-Z]+|" +
    "(?:[A-Z]+ |" + nameWord + " )+(?:Corporation|Inc|Studio|Project Authors))";
  const contact = String.raw`(?:https?://[A-Za-z0-9./_%#?=&~-]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+)`;
  const fontName = new RegExp("^(?:" + properName + "|\"" + properName + "\"|'" + properName + "')$");
  const attribution = new RegExp(String.raw`^(?:[Cc]opyright(?: (?:©|ⓒ|\([cC]\)))?|©|ⓒ|\([cC]\)) ` +
    String.raw`[0-9]{4}(?:[-–][0-9]{4})?(?:, [0-9]{4})*,? ` + properName +
    String.raw`(?: \(${contact}(?: \| ${contact})*\))?` +
    "(?:,? *with Reserved Font Names? (.+))?$");
  const designer = new RegExp("^Font designed by " + properName + "\\.?$");
  const template = /^Copyright \(c\) <dates?>, <Copyright Holder>\.?$/i;
  const conflict = /personal use only|non[- ]commercial use only|no redistribution|재배포\s*금지|상업적?\s*이용\s*금지/i;
  const scriptUrl = globalThis.document?.currentScript?.src;
  const noticeUrl = scriptUrl ? new URL("ofl-1.1.txt", scriptUrl) : "ofl-1.1.txt";
  const nameIds = [0, 1, 4, 5, 13, 14, 16];
  const maxNameBytes = 1 << 20;
  const os2Length = [68, 86, 96, 96, 96, 100];
  const bad = "글꼴 테이블이 손상되었거나 지원 범위를 벗어납니다.";

  function requireValue(ok, reason = bad) {
    if (!ok) throw new Error(reason);
  }

  function tagAt(view, offset) {
    return String.fromCharCode(...new Uint8Array(view.buffer, view.byteOffset + offset, 4));
  }

  function tables(buffer) {
    const view = new DataView(buffer);
    const magic = buffer.byteLength >= 4 && tagAt(view, 0);
    requireValue(magic === "OTTO" || magic === "\0\x01\0\0", "정적 TTF·OTF 단일 파일만 지원합니다.");
    requireValue(buffer.byteLength >= 12);
    const count = view.getUint16(4);
    const end = 12 + count * 16;
    requireValue(count > 0 && count <= 4095 && end <= buffer.byteLength);
    const result = new Map(), ranges = [];
    for (let pos = 12; pos < end; pos += 16) {
      const tag = tagAt(view, pos), start = view.getUint32(pos + 8), size = view.getUint32(pos + 12);
      requireValue(!result.has(tag) && start >= end && start <= buffer.byteLength - size && start % 4 === 0);
      result.set(tag, new DataView(buffer, start, size));
      if (size) ranges.push([start, start + size]);
    }
    ranges.sort((a, b) => a[0] - b[0]);
    requireValue(ranges.every(([start], i) => !i || ranges[i - 1][1] <= start));
    requireValue(!["fvar", "gvar", "CFF2"].some(tag => result.has(tag)), "가변 글꼴은 현재 조판할 수 없습니다.");
    const outlines = magic === "OTTO" ? {"CFF ": 4} : {loca: 4, glyf: 1};
    for (const [tag, minimum] of Object.entries({head: 54, maxp: 6, hhea: 36, hmtx: 4, cmap: 4, ...outlines})) {
      requireValue(result.get(tag)?.byteLength >= minimum, "정적 윤곽선 조판에 필요한 테이블을 확인할 수 없습니다.");
    }
    return result;
  }

  function decodeName(platform, enc, bytes) {
    let codec;
    if (platform === 0 || (platform === 3 && [0, 1, 10].includes(enc))) codec = "utf-16be";
    else if (platform === 1 && enc === 0) codec = "macintosh";
    else if (platform === 1 && [1, 2, 3, 25].includes(enc) && bytes.every(b => b < 128)) codec = "ascii";
    else return null;
    try {
      return new TextDecoder(codec, {fatal: true, ignoreBOM: true}).decode(bytes);
    } catch {
      return null;
    }
  }

  function readNames(table) {
    requireValue(table?.byteLength >= 6);
    const format = table.getUint16(0), count = table.getUint16(2), storage = table.getUint16(4);
    const end = 6 + 12 * count;
    requireValue(format <= 1 && count <= 4096 && end <= storage && storage <= table.byteLength);
    if (format === 1) {
      requireValue(end + 2 <= storage);
      const langEnd = end + 2 + 4 * table.getUint16(end);
      requireValue(langEnd <= storage);
      for (let p = end + 2; p < langEnd; p += 4) {
        requireValue(storage + table.getUint16(p) + table.getUint16(p + 2) <= table.byteLength);
      }
    }
    const names = Object.fromEntries(nameIds.map(key => [key, new Map()]));
    const undecoded = new Set();
    let total = 0;
    for (let p = 6; p < end; p += 12) {
      const platform = table.getUint16(p), enc = table.getUint16(p + 2), lang = table.getUint16(p + 4);
      const key = table.getUint16(p + 6), size = table.getUint16(p + 8), start = storage + table.getUint16(p + 10);
      requireValue(start + size <= table.byteLength);
      if (!names[key] || !size) continue;
      total += size;
      requireValue(total <= maxNameBytes);
      const bytes = new Uint8Array(table.buffer, table.byteOffset + start, size);
      const value = decodeName(platform, enc, bytes);
      if (value === null || /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(value)) {
        undecoded.add(key);
      } else if (value.trim()) {
        const korean = (platform === 3 && lang === 0x412) || (platform === 1 && lang === 23);
        const rank = korean ? 0 : 1;
        names[key].set(value, Math.min(rank, names[key].get(value) ?? rank));
      }
    }
    for (const key of nameIds) {
      names[key] = [...names[key]].sort((a, b) => a[1] - b[1]).map(([text]) => text);
    }
    return {names, undecoded};
  }

  async function sha256(bytes) {
    if (!globalThis.crypto?.subtle) {
      // LAN HTTP origins can still inspect a file locally with the packaged SHA-256 implementation.
      await import(new URL("sha256.min.js", scriptUrl || document.baseURI).href);
      return globalThis.sha256(bytes);
    }
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    return Array.from(digest, b => b.toString(16).padStart(2, "0")).join("");
  }

  function normalized(text) {
    return text.split(/[ \t\r\n]+/).filter(word => word && !/^-{3,}$/.test(word)).join(" ");
  }

  async function isOfl(value, holders) {
    const text = normalized(value);
    if (text.match(ofl)?.index === 0) return true;
    const start = text.indexOf(heading);
    if (start < 0 || await sha256(new TextEncoder().encode(text.slice(start))) !== oflTextHash) return false;
    const prefix = text.slice(0, start).trim();
    if (!prefix || holders.has(prefix)) return true;
    const grant = prefix.match(ofl);
    return grant && (grant.index === 0 || holders.has(prefix.slice(0, grant.index).trim()));
  }

  function isAttribution(clause) {
    const match = clause.replace(/\.$/, "").match(attribution);
    if (!match) return false;
    return !match[1] || match[1].split(/, | and /).every(name => fontName.test(name));
  }

  async function decide(face, names, undecoded) {
    const flags = face.os2Version < 2 ? face.fsType & 15 : face.fsType;
    let mode = flags & 14;
    if (flags & ~0x30e || (face.os2Version >= 3 && ![0, 2, 4, 8].includes(mode))) {
      return ["unknown", "unknown", "OS/2 내장 권한 비트의 의미가 불분명합니다."];
    }
    mode = mode & 8 ? 8 : mode & 4 ? 4 : mode;
    if (face.known) {
      return ["allowed", "allowed", "설치된 배포본의 SHA-256과 일치합니다. 원본에는 해당 배포본의 OFL 고지를 첨부합니다."];
    }
    const holders = new Set(names[0].map(normalized));
    const checks = await Promise.all(names[13].map(s => isOfl(s, holders)));
    const licensed = checks.length > 0 && checks.every(Boolean);
    let attributed = false, placeholder = false;
    for (const record of names[0]) {
      if (conflict.test(record)) return ["unknown", "unknown", "저작권 표기에 명시된 사용 제한이 있습니다."];
      for (const part of record.split(/(?<=\.)[ \t]+|[\r\n]+/)) {
        const clause = part.replace(/^[ \t]+|[ \t]+$/g, "");
        if (isAttribution(clause)) attributed = true;
        else if (template.test(clause)) placeholder = true;
        else if (clause && clause.toLowerCase() !== "all rights reserved." && !designer.test(clause) &&
                 clause.match(ofl)?.index !== 0 && !faq.test(clause)) {
          return ["unknown", "unknown", "저작권 표기에 있는 약관의 적용 범위를 확인할 수 없습니다."];
        }
      }
    }
    if (undecoded.has(0) || undecoded.has(13) || undecoded.has(14) || (names[13].length && !licensed) ||
        (names[14].length && (!licensed || !names[14].every(s => oflUrl.test(s))))) {
      return ["unknown", "unknown", "파일에 표시된 약관 전체의 적용 범위를 확인할 수 없습니다."];
    }
    if (licensed && (mode === 2 || mode === 4 || flags & 0x200)) {
      return ["unknown", "unknown", "OFL 약관과 OS/2 내장 제한이 충돌합니다."];
    }
    if (flags & 0x200) return ["restricted", "restricted", "비트맵만 내장할 수 있어 윤곽선 글꼴을 사용할 수 없습니다."];
    if (mode === 2) return ["restricted", "restricted", "OS/2에 내장 제한이 명시되어 있습니다."];
    if (mode === 4) return ["restricted", "restricted", "미리보기·인쇄 전용입니다. 현재 PDF는 읽기 전용을 보장하지 않습니다."];
    const archive = licensed && attributed && !placeholder ? "allowed" : "unknown";
    if (flags & 0x100) {
      return ["restricted", archive, "부분 내장이 금지되어 있습니다. 현재 PDF 조판은 전체 내장을 보장하지 않습니다."];
    }
    if (licensed) {
      const reason = archive === "allowed" ? "파일에 SIL OFL 1.1이 명시되어 있습니다. 저작권 표기와 라이선스 전문을 동봉합니다."
        : "SIL OFL 1.1이 명시되었으나 원본에 동봉할 저작권 표기를 확인할 수 없습니다.";
      return ["allowed", archive, reason];
    }
    if (mode === 8) return ["allowed", archive, "OS/2가 편집 가능한 문서 내장을 허용합니다. 원본 재배포 근거는 없습니다."];
    return ["unknown", "unknown", "설치 가능 표시만으로는 PDF 내장과 원본 재배포 권한을 확인할 수 없습니다."];
  }

  async function inspect(blob, known) {
    const data = await blob.arrayBuffer();
    const hash = await sha256(data);
    const locked = known?.faces?.[hash];
    const face = {hash, name: "", family: "", version: "", weight: "regular", copyright: "", license: "", url: "",
      known: Boolean(locked), supported: false, fsType: null, os2Version: null};
    let pdf = "unknown", archive = "unknown", reason;
    try {
      const directory = tables(data);
      const {names, undecoded} = readNames(directory.get("name"));
      face.name = names[4][0] ?? names[16][0] ?? names[1][0] ?? "";
      face.family = names[16][0] ?? names[1][0] ?? face.name;
      face.version = names[5][0] ?? "";
      face.copyright = names[0].join("\n");
      face.license = names[13].join("\n");
      face.url = names[14].find(s => /^https?:\/\/[A-Za-z0-9.-]+(?::[0-9]+)?(?:[/?#][^\s\\]*)?$/i.test(s)) ?? "";
      const metrics = directory.get("OS/2");
      requireValue(metrics?.byteLength >= 10);
      const version = metrics.getUint16(0);
      requireValue(version < os2Length.length && metrics.byteLength >= os2Length[version]);
      Object.assign(face, {supported: true, os2Version: version, fsType: metrics.getUint16(8),
        weight: metrics.getUint16(4) >= 600 ? "bold" : "regular"});
      if (locked) Object.assign(face, {weight: locked.weight, version: locked.version});
      [pdf, archive, reason] = await decide(face, names, undecoded);
    } catch (error) {
      reason = error.message;
    }
    return {...face, pdf: pdf === "allowed", archive: archive === "allowed", remote: archive === "allowed",
      status: {pdf, archive, remote: archive}, reason};
  }

  async function notice(face) {
    requireValue(face?.archive && !face.known && face.copyright, "이 글꼴에는 외부 OFL 고지를 생성할 수 없습니다.");
    const response = await fetch(noticeUrl);
    requireValue(response.ok, "동봉할 OFL 라이선스 전문을 열 수 없습니다.");
    const body = (await response.text()).replace(/\r\n/g, "\n");
    requireValue(await sha256(new TextEncoder().encode(body)) === "6863ff4dc820f24756b6acfb38a2c6bc19d5b8d71b17030330a6f16eb4cee348",
      "동봉할 OFL 라이선스 전문이 손상되었습니다.");
    return new Blob([face.copyright, "\n\n", body], {type: "text/plain;charset=utf-8"});
  }

  return {inspect, notice, sha256};
})();
