"""End-to-end tests for products.py. Run with: python3 -m unittest -v"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "products.py")


class CLITestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.data = os.path.join(self.dir, "products.json")

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        proc = subprocess.run(
            [sys.executable, SCRIPT, *args], cwd=self.dir,
            capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
        self.assertNotIn("Traceback", proc.stderr)
        return proc

    def ok(self, *args):
        proc = self.run_cli(*args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        return json.loads(proc.stdout)

    def fails(self, code, *args):
        proc = self.run_cli(*args)
        self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.strip())
        return proc

    def file_bytes(self, path=None):
        with open(path or self.data, "rb") as fh:
            return fh.read()

    def write_raw(self, content, path=None):
        with open(path or self.data, "w", encoding="utf-8") as fh:
            fh.write(content)

    def seed(self):
        self.ok("add", "--name", "Notebook", "--price", "4.5", "--quantity", "10")
        self.ok("add", "--name", "Pencil", "--price", "1.25", "--quantity", "5")


class TestRequiredSequence(CLITestCase):
    def test_handout_demo_sequence(self):
        self.assertEqual(self.ok("add", "--name", "Notebook", "--price", "4.5", "--quantity", "10"),
                         {"id": 1, "name": "Notebook", "price": "4.50", "quantity": 10})
        self.assertEqual(self.ok("add", "--name", "Pencil", "--price", "1.25", "--quantity", "5")["id"], 2)
        self.assertEqual([p["id"] for p in self.ok("list")], [1, 2])
        self.assertEqual(self.ok("get", "1")["name"], "Notebook")
        self.assertEqual(self.ok("update", "1", "--quantity", "12"),
                         {"id": 1, "name": "Notebook", "price": "4.50", "quantity": 12})
        self.assertEqual(self.ok("delete", "2", "--yes"), {"deleted_id": 2})
        self.assertEqual(self.ok("list"),
                         [{"id": 1, "name": "Notebook", "price": "4.50", "quantity": 12}])
        with open(self.data, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["next_id"], 3)


class TestHelpAndSyntax(CLITestCase):
    def test_help_does_not_touch_file(self):
        for args in (["--help"], ["add", "--help"], ["update", "--help"], ["low-stock", "--help"]):
            proc = self.run_cli(*args)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("usage", proc.stdout)
        self.assertIn("low-stock", self.run_cli("--help").stdout)
        self.assertFalse(os.path.exists(self.data))

    def test_invalid_syntax(self):
        self.seed()
        before = self.file_bytes()
        cases = [
            [],
            ["frobnicate"],
            ["list", "extra"],
            ["list", "--verbose"],
            ["list", "--file", "x.json"],
            ["get"],
            ["get", "1", "2"],
            ["add", "--name", "A", "--price", "1"],
            ["add", "--nam", "A", "--price", "1", "--quantity", "1"],
            ["update", "1"],
            ["delete", "1"],
            ["--file", "", "list"],
        ]
        for args in cases:
            with self.subTest(args=args):
                self.fails(2, *args)
        self.assertEqual(self.file_bytes(), before)

    def test_invalid_ids(self):
        self.seed()
        for bad in ("0", "-1", "abc", "1.0", "+1", "1e3"):
            for cmd in (["get", bad], ["update", bad, "--quantity", "1"], ["delete", bad, "--yes"]):
                with self.subTest(cmd=cmd):
                    self.fails(2, *cmd)


class TestValidation(CLITestCase):
    def test_price_rules(self):
        for raw, stored in (("0", "0.00"), ("4.5", "4.50"), ("4.50", "4.50"), ("10", "10.00")):
            with self.subTest(price=raw):
                self.assertEqual(self.ok("add", "--name", "X", "--price", raw, "--quantity", "1")["price"], stored)
        before = self.file_bytes()
        for raw in ("-1", "+1", "$1", "1e2", "1.234", "1.", ".5", "", " 1", "1,00", "NaN", "１"):
            with self.subTest(price=raw):
                self.fails(2, "add", "--name", "X", "--price", raw, "--quantity", "1")
        self.assertEqual(self.file_bytes(), before)

    def test_quantity_rules(self):
        self.assertEqual(self.ok("add", "--name", "X", "--price", "1", "--quantity", "0")["quantity"], 0)
        before = self.file_bytes()
        for raw in ("-1", "1.5", "+1", "", "abc", "1e2"):
            with self.subTest(quantity=raw):
                self.fails(2, "add", "--name", "X", "--price", "1", "--quantity", raw)
        self.assertEqual(self.file_bytes(), before)

    def test_name_rules(self):
        self.assertEqual(self.ok("add", "--name", "  Blue  Pen  ", "--price", "1", "--quantity", "1")["name"],
                         "Blue  Pen")
        self.assertEqual(self.ok("add", "--name", "Blue  Pen", "--price", "1", "--quantity", "1")["id"], 2)
        before = self.file_bytes()
        for raw in ("", "   ", "\t"):
            self.fails(2, "add", "--name", raw, "--price", "1", "--quantity", "1")
            self.fails(2, "update", "1", "--name", raw)
        self.assertEqual(self.file_bytes(), before)

    def test_update_validates_all_before_saving(self):
        self.seed()
        before = self.file_bytes()
        self.fails(2, "update", "1", "--name", "New", "--price", "-3")
        self.assertEqual(self.file_bytes(), before)


class TestLifecycle(CLITestCase):
    def test_update_preserves_other_fields(self):
        self.seed()
        self.assertEqual(self.ok("update", "2", "--name", "Pen", "--price", "2"),
                         {"id": 2, "name": "Pen", "price": "2.00", "quantity": 5})
        self.assertEqual(self.ok("get", "1"),
                         {"id": 1, "name": "Notebook", "price": "4.50", "quantity": 10})

    def test_ids_never_reused(self):
        self.seed()
        self.ok("delete", "2", "--yes")
        self.assertEqual(self.ok("add", "--name", "Eraser", "--price", "0.5", "--quantity", "3")["id"], 3)
        self.ok("delete", "3", "--yes")
        self.assertEqual(self.ok("add", "--name", "Ruler", "--price", "2", "--quantity", "1")["id"], 4)

    def test_list_sorted_numerically(self):
        self.write_raw(json.dumps({"next_id": 11, "products": [
            {"id": 10, "name": "B", "price": "1.00", "quantity": 1},
            {"id": 2, "name": "A", "price": "1.00", "quantity": 1},
        ]}))
        self.assertEqual([p["id"] for p in self.ok("list")], [2, 10])

    def test_missing_id_errors(self):
        self.seed()
        before = self.file_bytes()
        for cmd in (["get", "99"], ["update", "99", "--quantity", "1"], ["delete", "99", "--yes"]):
            with self.subTest(cmd=cmd):
                self.assertIn("99", self.fails(1, *cmd).stderr)
        self.assertEqual(self.file_bytes(), before)


class TestFiles(CLITestCase):
    def test_missing_file_is_empty_store(self):
        self.assertEqual(self.ok("list"), [])
        self.fails(1, "get", "1")
        self.fails(1, "update", "1", "--quantity", "1")
        self.fails(1, "delete", "1", "--yes")
        self.assertEqual(self.ok("low-stock", "--threshold", "5"), [])
        self.assertFalse(os.path.exists(self.data))

    def test_read_only_commands_do_not_rewrite(self):
        self.seed()
        before = self.file_bytes()
        mtime = os.stat(self.data).st_mtime_ns
        self.ok("list")
        self.ok("get", "1")
        self.ok("low-stock", "--threshold", "100")
        self.assertEqual(self.file_bytes(), before)
        self.assertEqual(os.stat(self.data).st_mtime_ns, mtime)

    def test_separate_file(self):
        self.ok("--file", "demo.json", "add", "--name", "Notebook", "--price", "4.50", "--quantity", "10")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "demo.json")))
        self.assertFalse(os.path.exists(self.data))
        self.assertEqual(len(self.ok("--file", "demo.json", "list")), 1)
        self.assertEqual(self.ok("list"), [])

    def test_path_with_spaces(self):
        os.mkdir(os.path.join(self.dir, "my data"))
        self.ok("--file", "my data/store file.json", "add", "--name", "A", "--price", "1", "--quantity", "1")
        self.assertEqual(len(self.ok("--file", "my data/store file.json", "list")), 1)

    def test_missing_parent_directory(self):
        self.fails(1, "--file", "nope/inv.json", "add", "--name", "A", "--price", "1", "--quantity", "1")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "nope")))

    def test_directory_as_file(self):
        self.fails(1, "--file", ".", "list")

    def test_invalid_stored_data(self):
        bad_contents = [
            "",
            "{not json",
            "[]",
            '{"next_id": 1}',
            '{"next_id": 0, "products": []}',
            '{"next_id": true, "products": []}',
            '{"next_id": 2, "products": [], "extra": 1}',
            '{"next_id": 1, "products": [{"id": 1, "name": "A", "price": "1.00", "quantity": 1}]}',
            '{"next_id": 3, "products": [{"id": 1, "name": "A", "price": "1.00", "quantity": 1},'
            ' {"id": 1, "name": "B", "price": "1.00", "quantity": 1}]}',
            '{"next_id": 2, "products": [{"id": 1, "name": "A", "price": 1.0, "quantity": 1}]}',
            '{"next_id": 2, "products": [{"id": 1, "name": "A", "price": "1.5", "quantity": 1}]}',
            '{"next_id": 2, "products": [{"id": 1, "name": " ", "price": "1.00", "quantity": 1}]}',
            '{"next_id": 2, "products": [{"id": 1, "name": "A", "price": "1.00", "quantity": -1}]}',
            '{"next_id": 2, "products": [{"id": 1, "name": "A", "price": "1.00"}]}',
            '{"next_id": 2, "next_id": 2, "products": []}',
            '{"next_id": NaN, "products": []}',
        ]
        commands = [
            ["list"], ["get", "1"], ["low-stock", "--threshold", "1"],
            ["add", "--name", "A", "--price", "1", "--quantity", "1"],
            ["update", "1", "--quantity", "2"], ["delete", "1", "--yes"],
        ]
        for content in bad_contents:
            self.write_raw(content)
            before = self.file_bytes()
            for cmd in commands:
                with self.subTest(content=content, cmd=cmd):
                    self.fails(1, *cmd)
                    self.assertEqual(self.file_bytes(), before)

    def test_invalid_utf8(self):
        with open(self.data, "wb") as fh:
            fh.write(b'{"next_id": 1, "products": [\xff]}')
        self.fails(1, "list")

    @unittest.skipIf(os.name == "nt" or hasattr(os, "geteuid") and os.geteuid() == 0,
                     "permission test needs a non-root POSIX user")
    def test_write_failure_keeps_data(self):
        self.seed()
        before = self.file_bytes()
        os.chmod(self.data, 0o444)
        try:
            self.fails(1, "add", "--name", "C", "--price", "1", "--quantity", "1")
        finally:
            os.chmod(self.data, 0o644)
        self.assertEqual(self.file_bytes(), before)

    def test_unicode_names_round_trip(self):
        self.assertEqual(self.ok("add", "--name", "笔记本 Café", "--price", "3", "--quantity", "2")["name"],
                         "笔记本 Café")
        with open(self.data, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["products"][0]["name"], "笔记本 Café")


class TestLowStock(CLITestCase):
    def setUp(self):
        super().setUp()
        for name, qty in (("Notebook", "10"), ("Pencil", "5"), ("Eraser", "0"), ("Stapler", "3"), ("Glue", "5")):
            self.ok("add", "--name", name, "--price", "1.50", "--quantity", qty)

    def test_threshold_inclusive_and_sorted(self):
        result = self.ok("low-stock", "--threshold", "5")
        self.assertEqual([(p["name"], p["quantity"]) for p in result],
                         [("Eraser", 0), ("Stapler", 3), ("Pencil", 5), ("Glue", 5)])
        self.assertEqual(set(result[0]), {"id", "name", "price", "quantity"})

    def test_target_adds_reorder_quantity(self):
        result = self.ok("low-stock", "--threshold", "3", "--target", "20")
        self.assertEqual([(p["name"], p["reorder_quantity"]) for p in result],
                         [("Eraser", 20), ("Stapler", 17)])

    def test_no_matches(self):
        self.ok("update", "3", "--quantity", "50")
        self.assertEqual(self.ok("low-stock", "--threshold", "2"), [])

    def test_invalid_arguments(self):
        for args in (["low-stock"], ["low-stock", "--threshold", "-1"], ["low-stock", "--threshold", "2.5"],
                     ["low-stock", "--threshold", "5", "--target", "4"], ["low-stock", "5"],
                     ["low-stock", "--threshold", "5", "--target", "x"]):
            with self.subTest(args=args):
                self.fails(2, *args)

    def test_separate_file(self):
        self.assertEqual(self.ok("--file", "other.json", "low-stock", "--threshold", "100"), [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "other.json")))


if __name__ == "__main__":
    unittest.main()
