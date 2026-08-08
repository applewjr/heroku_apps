"""Source-level checks on templates/thread.html.

There is no JS toolchain in this project, so the game's 2,000 lines of inline
JavaScript get no linting at all. These tests stand in for it, and they catch a
specific family of bugs that reads as correct in review:

  * a constant renamed in one place and not another
  * a dropdown entry with no matching arm in the evaluator, which silently
    evaluates false forever rather than erroring
  * a preset referring to a condition code that no longer exists
  * a control the row handlers bind to that is no longer in the row markup

Companion to test_thread.py, which checks what the game *does*. This file only
checks that the source hangs together.
"""

import pathlib
import re

import pytest

esprima = pytest.importorskip(
    "esprima", reason="pip install esprima to lint the inline JS (see requirements-dev.txt)"
)

TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "templates" / "thread.html"
SRC = TEMPLATE.read_text(encoding="utf-8")
JS = re.search(r"<script>\n(.*?)\n</script>", SRC, re.S).group(1)
HTML = SRC[:SRC.index("<script>")]

# Names the browser provides. Anything used but not declared and not here is a
# typo or a rename that only got applied in one place.
BROWSER_GLOBALS = {
    "window", "document", "console", "Math", "JSON", "Date", "Array", "Object",
    "String", "Number", "Boolean", "Error", "parseInt", "parseFloat", "isNaN",
    "setTimeout", "clearTimeout", "setInterval", "clearInterval", "localStorage",
    "navigator", "location", "prompt", "confirm", "alert", "btoa", "atob",
    "escape", "unescape", "encodeURIComponent", "decodeURIComponent",
    "Uint8Array", "Uint16Array", "Int8Array", "Int32Array", "Float32Array",
    "Map", "Set", "requestAnimationFrame", "cancelAnimationFrame", "performance",
    "undefined", "NaN", "Infinity", "Promise", "arguments", "this", "history",
}


def _pairs(const_name):
    """First element of each ["code", "label"] pair in a named const array."""
    block = re.search(r"const " + const_name + r" = \[(.*?)\];", JS, re.S)
    assert block, f"could not find {const_name} in the template"
    return re.findall(r'\["([^"]+)"', block.group(1))


def _vocabulary():
    conds = {"always", ""}
    for subject in _pairs("DIR_SUBJECTS"):
        for pred in _pairs("DIR_PREDS"):
            conds.add(subject + "." + pred)
    for pred in _pairs("HERE_PREDS"):
        conds.add("here." + pred)
    for f in ("A", "B", "C"):
        conds.add("flag." + f + ".on")
        conds.add("flag." + f + ".off")

    acts = {"move." + d for d in ("ahead", "right", "left", "back")}
    acts |= {"turn." + t for t in ("right", "left", "around")}
    acts |= {"flag." + f + "." + op
             for f in ("A", "B", "C") for op in ("on", "off", "toggle")}
    return conds, acts


def _preset_block():
    return re.search(r"const PRESETS = \{(.*?)\n    \};", JS, re.S).group(1)


# --------------------------------------------------------------------------

def test_inline_js_parses():
    esprima.parseScript(JS)


