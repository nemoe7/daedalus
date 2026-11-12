"""Review test assertions and waits in Python and JavaScript test files."""

import ast
import configparser
import fnmatch
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from config import exclusions, file_scope, suppressed
from files import text_file, walk
from prose.extract import run_node

from rules import TEST_RULE_IDS

MEASUREMENTS = {
  "width",
  "clientwidth",
  "clientheight",
  "offsetwidth",
  "offsetheight",
  "scrollwidth",
  "scrollheight",
  "bytelength",
  "byte_length",
  "height",
  "timeout",
  "duration",
  "elapsed",
  "measure",
  "token_count",
  "byte_count",
  "size_bytes",
  "poll_wait_seconds",
  "timeout_seconds",
}


REAL_WAIT_CALLS = {"sleep", "time.sleep", "asyncio.sleep"}


CHANNELS = {"stdout", "stderr", "output", "out", "err"}


WEB_CHANNELS = {
  "stdout",
  "stderr",
  "output",
  "out",
  "err",
  "body",
  "html",
  "text",
  "data",
  "content",
  "get_data",
  "decode",
  "readouterr",
  "inner_text",
  "text_content",
  "innerHTML",
  "textContent",
}


CLIENT_CALLS = {"get", "post", "put", "patch", "delete", "open", "walk", "guest"}


BARE_CLIENT_CALLS = {"open", "walk", "guest"}


TAINT_SOURCES = (WEB_CHANNELS - CHANNELS - {"readouterr"}) | CLIENT_CALLS


ASSERT_METHODS = {"assertEqual", "assertNotEqual", "assertIn", "assertNotIn"}


NODE_TIMEOUT = 10


