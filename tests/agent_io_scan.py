"""Static scan: file I/O whose path can come from an agent-writable root.

Used by tests/test_agent_writable_io_guard.py. Pure AST: nothing scanned is imported.

Agent-writable roots are directories the tenant's agent (openclaw container,
read-write mounts) or the terminal's sandbox user can write: uploads, canvas
pages (+ _data, stories, styles), music, generated_music, transcripts,
captures, the workspace mounts (Agent = the agent's own workspace), the code
workspace, and /tmp. A path that DERIVES from one of them (assignment, call
argument, helper return value, generator yield, lambda, loop variable,
comprehension; across modules through imports; through methods of other
objects) and reaches an I/O sink BY NAME follows whatever symlink the agent
planted. Each such sink must go through services/safe_open.py (no-follow,
beneath-root, fd-based) or be allowlisted with a reason.

Two taint labels (review 3 of PR #597 widened the engine):
  root  derives from an agent-writable root: a ROOT_NAMES identifier, a
        '/app/runtime/<root>' or '/tmp' string, `<x> / 'uploads'`,
        os.path.join(..., 'uploads'), f'{RUNTIME_DIR}/uploads/...', or an
        Anchored (safe_open) handle laundered back into a plain path
        (`a.path`, `str(a)`, `os.fspath(a)`, `open(a)`, f'{a}').
  req   the HEAD of the path is request-supplied (flask `request.*`, the JSON
        body, a route's URL parameter): `Path(request.json['p']).resolve()`
        is wherever the caller says, including into uploads/. A request value
        joined UNDER a fixed root (`ROOT / name`) is judged by the root.

Sinks are resolved through the import table, so `import os as _os`,
`from shutil import copy`, `from PIL import Image` and `getattr(os, 'x')` are
the functions they name. Anything the engine cannot resolve FAILS CLOSED: a
tainted argument passed to a dynamic callee (`table[k](p)`, `getattr(o, name)(p)`,
`f()(p)`) or alongside a sink passed as a value (`map(open, paths)`) is a
finding that needs an allowlist reason.

The analysis over-approximates on purpose: a false positive costs one
allowlist line with a reason; a false negative is a hole.
"""

import ast
import re
from pathlib import Path

# Identifiers that ARE agent-writable roots (wherever they are imported from).
ROOT_NAMES = {
    'UPLOADS_DIR', 'CANVAS_PAGES_DIR', 'CANVAS_DIR', 'MUSIC_DIR', 'GENERATED_MUSIC_DIR',
    'TRANSCRIPTS_DIR', '_TRANSCRIPTS_DIR_PATH', 'CAPTURES_DIR', 'WORKSPACE_DIR',
    'CODE_WORKSPACE_DIR', 'ALBUMS_DIR', 'AUDIO_CLIPS_DIR', '_CANVAS_DATA_DIR',
    'GENERATED_SOUNDS_DIR', 'STYLES_DIR', 'CACHE_DIR', '_BUILD_LOG_DIR',
    '_ZCODE_SESSIONS_DIR', 'ALBUMS_SUBDIR',
}
# Not roots any more (were /tmp-backed before review 2 of PR #597): _CLIENTS_DIR now
# falls back to the server-private dir, the events file lives there too.
RUNTIME_ROOT_STRINGS = ('uploads', 'canvas-pages', 'music', 'generated_music', 'transcripts',
                        'captures', 'workspace', 'code-workspace')
_ROOT_SEGMENT = re.compile(r'(?:^|/)(?:%s)(?:/|$)' % '|'.join(re.escape(d) for d in RUNTIME_ROOT_STRINGS))

# Everything the server process imports and runs in-process (review 3: plugins/ and the
# provider packages were outside the scope; the booking plugin was a write-anywhere).
SCAN = ('routes', 'services', 'server.py', 'app.py', 'plugins', 'tts_providers', 'providers')
SAFE_MODULE = 'services/safe_open.py'
EXCLUDE = {'services/safe_open.py'}  # the no-follow primitive itself

