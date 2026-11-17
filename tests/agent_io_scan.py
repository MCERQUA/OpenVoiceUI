"""Static scan: file I/O whose path can come from an agent-writable root.

Used by tests/test_agent_writable_io_guard.py. Pure AST: nothing scanned is imported.

Agent-writable roots are directories the tenant's agent (openclaw container,
read-write mounts) or the terminal's sandbox user can write: uploads, canvas
pages (+ _data, stories, styles), music, generated_music, transcripts,
captures, the workspace mounts (Agent = the agent's own workspace), the code
workspace, and /tmp. A path that DERIVES from one of them (assignment, call
argument, helper return value, loop variable, comprehension; across modules
through imports) and reaches an I/O sink BY NAME follows whatever symlink the
agent planted. Each such sink must go through services/safe_open.py
(no-follow, beneath-root, fd-based) or be allowlisted with a reason.

The analysis over-approximates on purpose: a false positive costs one
allowlist line with a reason; a false negative is a hole.
"""

import ast
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

SCAN = ('routes', 'services', 'server.py', 'app.py')
SAFE_MODULE = 'services/safe_open.py'
EXCLUDE = {'services/safe_open.py'}  # the no-follow primitive itself

NAME_SINKS = {'send_file', 'send_from_directory', 'open'}
MODULE_SINKS = {
    ('os', 'open'), ('os', 'replace'), ('os', 'rename'), ('os', 'chmod'),
    ('io', 'open'), ('builtins', 'open'),
    ('shutil', 'copy'), ('shutil', 'copy2'), ('shutil', 'copyfile'), ('shutil', 'copytree'),
    ('shutil', 'move'), ('shutil', 'rmtree'),
    ('flask', 'send_file'), ('flask', 'send_from_directory'),
}
TWO_PATH = {'replace', 'rename', 'copy', 'copy2', 'copyfile', 'copytree', 'move'}
METHOD_SINKS = {'open', 'read_text', 'read_bytes', 'write_text', 'write_bytes', 'chmod',
                'rename', 'replace'}
NOT_PATH_RECEIVERS = {'os', 'io', 'shutil', 'json', 'requests', 'httpx', 're', 'zipfile',
                      'tarfile', 'Image', 'wave', 'webbrowser', 'subprocess'}


def _is_root_string(node):
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    v = node.value
    if v == '/tmp' or v.startswith('/tmp/'):
        return True
    return v.startswith('/app/runtime/') and any(f'/{d}' in v for d in RUNTIME_ROOT_STRINGS)