JS_REVIEW = r"""
let acorn;
try { acorn = require(require.resolve('acorn', {paths:[process.argv[1]]})); }
catch { console.log(JSON.stringify({reason:'parser_unavailable'})); process.exit(0); }
const text = require('node:fs').readFileSync(0, 'utf8');
let tree;
try {
  try { tree = acorn.parse(text, {ecmaVersion:'latest', sourceType:'module', locations:true}); }
  catch { tree = acorn.parse(text, {ecmaVersion:'latest', sourceType:'script', locations:true}); }
} catch (error) {
  console.log(JSON.stringify({error:'SyntaxError', line:error.loc.line, column:error.loc.column + 1}));
  process.exit(0);
}
const MEASUREMENTS = new Set(__MEASUREMENTS__);
function shape(node) {
  if (node?.type === 'Identifier') return node.name;
  if (node?.type === 'MemberExpression') {
    if (!node.computed) return `${shape(node.object)}.${node.property.name}`;
    if (node.property.type === 'Literal' && typeof node.property.value === 'string' &&
      MEASUREMENTS.has(node.property.value.toLowerCase()))
      return `${shape(node.object)}.${node.property.value}`;
  }
  if (node?.type === 'CallExpression') return `${shape(node.callee)}(${node.arguments.map(shape).join(',')})`;
  return '<expression>';
}
const CHANNELS = new Set(['stdout', 'stderr', 'output', 'out', 'err']);
const WEB_CHANNELS = new Set([
  'stdout', 'stderr', 'output', 'out', 'err',
  'body', 'html', 'text', 'data', 'content',
  'get_data', 'decode', 'readouterr',
  'inner_text', 'text_content', 'innerHTML', 'textContent',
]);
const CLIENT_CALLS = new Set(['get', 'post', 'put', 'patch', 'delete', 'open', 'walk', 'guest']);
const BARE_CLIENT_CALLS = new Set(['open', 'walk', 'guest']);
const TAINT_SOURCES = new Set([
  'body', 'html', 'text', 'data', 'content',
  'get_data', 'decode', 'inner_text', 'text_content', 'innerHTML', 'textContent',
  ...CLIENT_CALLS,
]);
const TAINT_RECEIVERS = new Set(['response', 'res', 'resp', 'page', 'result', 'proc', 'client', 'app']);
const tainted = new Set();
function isTaintRhs(name) {
  if (!name || name === '<expression>' || name.startsWith('len(')) return false;
  const parts = name.split('.').map(p => p.split('(')[0]);
  if (parts[0] === '<expression>') return false;
  if (tainted.has(parts[0]) || BARE_CLIENT_CALLS.has(parts[0])) return true;
  if (parts.length > 1 && CLIENT_CALLS.has(parts[1])) return true;
  return TAINT_RECEIVERS.has(parts[0]) && parts.slice(1).some(p => TAINT_SOURCES.has(p));
}
function collectTargets(pattern, out) {
  if (!pattern || typeof pattern !== 'object') return;
  if (pattern.type === 'Identifier') out.push(pattern.name);
  else if (pattern.type === 'ArrayPattern') pattern.elements.forEach(el => collectTargets(el, out));
  else if (pattern.type === 'ObjectPattern') {
    pattern.properties.forEach(p => collectTargets(p.value || p.argument, out));
  }
  else if (pattern.type === 'RestElement') collectTargets(pattern.argument, out);
  else if (pattern.type === 'AssignmentPattern') collectTargets(pattern.left, out);
}
function taintPass(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'VariableDeclarator' && node.init && isTaintRhs(shape(node.init))) {
    const names = [];
    collectTargets(node.id, names);
    names.forEach(n => tainted.add(n));
  } else if (node.type === 'AssignmentExpression' && isTaintRhs(shape(node.right))) {
    const names = [];
    collectTargets(node.left, names);
    names.forEach(n => tainted.add(n));
  }
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(taintPass);
    else if (value && typeof value === 'object') taintPass(value);
  }
}
for (let i = 0; i < 3; i++) taintPass(tree);
const pairs = [];
const waits = [];
let testScope = false;
const lines = text.split("\n");
function containsNode(node, predicate) {
  if (!node || typeof node !== 'object') return false;
  if (predicate(node)) return true;
  return Object.values(node).some(value => Array.isArray(value)
    ? value.some(child => containsNode(child, predicate))
    : value && typeof value === 'object' && containsNode(value, predicate));
}
function rootCallName(node) {
  return node.type === 'CallExpression' ? shape(node.callee).split('.')[0] : '';
}
const fakeTimerMethods = new Set([
  'useFakeTimers',
  'advanceTimersByTime',
  'advanceTimersToNextTimer',
  'runAllTimers',
  'runOnlyPendingTimers',
  'clearAllTimers',
]);
function fakeTimerControl(node) {
  if (node.type !== 'CallExpression') return false;
  const name = shape(node.callee);
  return fakeTimerMethods.has(name.split('.').pop()) ||
    /^(?:sinon|clock)\.(?:install|tick)$/.test(name);
}
function hasTestDeclaration(node) {
  return containsNode(node, child => ['test','it','specify'].includes(rootCallName(child)));
}
function hasSuiteFakeTimerControl(node) {
  if (!node || typeof node !== 'object') return false;
  if (node.type === 'CallExpression' &&
    ['test','it','specify','describe','suite'].includes(rootCallName(node))) return false;
  if (fakeTimerControl(node)) return true;
  return Object.values(node).some(value => Array.isArray(value)
    ? value.some(child => hasSuiteFakeTimerControl(child))
    : value && typeof value === 'object' && hasSuiteFakeTimerControl(value));
}
const fakeTimerScopes = [];
function collectFakeTimerScopes(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'CallExpression') {
    const root = rootCallName(node);
    const callbacks = node.arguments.filter(arg =>
      ['FunctionExpression','ArrowFunctionExpression'].includes(arg.type));
    if (['test','it','specify'].includes(root)) {
      callbacks.forEach(callback => {
        if (containsNode(callback.body, fakeTimerControl)) {
          fakeTimerScopes.push({start:callback.start, end:callback.end});
        }
      });
    } else if (['describe','suite'].includes(root)) {
      callbacks.forEach(callback => {
        if (hasSuiteFakeTimerControl(callback.body)) {
          fakeTimerScopes.push({start:callback.start, end:callback.end});
        }
      });
    }
  }
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(child => collectFakeTimerScopes(child));
    else if (value && typeof value === 'object') collectFakeTimerScopes(value);
  }
}
collectFakeTimerScopes(tree);
const moduleFakeTimers = tree.body.some(statement =>
  !hasTestDeclaration(statement) && containsNode(statement, fakeTimerControl));
function fakeTimerControlled(node) {
  return moduleFakeTimers || fakeTimerScopes.some(scope =>
    scope.start <= node.start && node.end <= scope.end);
}
function timerCall(node) {
  if (node?.type !== 'CallExpression') return false;
  return ['sleep','timersPromises.setTimeout','timersPromises.sleep'].includes(shape(node.callee));
}
function promiseTimer(node) {
  if (node?.type !== 'NewExpression' || shape(node.callee) !== 'Promise') return null;
  let found = null;
  containsNode(node, child => {
    if (!found && child.type === 'CallExpression' && shape(child.callee) === 'setTimeout') {
      found = child;
    }
    return Boolean(found);
  });
  return found;
}
function awaitedTimer(node) {
  if (node.type !== 'AwaitExpression') return null;
  if (timerCall(node.argument)) return node.argument;
  return promiseTimer(node.argument);
}
function boundedPoll(node) {
  if (node.type !== 'WhileStatement') return false;
  const condition = text.slice(node.test.start, node.test.end);
  return /(?:Date\.now|performance\.now|(?:time\.)?(?:monotonic|perf_counter))\s*\(/.test(condition) &&
    /\b(?:deadline|timeout|timeout_at|end_time|expires_at)\b/i.test(condition);
}
const LOCAL_SCOPE = Symbol('local test scope');
const testCallbacks = new WeakSet();
function pair(left, right, node, operator, explicit) {
  for (let [actual, expected] of [[left,right],[right,left]]) {
    if (expected?.type === 'Identifier' && explicit.get(LOCAL_SCOPE) &&
        typeof explicit.get(expected.name) === 'string')
      expected = {type:'Literal', value:explicit.get(expected.name)};
    if (expected?.type === 'Literal' && ['string','number'].includes(typeof expected.value)) {
      const column = Array.from(lines[node.loc.start.line - 1].slice(0, node.loc.start.column)).length + 1;
      const actShape = shape(actual);
      pairs.push({actual:actShape, value:expected.value, operator, lang:'js',
        explicit_input:explicit.has(actShape) && explicit.get(actShape) === expected.value,
        web_taint:isTaintRhs(actShape), tainted:[...tainted],
        line:node.loc.start.line, column});
    }
  }
}
function checkIncludes(expr, node, explicit) {
  if (expr?.type === 'CallExpression' && expr.callee?.type === 'MemberExpression' &&
    expr.callee.property?.name === 'includes') {
    pair(expr.callee.object, expr.arguments[0], node, 'membership', explicit);
  }
}
function recordInput(name, value, explicit) {
  if (value?.type === 'Identifier' && explicit.get(LOCAL_SCOPE) &&
      typeof explicit.get(value.name) === 'string')
    value = {type:'Literal', value:explicit.get(value.name)};
  for (const key of explicit.keys()) {
    if (typeof key === 'string' && (key === name || key.startsWith(name + '.'))) explicit.delete(key);
  }
  if (value?.type === 'Literal' && ['string','number'].includes(typeof value.value))
    explicit.set(name, value.value);
}
function visit(node, isBoundedPoll=false, explicit=new Map()) {
  if (!node || typeof node !== 'object') return;
  if (['FunctionDeclaration','FunctionExpression','ArrowFunctionExpression'].includes(node.type))
    explicit = new Map([[LOCAL_SCOPE, testCallbacks.has(node) || /^test_/.test(node.id?.name || '')]]);
  if (node.type === 'IfStatement') {
    visit(node.test, isBoundedPoll, new Map(explicit));
    visit(node.consequent, isBoundedPoll, new Map(explicit));
    visit(node.alternate, isBoundedPoll, new Map(explicit));
    explicit.clear();
    return;
  }
  if (['ForStatement','ForOfStatement','ForInStatement','WhileStatement','DoWhileStatement',
    'TryStatement','SwitchStatement','SwitchCase'].includes(node.type)) {
    explicit.clear();
    explicit = new Map();
  }
  if (node.type === 'VariableDeclarator') recordInput(shape(node.id), node.init, explicit);
  if (node.type === 'AssignmentExpression')
    recordInput(shape(node.left), node.operator === '=' ? node.right : null, explicit);
  if (node.type === 'UpdateExpression') recordInput(shape(node.argument), null, explicit);
  if (node.type === 'ExpressionStatement' &&
      (node.expression?.type === 'CallExpression' ||
       (node.expression?.type === 'AwaitExpression' && node.expression.argument?.type === 'CallExpression'))) {
    const call = node.expression.type === 'AwaitExpression' ? node.expression.argument : node.expression;
    const callName = shape(call.callee);
    if (!callName.startsWith('assert') && !callName.startsWith('expect')) {
      const used = new Set();
      function inputNames(item) {
        if (!item || typeof item !== 'object') return;
        if (item.type === 'Identifier' && typeof explicit.get(item.name) === 'string')
          used.add(explicit.get(item.name));
        for (const value of Object.values(item)) {
          if (Array.isArray(value)) value.forEach(inputNames);
          else if (value && typeof value === 'object') inputNames(value);
        }
      }
      call.arguments.forEach(inputNames);
      for (const [key, value] of explicit) if (typeof key === 'string' && used.has(value)) explicit.delete(key);
    }
    const setter = /^(.*\.)?set_([A-Za-z_][A-Za-z_0-9]*)$/.exec(shape(call.callee));
    if (setter && call.arguments.length === 1)
      recordInput((setter[1] || '') + setter[2], call.arguments[0], explicit);
  }
  if (boundedPoll(node)) isBoundedPoll = true;
  if (node.type === 'AwaitExpression') {
    const delay = awaitedTimer(node);
    if (delay && !isBoundedPoll && !fakeTimerControlled(delay)) {
      const column = Array.from(lines[delay.loc.start.line - 1].slice(0, delay.loc.start.column)).length + 1;
      waits.push({actual:shape(delay.callee), line:delay.loc.start.line, column});
    }
  }
  if (node.type === 'ImportDeclaration' && node.source.value === 'node:test') testScope = true;
  if (node.type === 'CallExpression') {
    const name = shape(node.callee);
    if (name === 'require' && node.arguments[0]?.value === 'node:test') testScope = true;
    if (['test','it','describe','suite'].includes(name)) {
      for (const arg of node.arguments) {
        if (['FunctionExpression','ArrowFunctionExpression'].includes(arg.type)) {
          testScope = true;
          testCallbacks.add(arg);
        }
      }
    }
    if (['assert.equal','assert.strictEqual','assert.deepEqual',
      'assert.deepStrictEqual','assert.notEqual','assert.notStrictEqual'].includes(name)) {
      pair(node.arguments[0], node.arguments[1], node, 'equality', explicit);
      checkIncludes(node.arguments[0], node, explicit);
      checkIncludes(node.arguments[1], node, explicit);
    } else if (node.callee?.type === 'MemberExpression' &&
      node.callee.object?.type === 'CallExpression' &&
      shape(node.callee.object.callee) === 'expect') {
      const actual = node.callee.object.arguments[0];
      if (node.callee.property.name === 'toBe') {
        pair(actual, node.arguments[0], node, 'equality', explicit);
      } else if (node.callee.property.name === 'toHaveCSS' &&
        typeof node.arguments[0]?.value === 'string') {
        pair({type:'MemberExpression', object:actual,
          property:{type:'Identifier', name:node.arguments[0].value}, computed:false},
          node.arguments[1], node, 'equality', explicit);
      }
    } else if (name === 'assert' || name === 'assert.ok') {
      const expression = node.arguments[0];
      if (expression?.type === 'BinaryExpression' && ['==','===','!=','!=='].includes(expression.operator)) {
        pair(expression.left, expression.right, node, 'equality', explicit);
      } else {
        checkIncludes(expression, node, explicit);
      }
    }
  }
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(child => visit(child, isBoundedPoll, explicit));
    else if (value && typeof value === 'object') visit(value, isBoundedPoll, explicit);
  }
}
visit(tree);
console.log(JSON.stringify({pairs, waits, test_scope:testScope}));
""".replace("__MEASUREMENTS__", json.dumps(sorted(MEASUREMENTS)))


