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

test('paragraph alignment forwards the payload and reply unchanged over local IPC', async () => {
  const selection = {source: '원고', first_line: 0, last_line: 0};
  for (const payload of [selection, {...selection, align: null}, {...selection, align: 'right'}]) {
    const result = {supported: true, source: '결과', align: payload.align ?? null, message: ''};
    const desktop = bridge((command, args) => {
      assert.equal(command, 'paragraph_align');
      assert.equal(args.payload, payload);
      return result;
    });
    assert.equal(await desktop.paragraphAlign(payload), result);
  }
});

test('plain, paired and font previews opt in without changing PDF bytes or default types', async () => {
  const row = {before_line: 0, after_line: 2, gap_sp: 0, source: 'rule'};
  for (const kind of ['plain', 'paired', 'font']) {
    const both = kind !== 'plain';
    const spacing = both ? {rule: [row], model: [{...row, gap_sp: 99, source: 'ai'}]} : {rule: [row]};
    const desktop = bridge((cmd, args, options) => {
      if (cmd === 'style_convert') return {};
      assert.equal(cmd, {plain: 'render_proof', paired: 'render_model_proof', font: 'render_font_proof'}[kind]);
      const optIn = options?.headers?.['x-jjokgyeol-spacing'] === '1';
      if (kind === 'font') assert.deepEqual(args, Uint8Array.of(1, 2));
      if (!both && !optIn) return Buffer.from('%PDF-rule');
      const header = Buffer.from(JSON.stringify({decision: both ? 'model' : 'rule', reason: null,
        rule_bytes: 9, ...(optIn && {spacing})}));
      const prefix = Buffer.alloc(4);
      prefix.writeUInt32BE(header.length);
      return Buffer.concat([prefix, header, Buffer.from('%PDF-rule' + (both ? '%PDF-model' : ''))]);
    });
    const call = flag => kind === 'font' ? desktop.fontProof(Uint8Array.of(1, 2), flag) :
      both ? desktop.modelProof('source', {}, {}, flag) : desktop.proof('source', {}, flag);
    const proof = await call(true);
    assert.deepEqual(JSON.parse(JSON.stringify(proof.spacing ?? null)), spacing);
    assert.equal(await proof.rule.text(), '%PDF-rule');
    if (both) assert.equal(await proof.model.text(), '%PDF-model');
    const legacy = await call();
    assert.equal(await (both ? legacy.rule : legacy).text(), '%PDF-rule');
    assert.equal(legacy.spacing, undefined);
  }
});

test('a previous native runner returning raw PDF still supplies an opted-in rule proof', async () => {
  const desktop = bridge(cmd => cmd === 'style_convert' ? {} : Buffer.from('%PDF-old'));
  const proof = await desktop.proof('source', {}, true);
  assert.equal(await proof.rule.text(), '%PDF-old');
  assert.equal(proof.spacing, undefined);
});