# Bare names that open by name wherever they come from (over-approximate on purpose).
NAME_SINKS = {'send_file', 'send_from_directory', 'open'}
# Dotted functions (after alias resolution) -> how many leading positional args are paths.
FUNC_SINKS = {
    **{f'os.{x}': 1 for x in ('open', 'chmod', 'lchmod', 'chown', 'lchown', 'remove', 'unlink',
                              'rmdir', 'removedirs', 'utime', 'truncate', 'mkfifo', 'mknod',
                              'chflags', 'lchflags', 'setxattr', 'removexattr')},
    **{f'os.{x}': 2 for x in ('replace', 'rename', 'renames', 'link', 'symlink')},
    'io.open': 1, 'builtins.open': 1, 'codecs.open': 1, '_io.open': 1,
    'gzip.open': 1, 'gzip.GzipFile': 1, 'bz2.open': 1, 'bz2.BZ2File': 1,
    'lzma.open': 1, 'lzma.LZMAFile': 1, 'wave.open': 1, 'aifc.open': 1, 'sunau.open': 1,
    'zipfile.ZipFile': 1, 'zipfile.Path': 1, 'tarfile.open': 1, 'tarfile.TarFile': 1,
    'sqlite3.connect': 1, 'shelve.open': 1, 'dbm.open': 1,
    'PIL.Image.open': 1,
    **{f'shutil.{x}': 2 for x in ('copy', 'copy2', 'copyfile', 'copytree', 'move', 'copymode',
                                  'copystat', 'unpack_archive')},
    'shutil.rmtree': 1, 'shutil.chown': 1, 'shutil.make_archive': 2,
    'flask.send_file': 1, 'flask.send_from_directory': 1,
}
# Keyword arguments of a sink that name a path.
PATH_KWS = {'file', 'filename', 'path', 'name', 'src', 'dst', 'database', 'target', 'dest',
            'extract_dir', 'directory', 'base_name', 'root_dir', 'path_or_file'}
TWO_PATH = {'replace', 'rename', 'copy', 'copy2', 'copyfile', 'copytree', 'move'}
# Path methods: the RECEIVER is the path opened/modified by name.
METHOD_SINKS = {'open', 'read_text', 'read_bytes', 'write_text', 'write_bytes', 'chmod', 'lchmod',
                'rename', 'replace', 'unlink', 'rmdir', 'touch', 'symlink_to',
                'hardlink_to', 'link_to', 'copy', 'copy_into', 'move', 'move_into'}
# Deliberately NOT sinks (review 3 of PR #597, accepted LOW): mkdir/makedirs through a
# planted symlink create an EMPTY directory (umask mode) where the server can write; no
# content, no mode change of an existing dir (any later write into it is itself a sink).
# Metadata and listing calls (stat/exists/is_file/glob/rglob/iterdir/listdir/scandir/
# os.path.*) follow symlinks but only leak existence, names and sizes.
# Methods whose ARGUMENT is a path written by name: FileStorage/Image .save(path),
# TarFile/ZipFile .extractall(path) / .extract(member, path).
ARG_METHOD_SINKS = {'save': (0,), 'extractall': (0,), 'extract': (1,)}
# Methods on an Anchored handle that hand out another Anchored handle.
ANCHORED_METHODS = {'child', 'sibling', 'rename', 'replace', 'iterdir'}
# Attributes of an Anchored handle that are plain path STRINGS (laundering).
ANCHORED_ESCAPE_ATTRS = {'path', '__fspath__', '__str__'}
_SPAWN_MODULES = {'subprocess', 'os', 'pty', 'asyncio'}
_ROUTE_DECORATORS = {'route', 'get', 'post', 'put', 'delete', 'patch'}


def _is_root_string(node):
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    v = node.value
    if v == '/tmp' or v.startswith('/tmp/'):
        return True
    if v.startswith('/app/runtime/') and any(f'/{d}' in v for d in RUNTIME_ROOT_STRINGS):
        return True
    return bool(re.search(r'(?:^|/)runtime/', v) and _ROOT_SEGMENT.search(v.split('runtime/', 1)[1]))


def _first_segment_is_root(node):
    return (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.strip('/').split('/')[0] in RUNTIME_ROOT_STRINGS)


def _mentions_root_after_value(parts):
    """f'{X}/uploads/{n}' / X + '/uploads/' ...: a root segment in a constant that follows a
    non-constant prefix (a constant-only string is judged by _is_root_string)."""
    seen_value = False
    for p in parts:
        if isinstance(p, ast.Constant) and isinstance(p.value, str):
            if seen_value and _ROOT_SEGMENT.search(p.value if p.value.startswith('/') else '/' + p.value):
                return True
        else:
            seen_value = True
    return False


def _add_chain(node):
    """Operands of a + b + c (left-nested BinOp Add), in order."""
    out = []
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        out.insert(0, node.right)
        node = node.left
    out.insert(0, node)
    return out