def shape(node):
  if isinstance(node, ast.Name):
    return node.id
  if isinstance(node, ast.Attribute):
    return f"{shape(node.value)}.{node.attr}"
  if (
    isinstance(node, ast.Subscript)
    and isinstance(node.slice, ast.Constant)
    and isinstance(node.slice.value, str)
    and node.slice.value.lower() in MEASUREMENTS
  ):
    return f"{shape(node.value)}.{node.slice.value}"
  if isinstance(node, ast.Call):
    return f"{shape(node.func)}({','.join(map(shape, node.args))})"
  return "<expression>"


def _is_taint_rhs(name, tainted=frozenset()):
  if not name or name == "<expression>" or name.startswith("len("):
    return False
  parts = [part.split("(")[0] for part in name.split(".")]
  if parts[0] == "<expression>":
    return False
  if parts[0] in tainted or parts[0] in BARE_CLIENT_CALLS:
    return True
  return len(parts) > 1 and any(part in TAINT_SOURCES for part in parts[1:])


def _iter_scope_nodes(scope_node):
  for child in ast.iter_child_nodes(scope_node):
    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
      continue
    yield child
    yield from _iter_scope_nodes(child)


def _collect_scope_taint(scope_node):
  tainted = set()
  nodes = list(_iter_scope_nodes(scope_node))
  for _ in range(3):
    changed = False
    for node in nodes:
      if isinstance(node, ast.Assign) and _is_taint_rhs(shape(node.value), tainted):
        for target in node.targets:
          for sub in ast.walk(target):
            if isinstance(sub, ast.Name) and sub.id not in tainted:
              tainted.add(sub.id)
              changed = True
      elif (
        isinstance(node, ast.AnnAssign)
        and node.value is not None
        and _is_taint_rhs(shape(node.value), tainted)
      ):
        for sub in ast.walk(node.target):
          if isinstance(sub, ast.Name) and sub.id not in tainted:
            tainted.add(sub.id)
            changed = True
    if not changed:
      break
  return tainted


