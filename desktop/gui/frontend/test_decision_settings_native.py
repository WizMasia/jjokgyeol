"""Drive both native windows against the Tauri command and binary proof contracts."""
import base64
import hashlib
import json
import os
from pathlib import Path
from threading import Thread
import unittest

from playwright.sync_api import expect, sync_playwright

from editor_server import EditorServer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def frame(rule, model, decision, reason):
    meta = json.dumps({"decision": decision, "reason": reason, "rule_bytes": len(rule)}).encode()
    return len(meta).to_bytes(4, "big") + meta + rule + model


class DecisionSettingsTest(unittest.TestCase):
    def test_native_window_check_apply_proof_compare_and_export(self):
        rule = (ROOT / "proofs/engine-comparison/LuaLaTeX.pdf").read_bytes()
        model = (ROOT / "proofs/decision-model/model.pdf").read_bytes()
        server = EditorServer()
        self.addCleanup(server.server_close)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        with sync_playwright() as p, p.chromium.launch(executable_path=os.getenv("CHROME_PATH")) as browser:
            context = browser.new_context()
            context.add_init_script("""
                window.calls = [];
                const bytes = text => Uint8Array.from(atob(text), c => c.charCodeAt(0)).buffer;
                window.__TAURI__ = {
                  event: {listen: async (_, handler) => { window.applied = handler; }},
                  core: {invoke: async (command, args) => {
                    calls.push({command, args});
                    if (command === "settings_snapshot") return snapshot;
                    if (command === "builtin_font") return bytes(await fixtureFont(args.hash));
                    if (command === "take_settings_connection") {
                      if (args.revision === 2 && !window.finishOldTake) {
                        return new Promise(resolve => { window.finishOldTake = resolve; });
                      }
                      return takeConnection(args.revision);
                    }
                    if (command === "list_decision_models") return {models:["native-model", "another-model"]};
                    if (command === "check_decision_connection") {
                      if (args.connection.provider === "builtin") throw JSON.stringify({code:"builtin_unavailable"});
                      if (window.failCheck) throw JSON.stringify({code:"authentication", message:"native-test-key"});
                      return {ok:true};
                    }
                    if (command === "style_convert")
                      return args.action === 'export' ? {body_sp: 720896} : {};
                    if (command === "render_proof") return bytes(RULE_DATA);
                    if (command === "render_model_proof") {
                      if (window.renderError) throw JSON.stringify(window.renderError);
                      if (window.timedOut) return bytes(TIMEOUT_FRAME);
                      return window.broken || bytes(MODEL_FRAME);
                    }
                    if (command === "save_bytes") window.saved = Array.from(args);
                    return true;
                  }}
                };
            """.replace("RULE_DATA", json.dumps(base64.b64encode(rule).decode()))
               .replace("MODEL_FRAME", json.dumps(base64.b64encode(frame(rule, model, "mixed", "rate_limit")).decode()))
               .replace("TIMEOUT_FRAME", json.dumps(base64.b64encode(frame(rule, b"", "rule", "timeout")).decode())))
            main = context.new_page()
            # This mocked installation has one licensed face. The interop suite exercises the full Nanum catalog.
            fixture = ROOT / "tests/fixtures/fonts/abel"
            assets = [path.read_bytes() for path in (fixture / "Abel-Regular.ttf", fixture / "OFL.txt")]
            face_hash, notice_hash = (hashlib.sha256(data).hexdigest() for data in assets)
            encoded = dict(zip((face_hash, notice_hash), (base64.b64encode(data).decode() for data in assets)))
            main.expose_function("fixtureFont", lambda digest: encoded[digest])
            pending_connections = {}
            main.expose_function("takeConnection", lambda handle: pending_connections.pop(handle))
            main.goto(url)
            expect(main.locator("#status")).to_have_text("최신 버전", timeout=30000)
            main.evaluate("""([face,notice]) => {
                const known = settings.font_assets;
                for (const hash of Object.keys(known.faces)) delete known.faces[hash];
                known.faces[face] = {family:"abel", name:"Abel", weight:"regular", version:"1.000"};
                known.notice = notice;
            }""", [face_hash, notice_hash])

            main.locator("#save-document").click()
            main.wait_for_function("window.saved && !document.querySelector('#save-document').disabled")
            main.evaluate("delete window.saved")

            def open_child():
                main.locator("#open-settings").click()
                main.wait_for_function("calls.some(c => c.command === 'open_settings_window')")
                snapshot = main.evaluate("""() => calls.splice(calls.findIndex(c =>
                    c.command === 'open_settings_window'), 1)[0].args.snapshot""")
                child = context.new_page()
                child.add_init_script("window.snapshot = " + json.dumps(snapshot))
                child.goto(url + "?view=settings")
                expect(child.locator("#apply-settings")).to_be_enabled()
                expect(child.locator(".masthead")).to_be_hidden()
                expect(child.locator(".spread")).to_be_hidden()
                expect(child.locator("#paragraph-dock")).to_be_hidden()
                expect(child.locator("#app-status-bar")).to_be_hidden()
                return child

            child = open_child()
            child.locator("#decision-mode").select_option("model")
            child.locator("#decision-provider").select_option("chat")
            child.locator("#decision-list-models").click()
            child.locator("#decision-model-list").select_option("native-model")
            expect(child.locator("#decision-model")).to_have_value("native-model")
            catalog = child.evaluate("calls.find(c => c.command === 'list_decision_models').args.connection")
            self.assertEqual(catalog["provider"], "chat")
            self.assertNotIn("model", catalog)
            child.locator("#decision-api-key").fill("native-test-key")
            assert not child.evaluate("calls.some(c => c.command === 'check_decision_connection')")
            child.evaluate("window.failCheck = true")
            child.locator("#decision-check").click()
            expect(child.locator("#decision-check-status")).to_contain_text("인증")
            expect(child.locator("#decision-check-status")).not_to_contain_text("native-test-key")
            child.evaluate("window.failCheck = false")
            child.locator("#decision-check").click()
            expect(child.locator("#decision-check-status")).to_contain_text("성공")
            check = child.evaluate("calls.find(c => c.command === 'check_decision_connection').args.connection")
            child.locator("#apply-settings").click()
            child.wait_for_function("calls.some(c => c.command === 'apply_settings')")
            event = child.evaluate("calls.find(c => c.command === 'apply_settings').args.snapshot")
            # Rust removes the connection from the event and allows only the main window to take it.
            self.assertEqual(event["connection"], check)
            pending_connections[1] = event.pop("connection")
            event["connectionRevision"] = 1
            self.assertNotIn("native-test-key", json.dumps(event))
            child.close()
            # Rust serializes object fields in key order when sending the event.
            main.evaluate("payload => applied({payload})", json.loads(json.dumps(event, sort_keys=True)))
            expect(main.locator("#status")).to_contain_text("갱신 필요")
            self.assertFalse(pending_connections)
            self.assertFalse(main.evaluate("calls.some(c => ['set_style_dirty','set_dirty'].includes(c.command))"))
            main.locator("#refresh").click()
            status = main.locator("#proof-decision")
            expect(status).to_contain_text("혼합 조판", timeout=30000)
            args = main.evaluate("calls.find(c => c.command === 'render_model_proof').args")
            self.assertEqual(args["connection"], check)
            self.assertEqual(args["style"], {"body_sp": 720896})
            expect(main.locator("#desktop-pages canvas")).to_have_count(1)
            main.locator("#export").click()
            main.wait_for_function("window.saved")
            self.assertEqual(bytes(main.evaluate("saved")), model)
            main.evaluate("delete window.saved")
            main.evaluate("window.timedOut = true")
            main.locator("#refresh").click()
            expect(status).to_contain_text("시간 초과")
            choice = main.locator("#proof-choice")
            expect(choice).to_have_value("model")
            choice.select_option("rule")
            main.evaluate("window.timedOut = false")
            main.locator("#refresh").click()
            expect(status).not_to_contain_text("시간 초과")
            expect(choice).to_have_value("rule")
            expect(status).to_contain_text("규칙 조판")
            expect(main.locator("#desktop-pages canvas")).to_have_count(2)
            main.locator("#export").click()
            main.wait_for_function("window.saved")
            self.assertEqual(bytes(main.evaluate("saved")), rule)
            main.evaluate("delete window.saved")
            choice.select_option("model")
            expect(status).to_contain_text("요청 한도")
            # An immediate export waits for the selected PDF to finish displaying.
            main.evaluate("""() => {
                const pages = desktop.pages.bind(desktop);
                desktop.pages = async blob => {
                    await new Promise(resolve => { window.finishSwitch = resolve; });
                    return pages(blob);
                };
            }""")
            main.locator("#proof-choice").select_option("rule")
            main.wait_for_function("typeof window.finishSwitch === 'function'")
            expect(main.locator("#export")).to_be_disabled()
            main.evaluate("finishSwitch()")
            expect(main.locator("#desktop-pages canvas")).to_have_count(2)
            main.locator("#export").click()
            main.wait_for_function("window.saved")
            self.assertEqual(bytes(main.evaluate("saved")), rule)

            main.evaluate("window.broken = [0, 0, 255, 255]")
            main.locator("#refresh").click()
            expect(main.locator("#status")).to_contain_text("갱신 실패")
            expect(main.locator("#desktop-pages canvas")).to_have_count(2)
            main.evaluate("window.renderError = {code:'invalid_input', field:'source', message:'원고에 본문을 입력하세요.'}")
            main.locator("#refresh").click()
            expect(main.locator("#error")).to_have_text("source · 원고에 본문을 입력하세요.")
            expect(main.locator("#desktop-pages canvas")).to_have_count(2)
            main.evaluate("window.renderError = {code:'model', message:'native-test-key'}")
            main.locator("#refresh").click()
            expect(main.locator("#error")).to_contain_text("모델 ID")
            expect(main.locator("#error")).not_to_contain_text("native-test-key")
            # The later connection wins even if a previous IPC read returns afterwards.
            main.evaluate("payload => {window.oldApply = applied({payload})}", {**event, "connectionRevision": 2})
            main.wait_for_function("typeof window.finishOldTake === 'function'")
            expect(main.locator("#open-settings")).to_be_disabled()
            pending_connections[3] = {**check, "model": "latest"}
            main.evaluate("payload => applied({payload})", {**event, "connectionRevision": 3})
            expect(main.locator("#open-settings")).to_be_enabled()
            main.evaluate("conn => {finishOldTake(conn); return oldApply}", check)
            main.evaluate("payload => applied({payload})", {**event, "connectionRevision": 2})
            self.assertEqual(main.evaluate("""calls.filter(c =>
                c.command === 'take_settings_connection' && c.args.revision === 2).length"""), 1)

            # A failed private read retains both the applied connection and the last proof.
            main.evaluate("payload => applied({payload})", {**event, "connectionRevision": 98})
            expect(main.locator("#error")).to_contain_text("연결 설정")
            expect(main.locator("#desktop-pages canvas")).to_have_count(2)
            again = open_child()
            expect(again.locator("#decision-api-key")).to_have_value("native-test-key")
            expect(again.locator("#decision-model")).to_have_value("latest")
            again.locator("#decision-base-url").fill("http://localhost:1234/v1")
            expect(again.locator("#decision-api-key")).to_have_value("")
            again.locator("#close-settings").click()
            self.assertTrue(again.evaluate("calls.some(c => c.command === 'cancel_settings')"))

            builtin = open_child()
            builtin.locator("#decision-mode").select_option("builtin")
            expect(builtin.locator("#decision-fields")).to_be_hidden()
            builtin.locator("#decision-check").click()
            expect(builtin.locator("#decision-check-status")).to_contain_text("내장 모델")
            builtin.locator("#apply-settings").click()
            builtin.wait_for_function("calls.some(c => c.command === 'apply_settings')")
            self.assertEqual(builtin.evaluate("calls.find(c => c.command === 'apply_settings').args.snapshot.connection"),
                             {"provider": "builtin"})