def test_no_undeclared_identifiers():
    """Pools every declaration in the file rather than tracking scope, so it
    under-reports shadowing but never cries wolf about it."""
    tree = esprima.parseScript(JS, {"loc": True})
    declared, used = set(), {}

    def collect(node):
        if node is None or not hasattr(node, "type"):
            return
        if node.type == "Identifier":
            declared.add(node.name)
        elif node.type == "ObjectPattern":
            for prop in node.properties:
                collect(getattr(prop, "value", None) or getattr(prop, "argument", None))
        elif node.type == "ArrayPattern":
            for elem in node.elements:
                collect(elem)
        elif node.type in ("AssignmentPattern",):
            collect(node.left)
        elif node.type == "RestElement":
            collect(node.argument)

    DECLARERS = ("VariableDeclarator", "FunctionDeclaration", "FunctionExpression",
                 "ArrowFunctionExpression", "ClassDeclaration", "CatchClause")

    def walk(node, parent=None, key=None):
        if isinstance(node, list):
            for child in node:
                walk(child, parent, key)
            return
        if not hasattr(node, "type"):
            return

        if node.type in DECLARERS:
            collect(getattr(node, "id", None) or getattr(node, "param", None))
            for p in (getattr(node, "params", None) or []):
                collect(p)

        if node.type == "Identifier":
            is_name_not_use = (
                (parent is not None and parent.type == "MemberExpression"
                 and key == "property" and not getattr(parent, "computed", False))
                or (parent is not None and parent.type == "Property"
                    and key == "key" and not getattr(parent, "computed", False))
                or (parent is not None and parent.type in DECLARERS
                    and key in ("id", "param"))
            )
            if not is_name_not_use and node.name not in used:
                loc = getattr(node, "loc", None)
                used[node.name] = loc.start.line if loc else 0

        for field in dir(node):
            if field.startswith("_") or field in ("type", "loc", "range", "name",
                                                  "value", "raw", "regex"):
                continue
            try:
                child = getattr(node, field)
            except Exception:
                continue
            if isinstance(child, list) or hasattr(child, "type"):
                walk(child, node, field)

    walk(tree)

    missing = {n: ln for n, ln in used.items()
               if n not in declared and n not in BROWSER_GLOBALS}
    assert not missing, "used but never declared: " + ", ".join(
        f"{n} (line {ln})" for n, ln in sorted(missing.items(), key=lambda kv: kv[1])
    )


def test_every_directional_predicate_is_evaluated():
    """A predicate in the dropdown with no arm in the evaluator does not error.
    It quietly returns false for the rest of the game's life."""
    handled = set(re.findall(r'case "(\w+)":\s+return (?:trod|isOpen|!isOpen|ok)', JS))
    for pred in _pairs("DIR_PREDS"):
        assert pred in handled, f"{pred!r} is offered in the menus but never evaluated"


def test_every_here_predicate_is_evaluated():
    block = re.search(r'if \(p\[0\] === "here"\) \{(.*?)\n        \}', JS, re.S).group(1)
    for pred in _pairs("HERE_PREDS"):
        assert f'case "{pred}"' in block, f"here.{pred} is offered but never evaluated"


def test_preset_codes_all_exist():
    conds, acts = _vocabulary()
    block = _preset_block()
    found = re.findall(r'c1: "([^"]*)", c2: "([^"]*)", act: "([^"]*)"', block)
    # Without this, reformatting one preset would silently drop it from the
    # check while the others kept the test green.
    assert len(found) == block.count("{ on: true"), (
        f"parsed {len(found)} preset rules but the block declares "
        f"{block.count('{ on: true')} - the preset format changed and this "
        f"check is no longer seeing all of them"
    )
    for c1, c2, act in found:
        assert c1 in conds, f"preset uses unknown condition {c1!r}"
        assert c2 in conds, f"preset uses unknown condition {c2!r}"
        assert act in acts, f"preset uses unknown action {act!r}"


def test_every_preset_has_a_catch_all():
    """Without a rule that always matches, a program can halt on 'no rule
    matched' the moment the bot reaches a square nothing covers."""
    block = _preset_block()
    for name in re.findall(r"^\s{8}(\w+):", block, re.M):
        body = re.search(r"\b" + name + r": \[(.*?)\n        \],", block, re.S)
        if body:
            assert "always" in body.group(1), f"preset {name!r} has no catch-all rule"


def test_preset_buttons_and_presets_agree():
    names = set(re.findall(r"^\s{8}(\w+):", _preset_block(), re.M))
    buttons = set(re.findall(r'data-preset="([^"]+)"', HTML))
    assert buttons - names == set(), f"buttons with no preset: {buttons - names}"
    assert names - buttons == set(), f"presets with no button: {names - buttons}"


def test_dom_ids_the_script_reaches_for_exist():
    # whole file, not just static markup: some elements are built at runtime
    present = set(re.findall(r'id="([^"]+)"', SRC))
    for wanted in set(re.findall(r'getElementById\("([^"]+)"\)', JS)):
        assert wanted in present, f"getElementById({wanted!r}) matches no element"


def test_rule_row_markup_has_every_control_its_handlers_bind():
    row = re.search(r"el\.ruleList\.innerHTML = rules\.map\((.*?)\}\)\.join\(\"\"\);",
                    JS, re.S).group(1)
    for cls in ("c1", "c2", "act", "act2", "chk-on", "op", "up", "down", "dup",
                "del", "rule-grip"):
        assert cls in row, f"row markup has no .{cls}, but a handler binds to it"