def extract_prose_candidate(raw):
  """Extract a plain prose candidate from str or bytes after stripping HTML tags."""
  text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
  if not isinstance(text, str) or "{" in text or "}" in text or "\n" in text:
    return None
  has_html = bool(
    re.search(r"</[A-Za-z][A-Za-z0-9-]*>|<[A-Za-z][A-Za-z0-9-]*\s+[^>]*>", text)
  )
  stripped = re.sub(r"<[^>]+>", " ", text).strip() if has_html else text.strip()
  if (
    not stripped
    or re.search(r"[<>{}=\\/;]", stripped)
    or stripped.startswith((".", "#", "@", "/", "http://", "https://"))
  ):
    return None
  words = re.findall(r"(?<![A-Za-z0-9_-])[A-Za-z']+(?![A-Za-z0-9_-])", stripped)
  if len(words) >= 3 and not stripped.isupper() and re.search(r"\s", stripped):
    return stripped
  if (
    len(words) >= 2
    and stripped[-1] in ".?!"
    and stripped[0].isupper()
    and not stripped.isupper()
  ):
    return stripped
  return None


def _has_bounded_poll_limit(condition):
  names = {
    node.id.lower() for node in ast.walk(condition) if isinstance(node, ast.Name)
  }
  clocks = {
    shape(node.func).lower()
    for node in ast.walk(condition)
    if isinstance(node, ast.Call)
  }
  return bool(
    clocks
    & {"time.monotonic", "time.perf_counter", "time.time", "monotonic", "perf_counter"}
  ) and bool(names & {"deadline", "timeout", "timeout_at", "end_time", "expires_at"})