class _Module:
    def __init__(self, rel, tree, known=()):
        self.rel, self.tree = rel, tree
        self.funcs = {}       # qualname -> FunctionDef / AsyncFunctionDef / Lambda
        self.by_name = {}     # bare name -> [defs]
        self.methods = {}     # method name -> [FunctionDef directly in a ClassDef]
        self.parents = {}
        self.imports = {}     # local name -> (module rel path, remote name or None for a module)
        self.mods = {}        # local name -> dotted module it binds (import x.y as z)
        self.froms = {}       # local name -> dotted 'module.name' (from x import y as z)
        pkg = rel.rsplit('/', 1)[0].split('/') if '/' in rel else []
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                self.parents[ch] = node
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base_parts = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 else list(pkg)
                    mod = '.'.join(base_parts + ([node.module] if node.module else []))
                else:
                    mod = node.module or ''
                base = mod.replace('.', '/')
                for a in node.names:
                    local = a.asname or a.name
                    self.froms[local] = f'{mod}.{a.name}' if mod else a.name
                    sub = f'{base}/{a.name}.py' if base else f'{a.name}.py'
                    if sub in known:                      # from pkg import submodule
                        self.imports[local] = (sub, None)
                        self.mods[local] = f'{mod}.{a.name}'
                        del self.froms[local]
                    else:
                        self.imports[local] = (base + '.py', a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        self.mods[a.asname] = a.name
                        self.imports[a.asname] = (a.name.replace('.', '/') + '.py', None)
                    else:
                        top = a.name.split('.')[0]
                        self.mods[top] = top
                        self.imports[top] = (a.name.replace('.', '/') + '.py', None)
        self._collect(tree, '', in_class=False)

    def _collect(self, node, prefix, in_class):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[f'{prefix}{ch.name}'] = ch
                self.by_name.setdefault(ch.name, []).append(ch)
                if in_class:
                    self.methods.setdefault(ch.name, []).append(ch)
                self._collect(ch, f'{prefix}{ch.name}.', in_class=False)
            elif isinstance(ch, ast.ClassDef):
                self._collect(ch, f'{prefix}{ch.name}.', in_class=True)
            elif isinstance(ch, ast.Lambda):
                par = self.parents.get(ch)
                if (isinstance(par, ast.Assign) and len(par.targets) == 1
                        and isinstance(par.targets[0], ast.Name)):
                    name = par.targets[0].id
                    self.by_name.setdefault(name, []).append(ch)
                else:
                    name = f'<lambda@{ch.lineno}>'
                self.funcs[f'{prefix}{name}'] = ch
                self._collect(ch, f'{prefix}{name}.', in_class=False)
            else:
                self._collect(ch, prefix, in_class)

    def module_level_nodes(self):
        if not hasattr(self, '_mln'):
            self._mln = [n for n in ast.walk(self.tree)
                         if self.owner(n) is None
                         and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
        return self._mln

    def owner(self, node):
        p = self.parents.get(node)
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return p
            p = self.parents.get(p)
        return None

    # -- import resolution -----------------------------------------------------
    def real(self, name):
        """Dotted name an imported local binds ('_os' -> 'os', 'Image' -> 'PIL.Image')."""
        return self.mods.get(name) or self.froms.get(name)

    def dotted(self, expr):
        """'os.path.join' for an expression rooted at an imported name, else None."""
        if isinstance(expr, ast.Name):
            return self.real(expr.id)
        if isinstance(expr, ast.Attribute):
            base = self.dotted(expr.value)
            return f'{base}.{expr.attr}' if base else None
        if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == 'getattr'
                and len(expr.args) >= 2 and isinstance(expr.args[1], ast.Constant)
                and isinstance(expr.args[1].value, str)):
            base = self.dotted(expr.args[0])
            return f'{base}.{expr.args[1].value}' if base else None
        return None

    def is_module_expr(self, expr):
        """`os`, `_os`, `os.path`: an imported MODULE (its attributes are functions)."""
        if isinstance(expr, ast.Name):
            return expr.id in self.mods
        if isinstance(expr, ast.Attribute):
            return self.is_module_expr(expr.value)
        return False

    def call_target(self, call):
        """Dotted target of a call ('os.open', 'shutil.copy', 'subprocess.Popen'), a bare
        builtin name, '<dynamic>' for a callee the AST cannot name, or None."""
        f = call.func
        if isinstance(f, ast.Name):
            return self.froms.get(f.id) or self.mods.get(f.id) or f.id
        if isinstance(f, ast.Attribute):
            return self.dotted(f)
        if isinstance(f, ast.Call):
            d = self.dotted(f)
            if d:
                return d
            if isinstance(f.func, ast.Name) and f.func.id == 'getattr':
                return '<dynamic>'
        return '<dynamic>'


def _is_anchored_ctor(m, call):
    t = m.call_target(call) if isinstance(call, ast.Call) else None
    return bool(t) and (t.endswith('safe_open.Anchored') or t == 'services.safe_open.Anchored')


class _Engine:
    """One taint label ('root' or 'req') over the shared module set."""

    def __init__(self, modules, label):
        self.modules, self.label = modules, label
        self.global_tainted = {rel: set() for rel in modules}
        self.tainted_returns = set()   # id(def): returns a tainted path whatever its args
        self.return_props = {}         # id(def) -> params whose taint flows to the return
        self.tainted_params = {}       # id(def) -> set(param names)
        self.anchored_returns = set()  # id(def): returns an Anchored handle
        self.anchored_params = {}      # id(def) -> params that receive an Anchored handle
        self._lt = {}
        self._rc = {}
        if label == 'req':
            self._seed_route_params()
        self._fixpoint()

    # -- resolution ----------------------------------------------------------
    def _resolve(self, m, call):
        """(defs a call may run, exact?). Exact = same module, imports, self/cls; heuristic =
        a method of a class in this module or a module it imports (obj.meth(...))."""
        k = id(call)
        if k not in self._rc:
            self._rc[k] = self._resolve_uncached(m, call)
        return self._rc[k]

    def _resolve_uncached(self, m, call):
        f = call.func
        if isinstance(f, ast.Name):
            if f.id in m.by_name:
                return m.by_name[f.id], True
            imp = m.imports.get(f.id)
            if imp and imp[1] and imp[0] in self.modules:
                return self.modules[imp[0]].by_name.get(imp[1], []), True
            return [], True
        if isinstance(f, ast.Attribute):
            if isinstance(f.value, ast.Name):
                imp = m.imports.get(f.value.id)
                if imp and imp[1] is None:
                    if imp[0] in self.modules:
                        return self.modules[imp[0]].by_name.get(f.attr, []), True
                    return [], True
                if f.value.id in ('self', 'cls'):
                    return list(m.by_name.get(f.attr, [])), True
                if imp and imp[1] and imp[0] in self.modules:
                    # a from-imported object of a scanned module (`gateway_manager.send(p)`)
                    return list(self.modules[imp[0]].methods.get(f.attr, [])), False
                if m.real(f.value.id):
                    return [], True              # an imported (non-scanned) name's attribute
            defs = list(m.methods.get(f.attr, []))
            for imp in set(m.imports.values()):
                other = self.modules.get(imp[0])
                if other is not None and other is not m:
                    defs.extend(other.methods.get(f.attr, []))
            return defs, False
        return [], True

    @staticmethod
    def _params(target, call):
        a = target.args
        params = [x.arg for x in a.posonlyargs + a.args]
        if params and params[0] in ('self', 'cls') and isinstance(call.func, ast.Attribute):
            params = params[1:]
        return params

    def _imported_tainted(self, m, name):
        imp = m.imports.get(name)
        return bool(imp and imp[1] and imp[0] in self.modules and imp[1] in self.global_tainted[imp[0]])

    def _seed_route_params(self):
        """A route's URL parameters (`/x/<path:name>`) are request-supplied."""
        for m in self.modules.values():
            for fn in m.funcs.values():
                for dec in getattr(fn, 'decorator_list', ()):
                    if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                            and dec.func.attr in _ROUTE_DECORATORS and dec.args
                            and isinstance(dec.args[0], ast.Constant)
                            and isinstance(dec.args[0].value, str)):
                        continue
                    names = set(re.findall(r'<(?:[a-z_]+:)?([A-Za-z_][A-Za-z0-9_]*)>', dec.args[0].value))
                    if names:
                        self.tainted_params.setdefault(id(fn), set()).update(names)

    # -- sanitizer / Anchored ------------------------------------------------
    def _is_sanitizer(self, m, call):
        """A call into services/safe_open.py: its result is a no-follow, beneath-root handle."""
        f = call.func
        if isinstance(f, ast.Name):
            imp = m.imports.get(f.id)
            return bool(imp and imp[0] == SAFE_MODULE)
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            imp = m.imports.get(f.value.id)
            return bool(imp and (imp[0] == SAFE_MODULE or
                                 (imp[1] == 'safe_open' and imp[0] == 'services.py')))
        return False

    def _is_anchored(self, m, n, local):
        """An expression that evaluates to an Anchored handle."""
        if isinstance(n, ast.Name):
            return ('A', n.id) in local
        if isinstance(n, (ast.IfExp,)):
            return self._is_anchored(m, n.body, local) or self._is_anchored(m, n.orelse, local)
        if isinstance(n, ast.BoolOp):
            return any(self._is_anchored(m, v, local) for v in n.values)
        if isinstance(n, ast.Subscript):
            return self._is_anchored(m, n.value, local)        # a.iterdir()[0]
        if not isinstance(n, ast.Call):
            return False
        if _is_anchored_ctor(m, n):
            return True
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr in ANCHORED_METHODS and self._is_anchored(m, f.value, local):
            return True
        defs, exact = self._resolve(m, n)
        return exact and any(id(d) in self.anchored_returns for d in defs)

    _TRANSPARENT = (ast.IfExp, ast.BoolOp, ast.NamedExpr, ast.Await, ast.Starred)

    def _anchored_escapes(self, m, n, local):
        """An Anchored handle used as a plain path: a.path, str(a), os.fspath(a), f'{a}',
        open(a), a / 'x', or handed to anything that is not a scanned function."""
        if not self._is_anchored(m, n, local):
            return False
        child, p = n, m.parents.get(n)
        while isinstance(p, self._TRANSPARENT) or (isinstance(p, ast.Subscript) and p.value is child):
            if isinstance(p, ast.NamedExpr) and p.value is child:
                return False
            child, p = p, m.parents.get(p)
        if p is None:
            return False
        if isinstance(p, ast.Attribute):
            return p.attr in ANCHORED_ESCAPE_ATTRS
        if isinstance(p, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return, ast.Yield,
                          ast.YieldFrom, ast.Expr, ast.Compare, ast.If, ast.While, ast.Assert,
                          ast.withitem, ast.comprehension, ast.For, ast.AsyncFor, ast.UnaryOp,
                          ast.List, ast.Tuple, ast.Set, ast.Dict)):
            return False
        if isinstance(p, ast.keyword):
            p = m.parents.get(p)
        if isinstance(p, ast.Call) and p.func is not child:
            t = m.call_target(p)
            if t in ('isinstance', 'id', 'bool', 'type', 'print', 'repr'):
                return False
            if isinstance(p.func, ast.Attribute) and p.func.attr in (
                    'debug', 'info', 'warning', 'error', 'exception', 'critical', 'log',
                    'append', 'add', 'extend', 'insert'):
                return False
            defs, exact = self._resolve(m, p)
            if defs:
                return False   # handled by anchored_params propagation into the callee
            return True
        return True

    # -- taint ---------------------------------------------------------------
    def _walk(self, m, expr):
        stack = [expr]
        while stack:
            n = stack.pop()
            yield n
            if isinstance(n, ast.Call) and self._is_sanitizer(m, n):
                continue
            if isinstance(n, ast.Call):
                defs, exact = self._resolve(m, n)
                if defs and exact:
                    # a scanned function: its result's taint is decided by its return
                    # analysis (_call_returns_tainted), not by the mere presence of args
                    stack.append(n.func)
                    continue
            stack.extend(ast.iter_child_nodes(n))

    def _expr_tainted(self, m, expr, local):
        if expr is None:
            return False
        if self.label == 'req':
            return self._req_head(m, expr, local)
        for n in self._walk(m, expr):
            if _is_root_string(n):
                return True
            if isinstance(n, ast.Name) and (n.id in ROOT_NAMES or n.id in local
                                            or n.id in self.global_tainted[m.rel]
                                            or self._imported_tainted(m, n.id)):
                return True
            if isinstance(n, ast.Attribute) and n.attr in ROOT_NAMES:
                return True
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div) and _first_segment_is_root(n.right):
                return True   # <anything> / 'uploads' (a runtime root joined by name)
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) \
                    and _mentions_root_after_value(_add_chain(n)):
                return True   # str(RUNTIME_DIR) + '/uploads/' + n
            if isinstance(n, ast.JoinedStr) and _mentions_root_after_value(n.values):
                return True   # f'{RUNTIME_DIR}/uploads/{n}'
            if isinstance(n, ast.Call):
                fname = n.func.attr if isinstance(n.func, ast.Attribute) else \
                    n.func.id if isinstance(n.func, ast.Name) else ''
                if fname in ('join', 'joinpath', 'Path', 'PurePath', 'PosixPath') \
                        and not (isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Constant)) \
                        and any(_first_segment_is_root(a) for a in (n.args[1:] if fname == 'join' else n.args)):
                    return True   # os.path.join(..., 'runtime', 'uploads') / Path(x, 'uploads')
                if self._call_returns_tainted(m, n, local):
                    return True
            if self._anchored_escapes(m, n, local):
                return True
        return False

    def _req_head(self, m, e, local, depth=0):
        """Is the HEAD of this path expression request-supplied?"""
        if depth > 40 or e is None:
            return False
        r = lambda x: self._req_head(m, x, local, depth + 1)  # noqa: E731
        if isinstance(e, ast.Name):
            return (e.id == 'request' or e.id in local or e.id in self.global_tainted[m.rel]
                    or self._imported_tainted(m, e.id))
        if isinstance(e, (ast.Attribute, ast.Subscript, ast.Starred, ast.Await)):
            return r(e.value)
        if isinstance(e, ast.NamedExpr):
            return r(e.value)
        if isinstance(e, ast.BinOp):
            return r(e.left)
        if isinstance(e, ast.JoinedStr):
            first = e.values[0] if e.values else None
            return isinstance(first, ast.FormattedValue) and r(first.value)
        if isinstance(e, ast.IfExp):
            return r(e.body) or r(e.orelse)
        if isinstance(e, ast.BoolOp):
            return any(r(v) for v in e.values)
        if isinstance(e, ast.Call):
            if self._is_sanitizer(m, e):
                return False
            defs, exact = self._resolve(m, e)
            if defs:
                if self._call_returns_tainted(m, e, local):
                    return True
                if exact:
                    return False
            f = e.func
            if isinstance(f, ast.Attribute) and not m.is_module_expr(f.value):
                return r(f.value)                          # x.resolve() / data.get('p') / ...
            return bool(e.args) and r(e.args[0])           # Path(x) / os.path.join(x, ...) / str(x)
        return False

    def _call_returns_tainted(self, m, call, local):
        defs, _ = self._resolve(m, call)
        for d in defs:
            if id(d) in self.tainted_returns:
                return True
            props = self.return_props.get(id(d))
            if not props:
                continue
            params = self._params(d, call)
            for i, a in enumerate(call.args):
                if i < len(params) and params[i] in props and self._expr_tainted(m, a, local):
                    return True
            for kw in call.keywords:
                if kw.arg in props and self._expr_tainted(m, kw.value, local):
                    return True
        return False

    @staticmethod
    def _assign_targets(node):
        if isinstance(node, ast.Assign):
            return node.targets, node.value
        if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            return [node.target], node.value
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            return [node.target], node.iter
        if isinstance(node, ast.withitem):
            return ([node.optional_vars] if node.optional_vars is not None else []), node.context_expr
        if isinstance(node, ast.NamedExpr):
            return [node.target], node.value
        return [], None

    @staticmethod
    def _returns(m, fn):
        if isinstance(fn, ast.Lambda):
            return [fn.body]
        return [r.value for r in ast.walk(fn)
                if isinstance(r, (ast.Return, ast.Yield, ast.YieldFrom)) and m.owner(r) is fn]

    def _local_taint(self, m, fn, seed=None):
        if seed is None:
            seed = set(self.tainted_params.get(id(fn), ()))
            seed |= {('A', p) for p in self.anchored_params.get(id(fn), ())}
        seed = frozenset(seed)
        key = (id(fn), seed, self._gen)
        if key not in self._lt:
            local = set(seed)
            changed = True
            body = [fn.body] if isinstance(fn, ast.Lambda) else [fn]
            while changed:
                changed = False
                for root in body:
                    for node in ast.walk(root):
                        targets, value = self._assign_targets(node)
                        if value is None:
                            continue
                        anch = self.label == 'root' and self._is_anchored_iter(m, node, value, local)
                        if anch:
                            for t in targets:
                                if isinstance(t, ast.Name) and ('A', t.id) not in local:
                                    local.add(('A', t.id))
                                    changed = True
                            continue
                        if not self._expr_tainted(m, value, local):
                            continue
                        for t in targets:
                            for n in ast.walk(t):
                                if isinstance(n, ast.Name) and n.id not in local:
                                    local.add(n.id)
                                    changed = True
            self._lt[key] = local
        return self._lt[key]

    def _is_anchored_iter(self, m, node, value, local):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            # for c in a.iterdir(): c is an Anchored handle
            return (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                    and value.func.attr == 'iterdir' and self._is_anchored(m, value.func.value, local))
        return self._is_anchored(m, value, local)

    def _fixpoint(self):
        self._gen = 0
        changed = True
        while changed:
            changed = False
            self._gen += 1
            for m in self.modules.values():
                # module-level assignments, including those nested in module-level if/try
                # (e.g. `if not X.exists(): X = Path('/tmp/...')`), not those in functions
                for node in m.module_level_nodes():
                    targets, value = self._assign_targets(node)
                    if value is not None and not isinstance(value, ast.Lambda) \
                            and self._expr_tainted(m, value, set()):
                        for t in targets:
                            for n in ast.walk(t):
                                if isinstance(n, ast.Name) and n.id not in self.global_tainted[m.rel]:
                                    self.global_tainted[m.rel].add(n.id)
                                    changed = True
                # module-level call sites taint parameters too (e.g. boot-time seeding code)
                for node in m.module_level_nodes():
                    if isinstance(node, ast.Call):
                        changed |= self._propagate_call(m, node, set())
                for fn in m.funcs.values():
                    local = self._local_taint(m, fn)
                    returns = self._returns(m, fn)
                    # unconditional: tainted with no parameter tainted
                    if id(fn) not in self.tainted_returns:
                        base = self._local_taint(m, fn, seed=())
                        if any(self._expr_tainted(m, r, base) for r in returns):
                            self.tainted_returns.add(id(fn))
                            changed = True
                    if self.label == 'root' and id(fn) not in self.anchored_returns:
                        if any(r is not None and self._is_anchored(m, r, local) for r in returns):
                            self.anchored_returns.add(id(fn))
                            changed = True
                    # per parameter: does that parameter's taint reach the return value?
                    props = self.return_props.setdefault(id(fn), set())
                    a = fn.args
                    for arg in a.posonlyargs + a.args + a.kwonlyargs:
                        if arg.arg in props or arg.arg in ('self', 'cls'):
                            continue
                        lt = self._local_taint(m, fn, seed=(arg.arg,))
                        if any(self._expr_tainted(m, r, lt) for r in returns):
                            props.add(arg.arg)
                            changed = True
                    # call sites: a tainted argument taints the callee's parameter
                    for node in ast.walk(fn.body if isinstance(fn, ast.Lambda) else fn):
                        if isinstance(node, ast.Call) and m.owner(node) is fn:
                            changed |= self._propagate_call(m, node, local)

    def _propagate_call(self, m, node, local) -> bool:
        changed = False
        defs, _ = self._resolve(m, node)
        for target in defs:
            params = self._params(target, node)
            hit = {params[i] for i, a in enumerate(node.args)
                   if i < len(params) and self._expr_tainted(m, a, local)}
            hit |= {kw.arg for kw in node.keywords
                    if kw.arg and self._expr_tainted(m, kw.value, local)}
            cur = self.tainted_params.setdefault(id(target), set())
            if hit - cur:
                cur |= hit
                changed = True
            if self.label == 'root':
                ahit = {params[i] for i, a in enumerate(node.args)
                        if i < len(params) and self._is_anchored(m, a, local)}
                ahit |= {kw.arg for kw in node.keywords if kw.arg and self._is_anchored(m, kw.value, local)}
                acur = self.anchored_params.setdefault(id(target), set())
                if ahit - acur:
                    acur |= ahit
                    changed = True
        return changed

    # -- sinks ---------------------------------------------------------------
    def _sink(self, m, call):
        """(sink-name, [path exprs]) or None."""
        f = call.func
        if any(kw.arg in ('dir_fd', 'src_dir_fd', 'dst_dir_fd') for kw in call.keywords):
            return None  # relative to a pinned directory fd
        kw_paths = [kw.value for kw in call.keywords if kw.arg in PATH_KWS]
        target = m.call_target(call)
        if isinstance(f, ast.Name) and f.id in NAME_SINKS and f.id not in m.by_name:
            return f.id, call.args[:1] + kw_paths
        if target in FUNC_SINKS:
            short = target if not target.startswith('builtins.') else target.split('.', 1)[1]
            return short, call.args[:FUNC_SINKS[target]] + kw_paths
        if target and target != '<dynamic>' and isinstance(f, (ast.Attribute, ast.Call)) \
                and target.rsplit('.', 1)[-1] == 'open':
            # <any imported module>.open(path); for an imported VALUE (`UPLOADS_DIR.open()`)
            # the receiver is the path
            return target, call.args[:1] + kw_paths + [f.value if isinstance(f, ast.Attribute) else f]
        if isinstance(f, ast.Attribute) and not m.is_module_expr(f.value):
            if f.attr in ARG_METHOD_SINKS:
                idx = ARG_METHOD_SINKS[f.attr]
                paths = [call.args[i] for i in idx if i < len(call.args)]
                paths += [kw.value for kw in call.keywords if kw.arg in ('path', 'dst', 'fp')]
                if paths:
                    return f'.{f.attr}', paths
            if f.attr in METHOD_SINKS:
                if f.attr in ('replace', 'rename') and len(call.args) + len(call.keywords) != 1:
                    return None  # str.replace(a, b), not Path.replace(target)
                if f.attr in ('copy', 'move') and not call.args:
                    return None  # dict.copy()
                if isinstance(f.value, ast.Constant):
                    return None
                if isinstance(f.value, ast.Name) and f.value.id in ('self', 'cls') \
                        and f.attr in ('open', 'replace', 'rename', 'copy', 'move'):
                    return None  # a class's own method, resolved as a call instead
                # rename/replace never follow the TARGET name (they replace it), so only the
                # receiver (the file being moved) matters
                return f'.{f.attr}', [f.value]
        return None

    def _dynamic(self, m, call, local):
        """Fail closed: a tainted argument reaching a callee the AST cannot name, or handed
        over together with a sink function as a value (map(open, paths), partial(open, p))."""
        args = list(call.args) + [kw.value for kw in call.keywords]
        if not any(self._expr_tainted(m, a, local) for a in args):
            return None
        if m.call_target(call) == '<dynamic>':
            return '<dynamic-call>'
        for a in args:
            if isinstance(a, ast.Name) and a.id in NAME_SINKS and a.id not in m.by_name:
                return f'<sink-as-value:{a.id}>'
            d = m.dotted(a) if isinstance(a, ast.Attribute) else None
            if d in FUNC_SINKS:
                return f'<sink-as-value:{d}>'
        return None

    def findings(self):
        out = []
        for m in self.modules.values():
            scopes = [(q, fn) for q, fn in m.funcs.items()] + [('<module>', None)]
            for q, fn in scopes:
                body = (fn.body if isinstance(fn, ast.Lambda) else fn) if fn is not None else m.tree
                local = self._local_taint(m, fn) if fn is not None else set()
                for node in ast.walk(body):
                    if not isinstance(node, ast.Call) or m.owner(node) is not fn:
                        continue
                    if self._is_sanitizer(m, node):
                        continue
                    s = self._sink(m, node)
                    if s and any(self._expr_tainted(m, p, local) for p in s[1]):
                        out.append((m.rel, q, s[0], node.lineno))
                        continue
                    d = self._dynamic(m, node, local)
                    if d:
                        out.append((m.rel, q, d, node.lineno))
        return out