class _Module:
    def __init__(self, rel, tree):
        self.rel, self.tree = rel, tree
        self.funcs = {}       # qualname -> FunctionDef
        self.by_name = {}     # bare name -> [FunctionDef]
        self.parents = {}
        self.imports = {}     # local name -> (module rel path, remote name or None for a module)
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                self.parents[ch] = node
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                base = node.module.replace('.', '/')
                for a in node.names:
                    self.imports[a.asname or a.name] = (base + '.py', a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    self.imports[a.asname or a.name.split('.')[0]] = (a.name.replace('.', '/') + '.py', None)
        self._collect(tree, '')

    def _collect(self, node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[f'{prefix}{ch.name}'] = ch
                self.by_name.setdefault(ch.name, []).append(ch)
                self._collect(ch, f'{prefix}{ch.name}.')
            elif isinstance(ch, ast.ClassDef):
                self._collect(ch, f'{prefix}{ch.name}.')
            else:
                self._collect(ch, prefix)

    def module_level_nodes(self):
        if not hasattr(self, '_mln'):
            self._mln = [n for n in ast.walk(self.tree)
                         if self.owner(n) is None and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        return self._mln

    def owner(self, node):
        p = self.parents.get(node)
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return p
            p = self.parents.get(p)
        return None


class Scanner:
    def __init__(self, repo_root):
        self.root = Path(repo_root)
        self.modules = {}
        for item in SCAN:
            p = self.root / item
            files = [p] if p.is_file() else sorted(p.rglob('*.py'))
            for f in files:
                rel = f.relative_to(self.root).as_posix()
                if rel not in EXCLUDE:
                    self.modules[rel] = _Module(rel, ast.parse(f.read_text(encoding='utf-8')))
        self.global_tainted = {rel: set() for rel in self.modules}  # module-level names
        self.tainted_returns = set()   # id(FunctionDef): returns a tainted path whatever its args
        self.return_props = {}         # id(FunctionDef) -> params whose taint flows to the return
        self.tainted_params = {}       # id(FunctionDef) -> set(param names)
        self._lt = {}
        self._fixpoint()

    # -- resolution ----------------------------------------------------------
    def _resolve(self, m, call):
        """FunctionDefs a call may run (same module, or followed through imports)."""
        f = call.func
        if isinstance(f, ast.Name):
            if f.id in m.by_name:
                return m.by_name[f.id]
            imp = m.imports.get(f.id)
            if imp and imp[1] and imp[0] in self.modules:
                return self.modules[imp[0]].by_name.get(imp[1], [])
            return []
        if isinstance(f, ast.Attribute):
            if isinstance(f.value, ast.Name):
                imp = m.imports.get(f.value.id)
                if imp and imp[1] is None and imp[0] in self.modules:
                    return self.modules[imp[0]].by_name.get(f.attr, [])
                if f.value.id in ('self', 'cls'):
                    return [d for d in m.by_name.get(f.attr, [])]
            return []
        return []

    def _imported_tainted(self, m, name):
        imp = m.imports.get(name)
        return bool(imp and imp[1] and imp[0] in self.modules and imp[1] in self.global_tainted[imp[0]])

    # -- taint ---------------------------------------------------------------
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

    def _walk(self, m, expr):
        stack = [expr]
        while stack:
            n = stack.pop()
            yield n
            if isinstance(n, ast.Call) and self._is_sanitizer(m, n):
                continue
            if isinstance(n, ast.Call) and self._resolve(m, n):
                # a scanned function: its result's taint is decided by its return
                # analysis (_call_returns_tainted), not by the mere presence of args
                stack.append(n.func)
                continue
            stack.extend(ast.iter_child_nodes(n))

    def _expr_tainted(self, m, expr, local):
        if expr is None:
            return False
        for n in self._walk(m, expr):
            if _is_root_string(n):
                return True
            if isinstance(n, ast.Name) and (n.id in ROOT_NAMES or n.id in local
                                            or n.id in self.global_tainted[m.rel]
                                            or self._imported_tainted(m, n.id)):
                return True
            if isinstance(n, ast.Attribute) and n.attr in ROOT_NAMES:
                return True
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div) and isinstance(n.right, ast.Constant) \
                    and isinstance(n.right.value, str) \
                    and n.right.value.strip('/').split('/')[0] in RUNTIME_ROOT_STRINGS:
                return True   # <anything> / 'uploads' (a runtime root joined by name)
            if isinstance(n, ast.Call) and self._call_returns_tainted(m, n, local):
                return True
        return False

    def _call_returns_tainted(self, m, call, local):
        for d in self._resolve(m, call):
            if id(d) in self.tainted_returns:
                return True
            props = self.return_props.get(id(d))
            if not props:
                continue
            params = [a.arg for a in d.args.args]
            if params and params[0] in ('self', 'cls') and isinstance(call.func, ast.Attribute):
                params = params[1:]
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

    def _local_taint(self, m, fn, seed=None):
        seed = frozenset(self.tainted_params.get(id(fn), ())) if seed is None else frozenset(seed)
        key = (id(fn), seed, self._gen)
        if key not in self._lt:
            local = set(seed)
            changed = True
            while changed:
                changed = False
                for node in ast.walk(fn):
                    targets, value = self._assign_targets(node)
                    if value is None or not self._expr_tainted(m, value, local):
                        continue
                    for t in targets:
                        for n in ast.walk(t):
                            if isinstance(n, ast.Name) and n.id not in local:
                                local.add(n.id)
                                changed = True
            self._lt[key] = local
        return self._lt[key]

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
                    if value is not None and self._expr_tainted(m, value, set()):
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
                    returns = [r for r in ast.walk(fn) if isinstance(r, ast.Return) and m.owner(r) is fn]
                    # unconditional: tainted with no parameter tainted
                    if id(fn) not in self.tainted_returns:
                        base = self._local_taint(m, fn, seed=())
                        if any(self._expr_tainted(m, r.value, base) for r in returns):
                            self.tainted_returns.add(id(fn))
                            changed = True
                    # per parameter: does that parameter's taint reach the return value?
                    props = self.return_props.setdefault(id(fn), set())
                    for a in fn.args.args + fn.args.kwonlyargs:
                        if a.arg in props or a.arg in ('self', 'cls'):
                            continue
                        lt = self._local_taint(m, fn, seed=(a.arg,))
                        if any(self._expr_tainted(m, r.value, lt) for r in returns):
                            props.add(a.arg)
                            changed = True
                    # call sites: a tainted argument taints the callee's parameter
                    for node in ast.walk(fn):
                        if isinstance(node, ast.Call) and m.owner(node) is fn:
                            changed |= self._propagate_call(m, node, local)

    def _propagate_call(self, m, node, local) -> bool:
        changed = False
        for target in self._resolve(m, node):
            params = [a.arg for a in target.args.args]
            if params and params[0] in ('self', 'cls') and isinstance(node.func, ast.Attribute):
                params = params[1:]
            hit = {params[i] for i, a in enumerate(node.args)
                   if i < len(params) and self._expr_tainted(m, a, local)}
            hit |= {kw.arg for kw in node.keywords
                    if kw.arg and self._expr_tainted(m, kw.value, local)}
            cur = self.tainted_params.setdefault(id(target), set())
            if hit - cur:
                cur |= hit
                changed = True
        return changed

    # -- sinks ---------------------------------------------------------------
    @staticmethod
    def _sink(call):
        """(sink-name, [path exprs]) or None."""
        f = call.func
        if any(kw.arg == 'dir_fd' for kw in call.keywords):
            return None  # relative to a pinned directory fd
        if isinstance(f, ast.Name) and f.id in NAME_SINKS:
            return f.id, call.args[:1]
        if isinstance(f, ast.Attribute):
            if isinstance(f.value, ast.Name) and (f.value.id, f.attr) in MODULE_SINKS:
                return f'{f.value.id}.{f.attr}', call.args[:2 if f.attr in TWO_PATH else 1]
            if f.attr == 'save' and call.args:
                return '.save', call.args[:1]  # FileStorage.save(path) / Image.save(path) open by name
            if f.attr in METHOD_SINKS:
                if isinstance(f.value, ast.Name) and f.value.id in NOT_PATH_RECEIVERS:
                    return None
                if f.attr in ('replace', 'rename') and len(call.args) + len(call.keywords) != 1:
                    return None  # str.replace(a, b), not Path.replace(target)
                if isinstance(f.value, ast.Constant):
                    return None
                # rename/replace never follow the TARGET name (they replace it), so only the
                # receiver (the file being moved) matters
                return f'.{f.attr}', [f.value]
        return None

    def findings(self):
        out = []
        for m in self.modules.values():
            scopes = [(q, fn) for q, fn in m.funcs.items()] + [('<module>', None)]
            for q, fn in scopes:
                body = fn if fn is not None else m.tree
                local = self._local_taint(m, fn) if fn is not None else set()
                for node in ast.walk(body):
                    if not isinstance(node, ast.Call) or m.owner(node) is not fn:
                        continue
                    if self._is_sanitizer(m, node):
                        continue
                    s = self._sink(node)
                    if s and any(self._expr_tainted(m, p, local) for p in s[1]):
                        out.append((m.rel, q, s[0], node.lineno))
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


def spawn_sites(repo_root):
    """{'file::function::module.func': [lines]} for every process-spawn call site."""
    root = Path(repo_root)
    out = {}
    for item in SCAN:
        p = root / item
        for f in ([p] if p.is_file() else sorted(p.rglob('*.py'))):
            m = _Module(f.relative_to(root).as_posix(), ast.parse(f.read_text(encoding='utf-8')))
            quals = {id(fn): q for q, fn in m.funcs.items()}
            for node in ast.walk(m.tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)):
                    continue
                key = (node.func.value.id, node.func.attr)
                if key not in SPAWN_CALLS:
                    continue
                owner = m.owner(node)
                q = quals.get(id(owner), '<module>') if owner is not None else '<module>'
                out.setdefault(f'{m.rel}::{q}::{key[0]}.{key[1]}', []).append(node.lineno)
    return out