def _patches_real_wait(node):
  if not isinstance(node, ast.Call):
    return False
  name = shape(node.func)
  if not name.endswith(("patch", "patch.object", "setattr")):
    return False
  targets = {
    child.value
    for child in ast.walk(node)
    if isinstance(child, ast.Constant) and isinstance(child.value, str)
  }
  if any(target in REAL_WAIT_CALLS or target.endswith(".sleep") for target in targets):
    return True
  return (
    name.endswith(("patch.object", "setattr"))
    and len(node.args) >= 2
    and shape(node.args[0]) in {"time", "asyncio"}
    and isinstance(node.args[1], ast.Constant)
    and node.args[1].value == "sleep"
  )


def _python_waits(tree, lines):
  parents = {
    child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
  }

  def enclosing(node, types):
    parent = parents.get(node)
    while parent:
      if isinstance(parent, types):
        return parent
      parent = parents.get(parent)
    return None

  waits = []
  for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
      continue
    actual = shape(node.func)
    if actual not in REAL_WAIT_CALLS:
      continue
    if actual == "asyncio.sleep" and not enclosing(node, ast.Await):
      continue
    ancestor = parents.get(node)
    bounded = False
    fake_timed = False
    while ancestor:
      if isinstance(ancestor, ast.While) and _has_bounded_poll_limit(ancestor.test):
        bounded = True
      if isinstance(ancestor, (ast.With, ast.AsyncWith)) and any(
        _patches_real_wait(item.context_expr) for item in ancestor.items
      ):
        fake_timed = True
      if isinstance(
        ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
      ) and any(_patches_real_wait(candidate) for candidate in ast.walk(ancestor)):
        fake_timed = True
      ancestor = parents.get(ancestor)
    if bounded or fake_timed:
      continue
    waits.append(
      {
        "actual": actual,
        "line": node.lineno,
        "column": len(
          lines[node.lineno - 1].encode("utf-8")[: node.col_offset].decode("utf-8")
        )
        + 1,
      }
    )
  return waits