def _iter_files(root):
    root = Path(root)
    for item in SCAN:
        p = root / item
        if p.is_file():
            yield p
        elif p.is_dir():
            yield from sorted(p.rglob('*.py'))


def _load_modules(repo_root):
    root = Path(repo_root)
    files = list(_iter_files(root))
    known = {f.relative_to(root).as_posix() for f in files}
    mods = {}
    for f in files:
        rel = f.relative_to(root).as_posix()
        if rel not in EXCLUDE:
            mods[rel] = _Module(rel, ast.parse(f.read_text(encoding='utf-8')), known)
    return mods


class Scanner:
    """Union of the root-derived and the request-derived taint engines."""

    def __init__(self, repo_root):
        self.root = Path(repo_root)
        self.modules = _load_modules(self.root)
        self.engines = [_Engine(self.modules, 'root'), _Engine(self.modules, 'req')]

    def findings(self):
        seen, out = set(), []
        for e in self.engines:
            for f in e.findings():
                if f not in seen:
                    seen.add(f)
                    out.append(f)
        return out


_GROUPS_CACHE = {}


def unclassified(repo_root, allowlist):
    """Findings grouped by 'file::function::sink' that the allowlist does not cover."""
    key = str(Path(repo_root).resolve())
    if key not in _GROUPS_CACHE:
        groups = {}
        for rel, q, name, line in Scanner(repo_root).findings():
            groups.setdefault(f'{rel}::{q}::{name}', []).append(line)
        _GROUPS_CACHE[key] = groups
    groups = _GROUPS_CACHE[key]
    return {k: v for k, v in groups.items() if k not in allowlist}, groups


