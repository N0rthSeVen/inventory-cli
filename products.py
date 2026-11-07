#!/usr/bin/env python3
"""Command-line inventory manager for a small store.

Product records are kept in a local JSON file (default: products.json in the
current working directory). Each invocation performs exactly one operation and
exits; nothing is ever read from standard input.

Only the Python 3 standard library is used.
"""

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from decimal import Decimal

DEFAULT_FILE = "products.json"

EXIT_OK = 0
EXIT_DATA = 1   # product not found, invalid stored data, file read/write failure
EXIT_USAGE = 2  # invalid syntax, missing arguments, invalid input values

PRICE_INPUT_RE = re.compile(r"[0-9]+(?:\.[0-9]{1,2})?")
PRICE_STORED_RE = re.compile(r"[0-9]+\.[0-9]{2}")
UNSIGNED_INT_RE = re.compile(r"[0-9]+")

STORE_KEYS = {"next_id", "products"}
PRODUCT_KEYS = {"id", "name", "price", "quantity"}


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class CLIError(Exception):
    """An expected failure that is reported on stderr with an exit code."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class UsageError(CLIError):
    def __init__(self, message):
        super().__init__(message, EXIT_USAGE)


class DataError(CLIError):
    def __init__(self, message):
        super().__init__(message, EXIT_DATA)


# --------------------------------------------------------------------------
# Input validation (used as argparse "type" functions, so a bad value is
# rejected with exit code 2 before the data file is ever opened)
# --------------------------------------------------------------------------

def _to_int(text, label):
    if not UNSIGNED_INT_RE.fullmatch(text):
        raise argparse.ArgumentTypeError(
            f"invalid {label} {text!r}: must be a nonnegative whole number")
    try:
        return int(text)
    except ValueError:  # e.g. more digits than Python allows to convert
        raise argparse.ArgumentTypeError(f"invalid {label} {text!r}: too large")


def product_id(text):
    value = _to_int(text, "ID")
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"invalid ID {text!r}: must be a positive integer")
    return value


def product_name(text):
    name = text.strip()
    if not name:
        raise argparse.ArgumentTypeError(
            "invalid name: must contain at least one non-whitespace character")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        raise argparse.ArgumentTypeError("invalid name: not valid text")
    return name


def product_price(text):
    if not PRICE_INPUT_RE.fullmatch(text):
        raise argparse.ArgumentTypeError(
            f"invalid price {text!r}: use a nonnegative amount with at most two "
            "decimal places, e.g. 0, 4.5 or 4.50 (no signs, symbols or exponents)")
    # Decimal keeps the value exact; format it with exactly two decimals.
    return f"{Decimal(text):.2f}"


def product_quantity(text):
    return _to_int(text, "quantity")


def threshold_value(text):
    return _to_int(text, "threshold")


def target_value(text):
    return _to_int(text, "target")


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def empty_store():
    return {"next_id": 1, "products": []}


def _no_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def _no_special_constants(name):
    raise ValueError(f"unsupported JSON value {name}")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate_store(data, path):
    """Raise DataError unless `data` matches the required schema."""

    def bad(reason):
        return DataError(f"invalid data file {path!r}: {reason}")

    if not isinstance(data, dict):
        raise bad("top level must be a JSON object")
    if set(data) != STORE_KEYS:
        raise bad('expected exactly the keys "next_id" and "products"')

    next_id = data["next_id"]
    if not _is_int(next_id) or next_id < 1:
        raise bad("next_id must be a positive integer")

    products = data["products"]
    if not isinstance(products, list):
        raise bad("products must be an array")

    seen_ids = set()
    for index, product in enumerate(products):
        where = f"products[{index}]"
        if not isinstance(product, dict) or set(product) != PRODUCT_KEYS:
            raise bad(f"{where} must have exactly id, name, price and quantity")

        pid = product["id"]
        if not _is_int(pid) or pid < 1:
            raise bad(f"{where}.id must be a positive integer")
        if pid in seen_ids:
            raise bad(f"duplicate product ID {pid}")
        if pid >= next_id:
            raise bad(f"next_id ({next_id}) must be greater than every product ID")
        seen_ids.add(pid)

        name = product["name"]
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise bad(f"{where}.name must be non-empty, trimmed text")

        price = product["price"]
        if not isinstance(price, str) or not PRICE_STORED_RE.fullmatch(price):
            raise bad(f'{where}.price must be a string like "4.50"')

        quantity = product["quantity"]
        if not _is_int(quantity) or quantity < 0:
            raise bad(f"{where}.quantity must be a nonnegative integer")


def load_store(path):
    """Read and validate the store. A missing file is an empty store."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return empty_store()
    except OSError as exc:
        raise DataError(f"cannot read data file {path!r}: {exc.strerror or exc}")

    try:
        data = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_no_special_constants,
        )
    except (ValueError, RecursionError) as exc:
        # UnicodeDecodeError and JSONDecodeError are both ValueErrors.
        raise DataError(f"invalid data file {path!r}: not valid UTF-8 JSON ({exc})")

    validate_store(data, path)
    return data


