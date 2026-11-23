import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('activate', Path(__file__).with_name('activate.py'))
activate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activate)


class Node:
    def __init__(self, name, role, children=()):
        self.name, self.role, self.children = name, role, children

    def __iter__(self):
        return iter(self.children)

    def getRole(self):
        return self.role


class AccessibilityTest(unittest.TestCase):
    def test_tolerates_missing_children_during_startup(self):
        button = Node('설정', 'button')
        frame = Node('쪽결', 'frame', [None, button])
        for children in ([frame], [None, frame]):
            with self.subTest(children=children):
                self.assertIs(activate.find_control(children, '쪽결', '설정', 'frame', 'button'), button)

    def test_only_finds_button_in_named_window(self):
        wrong = Node('설정', 'button')
        correct = Node('설정', 'button')
        app = Node('app', 'application', [Node('other', 'frame', [wrong]), Node('쪽결', 'frame', [correct])])
        self.assertIs(activate.find_control(app, '쪽결', '설정', 'frame', 'button'), correct)
        self.assertIsNone(activate.find_control(app, '쪽결', '다른 설정', 'frame', 'button'))


if __name__ == '__main__':
    unittest.main()
