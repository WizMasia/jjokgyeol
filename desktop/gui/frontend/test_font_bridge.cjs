const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function bridge(invoke) {
  const script = fs.readFileSync(path.join(__dirname, 'desktop.js'), 'utf8');
  return vm.runInNewContext(script + '\ndesktop', {
    window: {__TAURI__: {core: {invoke}}}, Uint8Array, Blob, DataView, TextDecoder,
  });
}

test('font selection exposes opaque ids and returns exact local binary assets', async () => {
  const locked = 'c'.repeat(64);
  const samples = {list_fonts: [{id: 'opaque', name: 'Face'}],
    read_font: Uint8Array.of(0, 255, 96).buffer, builtin_font: Uint8Array.of(255, 0, 9).buffer};
  const calls = [];
  const desktop = bridge((cmd, args) => {
    calls.push([cmd, args?.id ?? args?.hash]);
    return samples[cmd];
  });
  assert.deepEqual(await desktop.listFonts(), samples.list_fonts);
  assert.deepEqual(new Uint8Array(await (await desktop.readFont('opaque')).arrayBuffer()), Uint8Array.of(0, 255, 96));
  assert.deepEqual(new Uint8Array(await (await desktop.builtinFont(locked)).arrayBuffer()), Uint8Array.of(255, 0, 9));
  assert.deepEqual(calls, [['list_fonts', undefined], ['read_font', 'opaque'], ['builtin_font', locked]]);
});

test('both binary frame inputs keep the exact view and decode rule and model PDFs', async () => {
  const selected = Uint8Array.of(32, 0, 255, 33).subarray(1, 3);
  for (const [decision, input] of [['rule', selected], ['model', new Blob([selected])]]) {
    const desktop = bridge((cmd, bytes) => {
      assert.equal(cmd, 'render_font_proof');
      assert.deepEqual(bytes, Uint8Array.of(0, 255));
      const header = Buffer.from(JSON.stringify({decision, reason: null, rule_bytes: 3}));
      const prefix = Buffer.alloc(4);
      prefix.writeUInt32BE(header.length);
      return Buffer.concat([prefix, header, Buffer.from(decision === 'model' ? 'onetwo' : 'one')]);
    });
    const result = await desktop.fontProof(input);
    assert.equal(result.decision, decision);
    assert.equal(await result.rule.text(), 'one');
    assert.equal(result.model && await result.model.text(), decision === 'rule' ? null : 'two');
  }
});
