# Product Inventory CLI

A Python 3 command-line tool for a small store's product inventory. It can create, read, update and delete product records, which it saves in a local JSON file. It also has a custom **`low-stock` reorder report**.

- Entry point: `products.py`
- Requirements: Python 3.7 or newer. It uses only the standard library, so there is nothing to install.
- Each call does one thing and exits. It never prompts for input.

## Usage

```
python3 products.py [--file PATH] COMMAND [OPTIONS]
```

On Windows you can use `py -3` in place of `python3`. `--file PATH` has to come **before** the command. It picks the JSON data file to use. If you leave it out, the tool uses `products.json` in the current directory. Put quotes around names and paths that contain spaces.

| Command | What it does |
|---|---|
| `add --name NAME --price PRICE --quantity QTY` | Creates a product and prints it as a JSON object |
| `list` | Prints every product as a JSON array, sorted by ID (`[]` if there are none) |
| `get ID` | Prints one product |
| `update ID [--name NAME] [--price PRICE] [--quantity QTY]` | Changes only the fields you pass (at least one is required) |
| `delete ID --yes` | Deletes a product and prints `{"deleted_id": ID}` |
| `low-stock --threshold N [--target M]` | **Custom feature.** See below. |

Run `python3 products.py --help` or `python3 products.py COMMAND --help` for full usage. Asking for help never reads or changes the data file.

### Field rules

| Field | Rule |
|---|---|
| `id` | Assigned automatically, starting at 1. Never changes and is never reused, even after a delete. |
| `name` | Must contain at least one non-whitespace character. Whitespace at the start and end is trimmed. Duplicate names are allowed. |
| `price` | Digits, optionally followed by `.` and one or two more digits (`0`, `4.5`, `4.50`). It is stored as a string with two decimals, like `"4.50"`. Signs, `$`, exponents and a third decimal place are rejected. |
| `quantity` | A whole number of zero or more. |

### Storage format

```json
{
  "next_id": 2,
  "products": [
    {"id": 1, "name": "Notebook", "price": "4.50", "quantity": 10}
  ]
}
```

- If the data file does not exist, the tool treats it as an empty store. `list` prints `[]`, and the first successful `add` creates the file. Read-only commands never create or rewrite it.
- If the file is empty, is not valid JSON, or does not match the schema, the tool reports an error and leaves the file alone.
- Changes are written atomically: the tool writes a temporary file in the same folder and then swaps it into place. A failed command never changes the file or uses up an ID. Missing parent folders are never created.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (this includes an empty list or `--help`) |
| 1 | Product ID not found, invalid stored data, or a file read/write failure |
| 2 | Invalid command syntax, missing arguments, or invalid input values |

On success, the tool prints only JSON to stdout. On failure, it prints a short message to stderr and nothing to stdout.

## Custom feature: `low-stock` reorder report

### Who it helps and why

It helps whoever orders stock for the store, such as the owner or a shift manager. `list` shows every product, so with a long catalogue it is easy to miss the item that is about to run out. `low-stock` answers two questions with one command:

1. **What is running low?** Every product whose quantity is at or below a threshold, with the most urgent (lowest quantity) first.
2. **How much should I order?** If you give a target stock level, each result also shows how many units it takes to get back to that level. The output can serve as a purchase list.

### Syntax

```
python3 products.py [--file PATH] low-stock --threshold THRESHOLD [--target TARGET]
```

| Input | Required | Rule |
|---|---|---|
| `--threshold THRESHOLD` | yes | A whole number ≥ 0. Products with `quantity <= THRESHOLD` are included. |
| `--target TARGET` | no | A whole number ≥ `THRESHOLD`. Adds `"reorder_quantity": TARGET - quantity` to each result. |

### Logic

1. Validate the arguments. A non-integer or negative value, a missing `--threshold`, or `--target` smaller than `--threshold` exits with code 2. The data file is not opened in that case.
2. Load and validate the data file, using the same rules as every other command.
3. Keep the products where `quantity <= threshold`.
4. Sort them by quantity in ascending order, with ties broken by ID.
5. If `--target` was given, add `reorder_quantity` to each result.
6. Print the JSON array. This prints `[]` when nothing matches, with exit code 0. The file is only read and never written.

### Expected output and examples

```
python3 products.py add --name "Notebook" --price 4.50 --quantity 10
python3 products.py add --name "Pencil" --price 1.25 --quantity 5
python3 products.py add --name "Eraser" --price 0.75 --quantity 0
python3 products.py add --name "Stapler" --price 8 --quantity 3
python3 products.py low-stock --threshold 5
```

```json
[
  {"id": 3, "name": "Eraser", "price": "0.75", "quantity": 0},
  {"id": 4, "name": "Stapler", "price": "8.00", "quantity": 3},
  {"id": 2, "name": "Pencil", "price": "1.25", "quantity": 5}
]
```

With a target level:

```
python3 products.py low-stock --threshold 5 --target 20
```

```json
[
  {"id": 3, "name": "Eraser", "price": "0.75", "quantity": 0, "reorder_quantity": 20},
  {"id": 4, "name": "Stapler", "price": "8.00", "quantity": 3, "reorder_quantity": 17},
  {"id": 2, "name": "Pencil", "price": "1.25", "quantity": 5, "reorder_quantity": 15}
]
```

Edge cases:

```
python3 products.py low-stock --threshold -1
python3 products.py low-stock --threshold 5 --target 2
python3 products.py --file empty.json low-stock --threshold 5
```

The first two are invalid arguments. Each prints an error to stderr and exits with code 2. The third uses a file that does not exist, so it prints `[]`, exits with 0, and does not create `empty.json`.

## Demonstration sequence

Start in a folder where `products.json` does not exist:

```
python3 products.py --help
python3 products.py add --name "Notebook" --price 4.5 --quantity 10
python3 products.py add --name "Pencil" --price 1.25 --quantity 5
python3 products.py list
python3 products.py get 1
python3 products.py update 1 --quantity 12
python3 products.py delete 2 --yes
python3 products.py list
cat products.json
```

At the end, the list contains only Notebook (`"4.50"`, quantity 12), and `next_id` in the file is 3.

Error checks:

```
python3 products.py get 99
python3 products.py add --name "Pen" --price -1 --quantity 3
python3 products.py delete 1
cat products.json
```

Separate data file:

```
python3 products.py --file demo.json add --name "Notebook" --price 4.50 --quantity 10
python3 products.py --file demo.json list
cat demo.json
```

## Tests

```
python3 -m unittest discover -s tests -v
```

The tests run the CLI as a subprocess against temporary files. They cover the required sequence, validation, exit codes, file errors, preservation of the file on failed commands, and the `low-stock` feature.

## Project structure

```
products.py            CLI entry point and all logic
tests/test_products.py End-to-end tests (standard-library unittest)
README.md
```

## Tools

The tool was built with a coding agent (Claude). Its output was reviewed and tested with the test suite above.