def python_pairs(text):
  pairs = []
  lines = text.splitlines()
  tree = ast.parse(text)
  module_tainted = _collect_scope_taint(tree)
  func_taint = {
    id(node): _collect_scope_taint(node)
    for node in ast.walk(tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
  }

  local_scope = object()

  def pair(left, right, node, operator, tainted, explicit):
    for actual, expected in ((left, right), (right, left)):
      if isinstance(expected, ast.Name) and explicit.get(local_scope):
        value = explicit.get(expected.id)
        if isinstance(value, (str, bytes)):
          expected = ast.Constant(value=value)
      if isinstance(expected, ast.Constant) and type(expected.value) in (
        str,
        bytes,
        int,
        float,
      ):
        act_shape = shape(actual)
        raw_val = expected.value
        val = (
          raw_val.decode("utf-8", "ignore") if isinstance(raw_val, bytes) else raw_val
        )
        pairs.append(
          {
            "actual": act_shape,
            "explicit_input": act_shape in explicit and explicit[act_shape] == raw_val,
            "value": val,
            "raw_value": raw_val,
            "tainted": sorted(tainted),
            "web_taint": _is_taint_rhs(act_shape, tainted)
            or isinstance(raw_val, bytes),
            "operator": operator,
            "line": node.lineno,
            "column": len(
              lines[node.lineno - 1].encode("utf-8")[: node.col_offset].decode("utf-8")
            )
            + 1,
          }
        )

  def record_input(node, explicit):
    targets, value = [], None
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
      targets = node.targets if isinstance(node, ast.Assign) else [node.target]
      value = None if isinstance(node, ast.AugAssign) else node.value
    elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
      call = node.value
      setter = shape(call.func).rsplit(".", 1)
      if setter[-1].startswith("set_") and len(call.args) == 1:
        target = setter[-1][4:]
        if len(setter) > 1:
          target = setter[0] + "." + target
        targets = [target]
        value = call.args[0]
    if isinstance(value, ast.Name) and explicit.get(local_scope):
      resolved = explicit.get(value.id)
      value = (
        ast.Constant(value=resolved) if isinstance(resolved, (str, bytes)) else value
      )
    for target in targets:
      name = target if isinstance(target, str) else shape(target)
      for key in list(explicit):
        if isinstance(key, str) and (key == name or key.startswith(name + ".")):
          explicit.pop(key)
      if isinstance(value, ast.Constant) and type(value.value) is not bool:
        explicit[name] = value.value

  def walk_scope(scope_node, active_taint, explicit=None):
    if explicit is None:
      explicit = {}
    for child in ast.iter_child_nodes(scope_node):
      if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
        walk_scope(
          child,
          active_taint | func_taint.get(id(child), set()),
          {local_scope: child.name.startswith("test_")},
        )
        continue
      if isinstance(
        child,
        (
          ast.If,
          ast.For,
          ast.AsyncFor,
          ast.While,
          ast.Try,
          ast.With,
          ast.AsyncWith,
          ast.Match,
        ),
      ):
        incoming = dict(explicit) if isinstance(child, ast.If) else {}
        for _, branch in ast.iter_fields(child):
          if isinstance(branch, list):
            statements = [item for item in branch if isinstance(item, ast.stmt)]
            if statements:
              walk_scope(
                ast.Module(body=statements, type_ignores=[]),
                active_taint,
                dict(incoming),
              )
            for handler in branch:
              if isinstance(handler, (ast.ExceptHandler, ast.match_case)):
                walk_scope(handler, active_taint, {})
        explicit.clear()
        continue
      expression = getattr(child, "value", None)
      if isinstance(expression, ast.Await):
        expression = expression.value
      if isinstance(child, (ast.Expr, ast.Assign, ast.AnnAssign)) and isinstance(
        expression, ast.Call
      ):
        call = expression
        if not shape(call.func).rsplit(".", 1)[-1].startswith("assert"):
          used = {
            explicit[name.id]
            for arg in [*call.args, *(kw.value for kw in call.keywords)]
            for name in ast.walk(arg)
            if isinstance(name, ast.Name)
            and isinstance(explicit.get(name.id), (str, bytes))
          }
          for key in list(explicit):
            if isinstance(key, str) and explicit[key] in used:
              explicit.pop(key)
      record_input(child, explicit)
      if isinstance(child, ast.Assert):
        for expression in ast.walk(child.test):
          if isinstance(expression, ast.Compare):
            left = expression.left
            for operator, right in zip(expression.ops, expression.comparators):
              if isinstance(operator, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
                pair(
                  left,
                  right,
                  expression,
                  "equality"
                  if isinstance(operator, (ast.Eq, ast.NotEq))
                  else "membership",
                  active_taint,
                  explicit,
                )
              left = right
      if (
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and shape(child.func.value) in ("self", "cls")
        and child.func.attr in ASSERT_METHODS
        and len(child.args) >= 2
      ):
        pair(
          child.args[0],
          child.args[1],
          child,
          "membership"
          if child.func.attr in {"assertIn", "assertNotIn"}
          else "equality",
          active_taint,
          explicit,
        )
      walk_scope(child, active_taint, explicit)

  walk_scope(tree, module_tainted)
  return {"pairs": pairs, "waits": _python_waits(tree, lines)}


def output_channel(name, tainted=(), lang="py", web_taint=False):
  if not name or name == "<expression>" or name.startswith("len("):
    return False
  taint_set = set(tainted)
  parts = [part.split("(")[0] for part in name.split(".")]
  if parts[0] == "<expression>":
    return False
  if parts[0] in taint_set or parts[0] in CHANNELS or parts[0] in BARE_CLIENT_CALLS:
    return True
  if lang == "js" and not web_taint:
    return any(part in CHANNELS for part in parts)
  return len(parts) > 1 and any(
    part in (WEB_CHANNELS | CLIENT_CALLS) for part in parts[1:]
  )


def category(pair):
  value, actual = pair["value"], pair["actual"]
  raw_val = pair.get("raw_value", value)
  tainted = pair.get("tainted", ())
  lang = pair.get("lang", "py")
  web_taint = bool(pair.get("web_taint"))
  if isinstance(raw_val, (str, bytes)) and output_channel(
    actual, tainted, lang=lang, web_taint=web_taint
  ):
    candidate = extract_prose_candidate(raw_val)
    if candidate is not None:
      words = re.findall(r"(?<![A-Za-z0-9_-])[A-Za-z']+(?![A-Za-z0-9_-])", candidate)
      if web_taint or len(words) >= 3:
        return "output_text"
  measurement_value = value
  if isinstance(value, str):
    pixels = re.fullmatch(r"([+]?(?:\d+(?:\.\d*)?|\.\d+))px", value)
    measurement_value = float(pixels[1]) if pixels else None
  if (
    type(measurement_value) in (int, float)
    and measurement_value > 0
    and pair["operator"] == "equality"
    and not pair.get("explicit_input")
    and (
      actual.split("(")[0].split(".")[-1].lower() in MEASUREMENTS
      or actual.rsplit(".", 1)[-1].split("(")[0].lower() in MEASUREMENTS
      or actual.startswith("px(")
      or actual.startswith("len(")
      and any(word in CHANNELS for word in re.findall(r"\w+", actual))
    )
  ):
    return "measurement"
  return None


def javascript_pairs(text):
  """Parse JavaScript assertions and test declarations through the bundled resolver."""
  if not shutil.which("node"):
    return {"reason": "parser_unavailable"}
  returncode, stdout = run_node(
    ["-e", JS_REVIEW, str(Path(__file__).resolve().parent)],
    text,
    timeout=NODE_TIMEOUT,
    check=False,
  )
  if returncode:
    raise RuntimeError("parser_process_failed")
  return json.loads(stdout)


def numeric_setting_findings(path, text):
  """Review direct numeric copies of statically resolved production settings."""
  tree = ast.parse(text)
  root = next(
    (
      parent
      for parent in path.resolve().parents
      if (parent / ".git").exists() or (parent / "pyproject.toml").exists()
    ),
    path.resolve().parent,
  )
  bindings = {}
  sources = {}

  def source(module, level=0):
    parts = module.split(".") if module else []
    base = path.resolve().parent
    if level:
      for _ in range(level - 1):
        base = base.parent
      candidates = [base.joinpath(*parts).with_suffix(".py")]
    else:
      candidates = [
        parent.joinpath(*parts).with_suffix(".py")
        for parent in path.resolve().parents
        if parent.is_relative_to(root)
      ]
    found = {
      candidate.resolve()
      for candidate in candidates
      if candidate.is_file()
      and candidate.resolve().is_relative_to(root)
      and not any(
        part in {"tests", "test", ".venv"} for part in candidate.relative_to(root).parts
      )
    }
    return next(iter(found)) if len(found) == 1 else None

  for node in tree.body:
    if isinstance(node, ast.Import):
      for alias in node.names:
        if resolved := source(alias.name):
          bindings[alias.asname or alias.name] = (resolved, None)
    elif isinstance(node, ast.ImportFrom) and (
      resolved := source(node.module, node.level)
    ):
      for alias in node.names:
        bindings[alias.asname or alias.name] = (resolved, alias.name)
  for node in tree.body:
    if isinstance(
      node,
      (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
    ):
      continue
    for child in ast.walk(node):
      if isinstance(child, ast.Name) and child.id in bindings:
        bindings.pop(child.id)
  for resolved, _ in bindings.values():
    if resolved in sources:
      continue
    try:
      parsed = ast.parse(resolved.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
      sources[resolved] = {}
      continue
    settings = {}
    writes = {}
    for node in ast.walk(parsed):
      if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        writes[node.id] = writes.get(node.id, 0) + 1
    for node in parsed.body:
      targets = (
        node.targets
        if isinstance(node, ast.Assign)
        else ([node.target] if isinstance(node, ast.AnnAssign) else [])
      )
      for target in targets:
        if not isinstance(target, ast.Name) or not target.id.isupper():
          continue
        if re.search(
          r"(?:SCHEMA|STATUS|EXIT|PERMISSION|FRAME|DIGEST|MODE|INDEX|VERSION)",
          target.id,
        ):
          continue
        value = getattr(node, "value", None)
        if isinstance(value, ast.UnaryOp) and isinstance(
          value.op, (ast.USub, ast.UAdd)
        ):
          value = value.operand
        if (
          isinstance(value, ast.Constant)
          and type(value.value) in (int, float)
          and writes[target.id] == 1
        ):
          settings[target.id] = node.lineno
    sources[resolved] = settings
  findings = []
  for function in ast.walk(tree):
    if not isinstance(
      function, (ast.FunctionDef, ast.AsyncFunctionDef)
    ) or not function.name.startswith("test_"):
      continue
    if (
      function.args.args
      or function.args.posonlyargs
      or function.args.kwonlyargs
      or function.args.vararg
      or function.args.kwarg
      or function.decorator_list
    ):
      continue
    nodes = list(_iter_scope_nodes(function))
    shadowed = {
      node.id
      for node in nodes
      if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    }
    shadowed.update(
      arg.arg for arg in ast.walk(function.args) if isinstance(arg, ast.arg)
    )
    for node in nodes:
      if isinstance(node, (ast.Import, ast.ImportFrom)):
        shadowed.update(
          alias.asname or alias.name.split(".")[0] for alias in node.names
        )
      if isinstance(node, ast.Call):
        for argument in [*node.args, *(kw.value for kw in node.keywords)]:
          shadowed.update(
            child.id for child in ast.walk(argument) if isinstance(child, ast.Name)
          )
        if isinstance(node.func, ast.Attribute):
          shadowed.update(
            child.id
            for child in ast.walk(node.func.value)
            if isinstance(child, ast.Name)
          )
      if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
        shadowed.update(
          child.id for child in ast.walk(node.value) if isinstance(child, ast.Name)
        )
    for node in nodes:
      if not isinstance(node, ast.Assert) or not isinstance(node.test, ast.Compare):
        continue
      comparison = node.test
      if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
        continue
      for actual, expected in (
        (comparison.left, comparison.comparators[0]),
        (comparison.comparators[0], comparison.left),
      ):
        literal = (
          expected.operand
          if isinstance(expected, ast.UnaryOp)
          and isinstance(expected.op, (ast.USub, ast.UAdd))
          else expected
        )
        if not isinstance(literal, ast.Constant) or type(literal.value) not in (
          int,
          float,
        ):
          continue
        name = (
          actual.id
          if isinstance(actual, ast.Name)
          else (
            actual.value.id
            if isinstance(actual, ast.Attribute) and isinstance(actual.value, ast.Name)
            else None
          )
        )
        if name not in bindings or name in shadowed:
          continue
        resolved, imported = bindings[name]
        setting = (
          imported
          if isinstance(actual, ast.Name)
          else actual.attr
          if imported is None
          else None
        )
        line = sources[resolved].get(setting)
        if line is None:
          continue
        findings.append(
          {
            "rule": TEST_RULE_IDS["numeric_setting"],
            "category": "numeric_setting",
            "path": str(path),
            "line": node.lineno,
            "column": node.col_offset + 1,
            "source_path": str(resolved),
            "source_line": line,
            "evidence": f"copied numeric setting {setting} from {resolved}:{line}",
            "status": "manual_review",
          }
        )
  return findings


def review_tests(targets):
  findings, coverage, errors, visited = [], [], [], set()
  for path in walk(targets, []):
    if path.resolve() in visited:
      continue
    visited.add(path.resolve())
    row = {"path": str(path), "status": "not_checked"}
    coverage.append(row)
    text = text_file(path)
    if text is None:
      row["reason"] = "unreadable_or_non_text"
      continue
    try:
      if path.suffix.lower() == ".py":
        parsed = python_pairs(text)
        row["parser"] = "python_ast"
      elif path.suffix.lower() in {".js", ".cjs", ".mjs"}:
        parsed = javascript_pairs(text)
        row["parser"] = "acorn"
      else:
        row["reason"] = "unsupported_language"
        continue
    except SyntaxError as error:
      parsed = {"error": "SyntaxError", "line": error.lineno, "column": error.offset}
    except (
      OSError,
      subprocess.TimeoutExpired,
      ValueError,
      RuntimeError,
      RecursionError,
    ) as error:
      parsed = {"error": type(error).__name__}
    if "reason" in parsed:
      row["reason"] = parsed["reason"]
      continue
    if "error" in parsed:
      row["reason"] = "parse_error"
      errors.append(
        {
          "path": str(path),
          "type": parsed["error"],
          "line": parsed.get("line"),
          "column": parsed.get("column"),
        }
      )
      continue
    row["status"] = "checked"
    with file_scope(path, text):
      file_ids, line_ids, _ = exclusions(path, text.splitlines())
    if path.suffix.lower() == ".py":
      for finding in numeric_setting_findings(path, text):
        if not suppressed(finding["rule"], file_ids | line_ids[finding["line"] - 1]):
          findings.append(finding)
    for pair in parsed["pairs"]:
      role = category(pair)
      if role is None:
        continue
      code = TEST_RULE_IDS[role]
      if suppressed(code, file_ids | line_ids[pair["line"] - 1]):
        continue
      findings.append(
        {
          "rule": code,
          "category": role,
          "path": str(path),
          "line": pair["line"],
          "column": pair["column"],
          "evidence": f"{pair['actual']} {pair['operator']} <literal>",
          "status": "review",
        }
      )
    for wait_finding in parsed.get("waits", ()):
      code = TEST_RULE_IDS["timing_wait"]
      if suppressed(code, file_ids | line_ids[wait_finding["line"] - 1]):
        continue
      findings.append(
        {
          "rule": code,
          "category": "timing_wait",
          "path": str(path),
          "line": wait_finding["line"],
          "column": wait_finding["column"],
          "evidence": f"unconditional wait via {wait_finding['actual']}",
          "status": "review",
        }
      )
  return {"findings": findings, "coverage": coverage, "errors": errors}


def is_test_file(path, text, helpers):
  """Recognize test paths and declarations without running supplied code."""
  if path.resolve() in helpers:
    return True
  suffix = path.suffix.lower()
  if suffix not in {".py", ".js", ".cjs", ".mjs"}:
    return False
  if any(
    fnmatch.fnmatchcase(path.name, pattern)
    for pattern in (
      "test_*.py",
      "*_test.py",
      "test_*.js",
      "test_*.cjs",
      "test_*.mjs",
      "*.test.*",
      "*.spec.*",
      "*.checks.cjs",
      "conftest.py",
    )
  ) or any(part in {"test", "tests"} for part in path.parts[:-1]):
    return True
  if suffix == ".py":
    for parent in path.resolve().parents:
      config = parent / "pytest.ini"
      pyproject = parent / "pyproject.toml"
      patterns, roots = None, None
      if config.is_file():
        settings = configparser.ConfigParser(interpolation=None)
        settings.read(config, encoding="utf-8")
        patterns = settings.get("pytest", "python_files", fallback="test_*.py").split()
        roots = settings.get("pytest", "testpaths", fallback=".").split()
      elif pyproject.is_file():
        raw = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        ini = raw.get("tool", {}).get("pytest", {}).get("ini_options")
        if isinstance(ini, dict):
          pf = ini.get("python_files", ["test_*.py"])
          tp = ini.get("testpaths", ["."])
          patterns = [pf] if isinstance(pf, str) else list(pf)
          roots = [tp] if isinstance(tp, str) else list(tp)
      if patterns is not None and roots is not None:
        if any(fnmatch.fnmatchcase(path.name, pattern) for pattern in patterns) and any(
          path.resolve().is_relative_to((parent / root).resolve()) for root in roots
        ):
          return True
        break
    return any(
      isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
      and node.name.startswith("test_")
      or isinstance(node, ast.ClassDef)
      and any(ast.unparse(base).endswith("TestCase") for base in node.bases)
      for node in ast.walk(ast.parse(text))
    )
  parsed = javascript_pairs(text)
  if "reason" in parsed or "error" in parsed:
    raise ValueError(parsed.get("reason") or parsed["error"])
  return parsed["test_scope"]
