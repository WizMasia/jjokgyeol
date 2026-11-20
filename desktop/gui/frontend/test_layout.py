"""Browser layout regression for the shared Korean editor controls."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import threading
import unittest

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent


class EditorLayoutTest(unittest.TestCase):
    def test_shared_controls_align_and_fit_narrow_viewport(self):
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            partial(SimpleHTTPRequestHandler, directory=str(HERE / "dist")),
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=os.environ.get("CHROME_PATH"), headless=True
                )
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    page.add_init_script(
                        "window.__TAURI__ = {core: {invoke: async command => { "
                        "if (command === 'style_convert') return {}; "
                        "throw 'layout-only test'; }}};"
                    )
                    page.goto(f"http://127.0.0.1:{server.server_port}/index.html")
                    page.wait_for_function("document.querySelector('#style').inert === false")
                    page.locator("details > summary").click()
                    font_size = page.locator("#text-roles [data-text='font_pt']")
                    self.assertTrue(
                        font_size.evaluate(
                            "control => { const label = control.closest('label'); "
                            "return label.hidden && label.getClientRects().length === 0; }"
                        )
                    )
                    page.locator("#text-role").select_option("heading1")
                    self.assertTrue(
                        font_size.evaluate(
                            "control => { const label = control.closest('label'); "
                            "return !label.hidden && label.getClientRects().length > 0; }"
                        )
                    )
                    self.assertTrue(page.locator("#text-roles [data-text='font']").is_visible())
                    self.assertTrue(font_size.is_visible())
                    page.evaluate("window.scrollTo(0, 0)")

                    wide_actions = [
                        page.locator("#refresh").bounding_box(),
                        page.locator("#export").bounding_box(),
                    ]
                    self.assertLessEqual(
                        max(box["height"] for box in wide_actions)
                        - min(box["height"] for box in wide_actions),
                        1,
                    )
                    wide_controls = [
                        page.locator(selector).bounding_box()
                        for selector in (
                            "#paragraph-layout",
                            "#body_font",
                            "#body_pt",
                            "#leading_pt",
                        )
                    ]
                    self.assertLessEqual(
                        max(box["y"] for box in wide_controls)
                        - min(box["y"] for box in wide_controls),
                        1,
                    )

                    page.set_viewport_size({"width": 360, "height": 800})
                    narrow_actions = [
                        page.locator("#refresh").bounding_box(),
                        page.locator("#export").bounding_box(),
                    ]
                    self.assertLessEqual(
                        max(box["height"] for box in narrow_actions)
                        - min(box["height"] for box in narrow_actions),
                        1,
                    )

                    input_columns = []
                    for selector in (
                        "#margin-settings input[name='margins_mm']",
                        "#text-roles [data-text='font'], #text-roles [data-text='font_pt']",
                    ):
                        boxes = page.locator(selector).evaluate_all(
                            "elements => elements.filter(element => "
                            "element.getClientRects().length).map(element => { "
                            "const rect = element.getBoundingClientRect(); "
                            "return {x: rect.x, y: rect.y, bottom: rect.bottom}; })"
                        )
                        self.assertGreaterEqual(len(boxes), 2, selector)
                        input_columns.append((selector, boxes))
                        rows = []
                        for box in sorted(boxes, key=lambda item: item["y"]):
                            if not rows or box["y"] > rows[-1]["bottom"] + 1:
                                rows.append({"y": box["y"], "bottom": box["bottom"]})
                            else:
                                rows[-1]["bottom"] = max(rows[-1]["bottom"], box["bottom"])
                        gaps = [
                            following["y"] - current["bottom"]
                            for current, following in zip(rows, rows[1:])
                        ]
                        self.assertLessEqual(max(gaps, default=0), 24, selector)

                    narrow_layout = page.evaluate(
                        """() => {
                          const clipped = [];
                          for (const control of document.querySelectorAll(
                            'button, input, select, textarea, summary'
                          )) {
                            if (!control.getClientRects().length) continue;
                            const rect = control.getBoundingClientRect();
                            if (rect.left < -1 || rect.right > innerWidth + 1) {
                              clipped.push(control.id || control.tagName);
                            }
                            for (let parent = control.parentElement; parent; parent = parent.parentElement) {
                              if (!['auto', 'scroll', 'hidden', 'clip'].includes(
                                getComputedStyle(parent).overflowX
                              )) continue;
                              const left = parent.getBoundingClientRect().left + parent.clientLeft;
                              const right = left + parent.clientWidth;
                              if (rect.left < left - 1 || rect.right > right + 1) {
                                clipped.push(control.id || control.tagName);
                                break;
                              }
                            }
                          }
                          return {
                            bodyWidth: document.body.scrollWidth,
                            documentWidth: document.documentElement.scrollWidth,
                            viewportWidth: innerWidth,
                            clipped,
                            hiddenConditionals: ['#article-style', '#gutter-control', '#line-number-faces']
                              .every(selector => {
                                const element = document.querySelector(selector);
                                return element.hidden && element.getClientRects().length === 0;
                              })
                          };
                        }"""
                    )
                    self.assertLessEqual(
                        narrow_layout["bodyWidth"], narrow_layout["viewportWidth"]
                    )
                    self.assertLessEqual(
                        narrow_layout["documentWidth"], narrow_layout["viewportWidth"]
                    )
                    self.assertEqual(narrow_layout["clipped"], [])
                    self.assertTrue(narrow_layout["hiddenConditionals"])
                    for selector, boxes in input_columns:
                        with self.subTest(fieldset=selector):
                            self.assertLessEqual(
                                max(box["x"] for box in boxes)
                                - min(box["x"] for box in boxes),
                                2,
                                f"inputs should share a left edge in {selector}",
                            )
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
