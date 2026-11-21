"""Inventory guard: no raw INSERT/UPDATE/DELETE ships unexecuted.

Repository unit tests mock the DB session, so for years the raw SQL
strings in ``backend/src`` were never executed anywhere before
shipping. That blind spot let a duplicated-column INSERT break all
task creation on a real Postgres install on 2026-11-11. The
``test_sql_exec_*`` suites closed the gap statement by statement
(41 INSERT literals, 75 UPDATE statements, 19 DELETE statements --
see ``SQL_EXECUTION_COVERAGE_REPORT.md``,
``UPDATE_EXECUTION_COVERAGE_REPORT.md`` and
``DELETE_INVENTORY_REPORT.md`` in the sprint research notes),
but nothing stopped the *next* raw statement from landing uncovered.
This guard is that stop. (DELETEs joined the inventory last: they
were in neither original inventory, and one of them --
``clip_export_repository.purge_failures`` -- sat executed nowhere
until the DELETE lane.)

What "covered" means here, concretely:

- **Extraction-covered**: the ``test_sql_exec_*`` suites call
  ``sql_exec_support.extract_statement(rel_path, func_name, ...)``;
  this guard replays every such call and collects the exact statement
  texts the suites execute (the documented strict xfails are ordinary
  tests in those suites, so they are covered by construction).
- **Function-covered**: the runtime-assembled UPDATEs (SET clause
  built from a caller-supplied dict, so no fixed text exists to
  extract) are driven at method level through ``ExecSession``; the
  guard recognises the repository-method calls in the suites, plus
  the ``inspect.getsource`` pin in ``test_task_insert_columns.py``.
  A statement whose text is only an f-string *fragment* (the
  assembled kind) is covered only at this level.

The inventory itself reuses the canonical rendering from
``sql_exec_support`` (``_render`` / ``_module_constants``), so the
counts quoted by the gates -- 41 INSERT / 75 UPDATE / 19 DELETE --
are the counts this guard sees. Those counts are pinned below on
purpose: if a new statement lands *with* its execution test, bump
the pin in the same change; if the inventory ever silently breaks
(regex drift, a moved tree), the pin fails instead of the guard
passing vacuously.

Known boundary, shared with the lanes' canonical inventory: only
literals inside functions are inventoried (``extract_statement`` has
the same reach). A raw statement parked in a module-level constant
would need its own extraction support first.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from sql_exec_support import SRC_ROOT, _module_constants, _render, extract_statement

TEST_ROOT = Path(__file__).resolve().parent

CANONICAL_INSERT_COUNT = 41
CANONICAL_UPDATE_COUNT = 75
CANONICAL_DELETE_COUNT = 19

_STATEMENT_RE = re.compile(
    r"^(INSERT\s+INTO\s|UPDATE\s+[A-Za-z_]|DELETE\s+FROM\s)", re.I
)


class Statement(NamedTuple):
    rel_path: str
    func_name: str
    text: str  # whitespace-collapsed statement (or fragment) text
    fragment: bool  # True when only the constant parts of an f-string exist


# ---------------------------------------------------------------------------
# Inventory: every raw INSERT/UPDATE/DELETE literal in backend/src,
# attributed to its enclosing function. Mirrors the lanes' canonical method:
# literals in call arguments, assignments and returns; docstrings
# excluded; identical texts deduplicated per function.
# ---------------------------------------------------------------------------


def _fragment_text(node: ast.JoinedStr) -> str:
    return "".join(
        str(value.value) for value in node.values if isinstance(value, ast.Constant)
    )


def _collect_from_function(func, constants: dict[str, str], out: list[Statement], rel: str):
    docstring = None
    if (
        func.body
        and isinstance(func.body[0], ast.Expr)
        and isinstance(func.body[0].value, ast.Constant)
        and isinstance(func.body[0].value.value, str)
    ):
        docstring = func.body[0].value

    def visit(node: ast.AST, in_position: bool) -> None:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node is not func
        ):
            return  # nested scope: inventoried under its own name
        if isinstance(node, (ast.Call, ast.Assign, ast.AnnAssign, ast.Return)):
            in_position = True
        if isinstance(node, (ast.Constant, ast.JoinedStr)):
            if in_position and node is not docstring:
                text = _render(node, constants)
                fragment = False
                if text is None and isinstance(node, ast.JoinedStr):
                    text = _fragment_text(node)
                    fragment = True
                if text is not None and _STATEMENT_RE.match(text.strip()):
                    out.append(
                        Statement(rel, func.name, " ".join(text.split()), fragment)
                    )
            return  # never descend into a string node's parts
        for child in ast.iter_child_nodes(node):
            visit(child, in_position)

    for stmt in func.body:
        visit(stmt, False)


def _inventory(src_root: Path) -> list[Statement]:
    statements: list[Statement] = []
    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(src_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found: list[Statement] = []
                _collect_from_function(node, constants, found, rel)
                seen: set[str] = set()
                for statement in found:
                    if statement.text not in seen:
                        seen.add(statement.text)
                        statements.append(statement)
    return statements


# ---------------------------------------------------------------------------
# Coverage derivation: replay what the execution suites actually cover.
# ---------------------------------------------------------------------------


def _const(node: ast.AST):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(_const(element) for element in node.elts)
    raise ValueError(f"non-constant argument: {ast.dump(node)[:80]}")


def _scopes(tree: ast.Module):
    yield tree.body
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.body


def _walk_scope(body):
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # defs are their own scopes
        yield node
        stack.extend(ast.iter_child_nodes(node))


@lru_cache(maxsize=1)
def _derive_coverage() -> tuple[dict, frozenset, tuple]:
    """Return (extracted texts by (rel, func), function-covered pairs,
    replay problems). Extraction is replayed through the real
    ``extract_statement`` so the texts are byte-identical to what the
    suites execute."""
    covered_texts: dict[tuple[str, str], set[str]] = {}
    covered_funcs: set[tuple[str, str]] = set()
    problems: list[str] = []
    test_files = sorted(TEST_ROOT.glob("test_sql_exec_*.py")) + [
        TEST_ROOT / "test_task_insert_columns.py"
    ]
    for path in test_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        class_module: dict[str, str] = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("src.")
            ):
                for alias in node.names:
                    class_module[alias.asname or alias.name] = node.module

        def rel_of(cls: str) -> str:
            return class_module[cls][len("src.") :].replace(".", "/") + ".py"

        for body in _scopes(tree):
            # Instance variables are scoped per test function: the same
            # name (``repo``) is reused for different repository classes.
            var_class: dict[str, str] = {}
            for node in _walk_scope(body):
                if (
                    isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id in class_module
                ):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            var_class[target.id] = node.value.func.id
            for node in _walk_scope(body):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Name) and func.id == "extract_statement":
                    try:
                        args = [_const(arg) for arg in node.args]
                        kwargs = {kw.arg: _const(kw.value) for kw in node.keywords}
                        rel, func_name, needle = args[0], args[1], args[2]
                        occurrence = (
                            args[3] if len(args) > 3 else kwargs.get("occurrence", 0)
                        )
                        text = extract_statement(
                            rel,
                            func_name,
                            needle,
                            occurrence,
                            tuple(kwargs.get("must_contain", ())),
                            tuple(kwargs.get("must_not_contain", ())),
                        )
                        covered_texts.setdefault((rel, func_name), set()).add(
                            " ".join(text.split())
                        )
                    except Exception as exc:  # noqa: BLE001 -- recorded, asserted on
                        problems.append(f"{path.name}: extract_statement replay: {exc}")
                    continue
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    receiver = func.value.id
                    cls = receiver if receiver in class_module else var_class.get(receiver)
                    if cls:
                        covered_funcs.add((rel_of(cls), func.attr))
                        continue
                if isinstance(func, ast.Attribute) and func.attr == "getsource":
                    for arg in node.args:
                        if (
                            isinstance(arg, ast.Attribute)
                            and isinstance(arg.value, ast.Name)
                            and arg.value.id in class_module
                        ):
                            covered_funcs.add((rel_of(arg.value.id), arg.attr))
    return covered_texts, frozenset(covered_funcs), tuple(problems)


def _find_uncovered(
    src_root: Path, covered_texts: dict, covered_funcs: frozenset
) -> list[Statement]:
    uncovered: list[Statement] = []
    for statement in _inventory(src_root):
        key = (statement.rel_path, statement.func_name)
        if key in covered_funcs:
            continue
        if statement.fragment:
            uncovered.append(statement)
        elif statement.text not in covered_texts.get(key, set()):
            uncovered.append(statement)
    return uncovered


def _format_uncovered(uncovered: list[Statement]) -> str:
    lines = []
    for statement in uncovered:
        kind = (
            "runtime-assembled fragment (needs method-level coverage)"
            if statement.fragment
            else "statement"
        )
        text = statement.text
        if len(text) > 100:
            text = text[:97] + "..."
        lines.append(
            f"  {statement.rel_path} :: {statement.func_name} -- {kind}: {text}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The guard.
# ---------------------------------------------------------------------------


def test_inventory_counts_match_the_canonical_ast_inventory():
    statements = _inventory(SRC_ROOT)
    inserts = [s for s in statements if s.text.upper().startswith("INSERT")]
    updates = [s for s in statements if s.text.upper().startswith("UPDATE")]
    deletes = [s for s in statements if s.text.upper().startswith("DELETE")]
    assert len(inserts) == CANONICAL_INSERT_COUNT, (
        f"inventory finds {len(inserts)} INSERT literals, canonical count is "
        f"{CANONICAL_INSERT_COUNT}. If a statement was added together with "
        "its execution test, bump CANONICAL_INSERT_COUNT in this file in the "
        "same change; if not, the inventory method itself has drifted."
    )
    assert len(updates) == CANONICAL_UPDATE_COUNT, (
        f"inventory finds {len(updates)} UPDATE statements, canonical count "
        f"is {CANONICAL_UPDATE_COUNT}. If a statement was added together "
        "with its execution test, bump CANONICAL_UPDATE_COUNT in this file "
        "in the same change; if not, the inventory method itself has drifted."
    )
    assert len(deletes) == CANONICAL_DELETE_COUNT, (
        f"inventory finds {len(deletes)} DELETE statements, canonical count "
        f"is {CANONICAL_DELETE_COUNT}. If a statement was added together "
        "with its execution test, bump CANONICAL_DELETE_COUNT in this file "
        "in the same change; if not, the inventory method itself has drifted."
    )


def test_every_raw_insert_or_update_has_execution_coverage():
    covered_texts, covered_funcs, problems = _derive_coverage()
    assert not problems, (
        "coverage derivation failed to replay some extract_statement calls; "
        "the guard cannot see the suites' coverage, so it fails loudly "
        "instead of passing blind:\n" + "\n".join(f"  {p}" for p in problems)
    )
    uncovered = _find_uncovered(SRC_ROOT, covered_texts, covered_funcs)
    assert not uncovered, (
        f"{len(uncovered)} raw INSERT/UPDATE/DELETE statement(s) in "
        "backend/src have no statement-execution test. Add one to the "
        "test_sql_exec_* suites (extract the real text with "
        "sql_exec_support.extract_statement and execute it, or drive "
        "the method through ExecSession for a runtime-assembled "
        "statement) -- repository unit tests mock the session, so "
        "without an execution test this statement ships unexecuted, "
        "the exact hole that broke task creation on 2026-11-11:\n"
        + _format_uncovered(uncovered)
    )


# ---------------------------------------------------------------------------
# Bite proofs: the guard must catch synthetic uncovered statements in a
# temp copy of a real source file (real sources are never modified).
# ---------------------------------------------------------------------------

_PROBE_REL = "repositories/cache_repository.py"


def _temp_tree(tmp_path: Path, transform) -> Path:
    source = (SRC_ROOT / _PROBE_REL).read_text(encoding="utf-8")
    root = tmp_path / "src"
    target = root / _PROBE_REL
    target.parent.mkdir(parents=True)
    target.write_text(transform(source), encoding="utf-8")
    return root


def _guard_report(root: Path) -> str:
    covered_texts, covered_funcs, problems = _derive_coverage()
    assert not problems
    return _format_uncovered(_find_uncovered(root, covered_texts, covered_funcs))


def test_guard_reports_a_statement_in_a_brand_new_function(tmp_path):
    def inject(source: str) -> str:
        return source + (
            "\n\nasync def guard_probe_new_function(db):\n"
            "    await db.execute(\n"
            '        text("INSERT INTO guard_probe (id) VALUES (:id)"), {"id": "x"}\n'
            "    )\n"
        )

    report = _guard_report(_temp_tree(tmp_path, inject))
    assert "guard_probe_new_function" in report
    assert "INSERT INTO guard_probe" in report


def test_guard_reports_a_new_statement_inside_a_covered_function(tmp_path):
    def inject(source: str) -> str:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "upsert_cache":
                first = node.body[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    insert_at = first.end_lineno  # just past the docstring
                else:
                    insert_at = first.lineno - 1  # just before the first statement
                indent = " " * first.col_offset
                break
        else:  # pragma: no cover -- the probe file is pinned by the suites
            raise AssertionError("upsert_cache not found in probe source")
        lines = source.splitlines(keepends=True)
        probe = (
            f"{indent}await db.execute(\n"
            f'{indent}    text("INSERT INTO guard_probe (id) VALUES (:id)"),'
            ' {"id": "x"}\n'
            f"{indent})\n"
        )
        lines.insert(insert_at, probe)
        return "".join(lines)

    report = _guard_report(_temp_tree(tmp_path, inject))
    assert "upsert_cache" in report
    assert "INSERT INTO guard_probe" in report
    # ...while the function's own real statement stays covered.
    assert "INSERT INTO processing_cache" not in report


def test_guard_reports_an_uncovered_runtime_assembled_fragment(tmp_path):
    def inject(source: str) -> str:
        return source + (
            "\n\nasync def guard_probe_assembled(db, fields):\n"
            '    assignments = ", ".join(f"{key} = :{key}" for key in fields)\n'
            "    await db.execute(\n"
            '        text(f"UPDATE guard_probe SET {assignments} WHERE id = :id"), {}\n'
            "    )\n"
        )

    report = _guard_report(_temp_tree(tmp_path, inject))
    assert "guard_probe_assembled" in report
    assert "runtime-assembled fragment" in report


def test_guard_reports_an_uncovered_delete(tmp_path):
    def inject(source: str) -> str:
        return source + (
            "\n\nasync def guard_probe_delete(db):\n"
            "    await db.execute(\n"
            '        text("DELETE FROM guard_probe WHERE id = :id"), {"id": "x"}\n'
            "    )\n"
        )

    report = _guard_report(_temp_tree(tmp_path, inject))
    assert "guard_probe_delete" in report
    assert "DELETE FROM guard_probe" in report