# ── process spawns ───────────────────────────────────────────────────────────

SPAWN_CALLS = (
    {('subprocess', x) for x in ('run', 'call', 'check_call', 'check_output', 'Popen',
                                 'getoutput', 'getstatusoutput')}
    | {('os', x) for x in ('system', 'popen', 'execv', 'execve', 'execvp', 'execvpe', 'execl',
                           'execle', 'execlp', 'execlpe', 'spawnv', 'spawnve', 'spawnvp',
                           'spawnvpe', 'spawnl', 'spawnle', 'spawnlp', 'spawnlpe',
                           'posix_spawn', 'posix_spawnp', 'fork', 'forkpty')}
    | {('pty', 'fork'), ('pty', 'spawn'),
       ('asyncio', 'create_subprocess_exec'), ('asyncio', 'create_subprocess_shell')}
)
_SPAWN_DOTTED = {f'{a}.{b}' for a, b in SPAWN_CALLS}


def spawn_sites(repo_root):
    """{'file::function::module.func': [lines]} for every process-spawn call site.

    Resolved through the import table (`import subprocess as sp`, `from subprocess import
    Popen`, `getattr(os, 'system')`). A getattr on a spawn module with a NON-constant name,
    and an event loop's subprocess_exec/_shell, are reported too (fail closed)."""
    root = Path(repo_root)
    files = list(_iter_files(root))
    known = {f.relative_to(root).as_posix() for f in files}
    out = {}
    for f in files:
        m = _Module(f.relative_to(root).as_posix(), ast.parse(f.read_text(encoding='utf-8')), known)
        quals = {id(fn): q for q, fn in m.funcs.items()}
        for node in ast.walk(m.tree):
            if not isinstance(node, ast.Call):
                continue
            t = m.call_target(node)
            key = None
            if t in _SPAWN_DOTTED:
                key = t
            elif (isinstance(node.func, ast.Call) and isinstance(node.func.func, ast.Name)
                  and node.func.func.id == 'getattr' and node.func.args
                  and (m.dotted(node.func.args[0]) or '') in _SPAWN_MODULES):
                key = f'{m.dotted(node.func.args[0])}.getattr(?)'
            elif isinstance(node.func, ast.Attribute) and node.func.attr in ('subprocess_exec',
                                                                             'subprocess_shell'):
                key = f'asyncio.loop.{node.func.attr}'
            if key is None:
                continue
            owner = m.owner(node)
            q = quals.get(id(owner), '<module>') if owner is not None else '<module>'
            out.setdefault(f'{m.rel}::{q}::{key}', []).append(node.lineno)
    return out