def _new_file_mode():
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def save_store(path, store):
    """Write the store atomically: temp file in the same folder, then replace.

    If anything fails, the original file is left exactly as it was.
    Missing parent directories are never created.
    """
    target = os.path.realpath(path)
    directory = os.path.dirname(target)
    if not os.path.isdir(directory):
        raise DataError(
            f"cannot write data file {path!r}: directory {directory!r} does not exist")
    if os.path.exists(target) and not os.access(target, os.W_OK):
        raise DataError(f"cannot write data file {path!r}: permission denied")

    payload = (json.dumps(store, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".products-", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            if os.path.exists(target):
                mode = stat.S_IMODE(os.stat(target).st_mode)
            else:
                mode = _new_file_mode()
            os.chmod(tmp_path, mode)
        except OSError:
            pass  # permissions are best-effort (e.g. on Windows)
        os.replace(tmp_path, target)
        tmp_path = None
    except OSError as exc:
        raise DataError(f"cannot write data file {path!r}: {exc.strerror or exc}")
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def find_product(store, pid):
    for product in store["products"]:
        if product["id"] == pid:
            return product
    raise DataError(f"product with ID {pid} not found")


def sorted_products(store):
    return sorted(store["products"], key=lambda p: p["id"])


# --------------------------------------------------------------------------
# Commands — each returns the JSON-serialisable result to print
# --------------------------------------------------------------------------

def cmd_add(args):
    store = load_store(args.file)
    product = {
        "id": store["next_id"],
        "name": args.name,
        "price": args.price,
        "quantity": args.quantity,
    }
    store["products"].append(product)
    store["next_id"] += 1
    save_store(args.file, store)
    return product


def cmd_list(args):
    return sorted_products(load_store(args.file))


def cmd_get(args):
    return find_product(load_store(args.file), args.id)


def cmd_update(args):
    changes = {
        field: getattr(args, field)
        for field in ("name", "price", "quantity")
        if getattr(args, field) is not None
    }
    if not changes:
        raise UsageError(
            "update needs at least one of --name, --price or --quantity")

    store = load_store(args.file)
    product = find_product(store, args.id)
    product.update(changes)
    save_store(args.file, store)
    return product


def cmd_delete(args):
    if not args.yes:
        raise UsageError(f"refusing to delete product {args.id} without --yes")

    store = load_store(args.file)
    product = find_product(store, args.id)
    store["products"].remove(product)
    save_store(args.file, store)
    return {"deleted_id": args.id}


def cmd_low_stock(args):
    """Custom feature: products at or below a stock threshold (reorder report).

    Results are sorted by quantity (most urgent first), then by ID. With
    --target, each result also gets reorder_quantity = target - quantity,
    i.e. how many units to order to bring that product back up to target.
    """
    if args.target is not None and args.target < args.threshold:
        raise UsageError(
            f"--target ({args.target}) must be greater than or equal to "
            f"--threshold ({args.threshold})")

    store = load_store(args.file)
    low = [p for p in store["products"] if p["quantity"] <= args.threshold]
    low.sort(key=lambda p: (p["quantity"], p["id"]))

    if args.target is None:
        return low
    return [dict(p, reorder_quantity=args.target - p["quantity"]) for p in low]


COMMANDS = {
    "add": cmd_add,
    "list": cmd_list,
    "get": cmd_get,
    "update": cmd_update,
    "delete": cmd_delete,
    "low-stock": cmd_low_stock,
}


# --------------------------------------------------------------------------
# Command-line interface
# --------------------------------------------------------------------------

MAIN_EPILOG = """\
examples:
  python3 products.py add --name "Notebook" --price 4.50 --quantity 10
  python3 products.py list
  python3 products.py get 1
  python3 products.py update 1 --quantity 12
  python3 products.py delete 1 --yes
  python3 products.py low-stock --threshold 5 --target 20
  python3 products.py --file demo.json list

exit codes:
  0  success (including an empty list or a help request)
  1  product ID not found, invalid stored data, or file read/write failure
  2  invalid command syntax, missing arguments, or invalid input values
"""

LOW_STOCK_EPILOG = """\
Shows every product whose quantity is at or below THRESHOLD, most urgent
(lowest quantity) first. Prints [] when nothing is low. The data file is
only read, never created or changed.

With --target TARGET, each result also includes "reorder_quantity":
TARGET minus the current quantity. TARGET must be >= THRESHOLD.

examples:
  python3 products.py low-stock --threshold 5
  python3 products.py low-stock --threshold 5 --target 20
"""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="products.py",
        description="Manage a small store's product inventory stored in a JSON file.",
        epilog=MAIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument(
        "--file", default=DEFAULT_FILE, metavar="PATH",
        help=f"JSON data file to use (default: {DEFAULT_FILE} in the current "
             "directory); must come before the command")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND", title="commands")
    sub.required = True

    def add_command(name, help_text, **kwargs):
        return sub.add_parser(
            name, help=help_text, description=help_text,
            allow_abbrev=False,
            formatter_class=argparse.RawDescriptionHelpFormatter, **kwargs)

    p = add_command("add", "create a product and print it")
    p.add_argument("--name", required=True, type=product_name, metavar="NAME",
                   help="product name (surrounding whitespace is trimmed)")
    p.add_argument("--price", required=True, type=product_price, metavar="PRICE",
                   help="nonnegative price, e.g. 4, 4.5 or 4.50")
    p.add_argument("--quantity", required=True, type=product_quantity, metavar="QTY",
                   help="nonnegative whole number of units")

    add_command("list", "print all products sorted by ID")

    p = add_command("get", "print one product by ID")
    p.add_argument("id", type=product_id, metavar="ID", help="positive product ID")

    p = add_command("update", "change one or more fields of a product")
    p.add_argument("id", type=product_id, metavar="ID", help="positive product ID")
    p.add_argument("--name", type=product_name, metavar="NAME", help="new name")
    p.add_argument("--price", type=product_price, metavar="PRICE", help="new price")
    p.add_argument("--quantity", type=product_quantity, metavar="QTY",
                   help="new quantity")

    p = add_command("delete", "delete a product (requires --yes)")
    p.add_argument("id", type=product_id, metavar="ID", help="positive product ID")
    p.add_argument("--yes", action="store_true",
                   help="confirm the deletion (required; there is no prompt)")

    p = add_command("low-stock",
                    "list products at or below a quantity threshold (reorder report)",
                    epilog=LOW_STOCK_EPILOG)
    p.add_argument("--threshold", required=True, type=threshold_value, metavar="THRESHOLD",
                   help="include products whose quantity is <= this number")
    p.add_argument("--target", type=target_value, metavar="TARGET",
                   help="desired stock level; adds reorder_quantity to each result")

    return parser


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    parser = build_parser()
    args = parser.parse_args(argv)  # syntax/value errors exit 2; --help exits 0
    if not args.file:
        parser.error("--file PATH must not be empty")

    try:
        result = COMMANDS[args.command](args)
    except CLIError as exc:
        print(f"products.py: error: {exc}", file=sys.stderr)
        return exc.code

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
