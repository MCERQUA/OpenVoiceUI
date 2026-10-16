#!/usr/bin/env node
// install-skills.js — copy the bundled OpenVoiceUI agent skills (./skills) into
// OpenClaw's managed skills directory, under a subfolder this script owns:
//
//     <openclaw state dir>/skills/openvoiceui/<skill>/SKILL.md
//
// OpenClaw scans <state dir>/skills (default ~/.openclaw/skills) on its own, including
// one level of grouping folders, so no openclaw.json change is needed.
//
// Who uses this:
//   - Native installs (setup-sudo.sh runs it; re-run it after `git pull` to update).
//   - Anyone running OpenClaw outside Docker who wants the skills.
//   Docker / Pinokio / npm CLI installs do NOT copy anything: docker-compose mounts ./skills
//   read-only at /root/.openclaw/skills/openvoiceui, so `git pull` updates them.
//   Pinokio's install and update steps call it with --check to validate the bundle.
//
// Update rules (safe to re-run any number of times):
//   - new file            -> copied
//   - unchanged since the last install (hash matches the install manifest) -> updated
//   - edited locally      -> KEPT, with a warning (use --force to overwrite; the local
//                            copy is saved next to it as <file>.local-<timestamp> first)
//   - nothing is ever deleted; skills that are no longer bundled are left in place.
//   Your own skills belong in <workspace>/skills/<name>/ or ~/.openclaw/skills/<name>/,
//   never inside the openvoiceui/ subfolder. See docs/SKILLS.md.
//
// Usage:
//   node install-skills.js [--target DIR] [--dry-run] [--force] [--quiet]
//   node install-skills.js --check [--mountpoint DIR]
//     --check validates the bundle only; --mountpoint also creates the (empty) Docker mountpoint
//     dir, e.g. openclaw-data/skills/openvoiceui, so Docker does not create it root-owned.
//
// Exit codes: 0 ok · 1 install error · 2 invalid bundle · 64 bad arguments

"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");

const BUNDLE_DIR = path.resolve(__dirname, "skills");
const SUBDIR = "openvoiceui";
const MANIFEST = ".openvoiceui-skills.json";
const MAX_SKILL_MD_BYTES = 256000; // OpenClaw's default skills.limits.maxSkillFileBytes

function parseArgs(argv) {
  const opts = { target: "", mountpoint: "", dryRun: false, force: false, check: false, quiet: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--dry-run") opts.dryRun = true;
    else if (a === "--force") opts.force = true;
    else if (a === "--check") opts.check = true;
    else if (a === "--quiet") opts.quiet = true;
    else if (a === "--target") opts.target = argv[++i] || "";
    else if (a.startsWith("--target=")) opts.target = a.slice("--target=".length);
    else if (a === "--mountpoint") opts.mountpoint = argv[++i] || "";
    else if (a === "-h" || a === "--help") opts.help = true;
    else { opts.bad = a; }
  }
  return opts;
}

function expandHome(p, home) {
  if (p === "~") return home;
  if (p.startsWith("~/") || p.startsWith("~\\")) return path.join(home, p.slice(2));
  return p;
}

// Mirrors OpenClaw's state-dir resolution: OPENCLAW_STATE_DIR, else the directory of
// OPENCLAW_CONFIG_PATH, else <OPENCLAW_HOME or OS home>/.openclaw.
function resolveStateDir(env) {
  const osHome = os.homedir();
  const home = env.OPENCLAW_HOME && env.OPENCLAW_HOME.trim()
    ? path.resolve(expandHome(env.OPENCLAW_HOME.trim(), osHome))
    : osHome;
  if (env.OPENCLAW_STATE_DIR && env.OPENCLAW_STATE_DIR.trim()) {
    return path.resolve(expandHome(env.OPENCLAW_STATE_DIR.trim(), home));
  }
  if (env.OPENCLAW_CONFIG_PATH && env.OPENCLAW_CONFIG_PATH.trim()) {
    return path.dirname(path.resolve(expandHome(env.OPENCLAW_CONFIG_PATH.trim(), home)));
  }
  return path.join(home, ".openclaw");
}

function defaultTarget(env) {
  return path.join(resolveStateDir(env), "skills", SUBDIR);
}

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

// Every regular file under dir, as POSIX-style relative paths. Symlinks are rejected:
// OpenClaw refuses skill paths that resolve outside their root, so the bundle must be real files.
function listFiles(dir, rel = "", out = [], problems = []) {
  for (const ent of fs.readdirSync(path.join(dir, rel), { withFileTypes: true })) {
    if (ent.name === "__pycache__" || ent.name === ".DS_Store") continue;
    const r = rel ? rel + "/" + ent.name : ent.name;
    if (ent.isSymbolicLink()) problems.push(`${r}: symlinks are not allowed in the bundle`);
    else if (ent.isDirectory()) listFiles(dir, r, out, problems);
    else if (ent.isFile()) out.push(r);
  }
  return out;
}

function readFrontmatter(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n/.exec(text);
  if (!m) return null;
  const fm = {};
  for (const line of m[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
    if (kv) fm[kv[1]] = kv[2].replace(/^["']|["']$/g, "").trim();
  }
  return fm;
}

// Validate the bundle. Returns { skills: [names], problems: [strings] }.
function validateBundle(bundleDir) {
  const problems = [];
  if (!fs.existsSync(bundleDir) || !fs.statSync(bundleDir).isDirectory()) {
    return { skills: [], problems: [`bundle directory not found: ${bundleDir}`] };
  }
  const skills = [];
  for (const ent of fs.readdirSync(bundleDir, { withFileTypes: true })) {
    if (!ent.isDirectory() || ent.name.startsWith(".") || ent.name === "__pycache__") continue;
    const md = path.join(bundleDir, ent.name, "SKILL.md");
    if (!fs.existsSync(md)) { problems.push(`${ent.name}/: missing SKILL.md`); continue; }
    const size = fs.statSync(md).size;
    if (size > MAX_SKILL_MD_BYTES) problems.push(`${ent.name}/SKILL.md: ${size} bytes exceeds ${MAX_SKILL_MD_BYTES}`);
    const fm = readFrontmatter(fs.readFileSync(md, "utf8"));
    if (!fm) { problems.push(`${ent.name}/SKILL.md: no YAML frontmatter`); continue; }
    if (fm.name !== ent.name) problems.push(`${ent.name}/SKILL.md: frontmatter name "${fm.name || ""}" must equal the folder name`);
    if (!fm.description) problems.push(`${ent.name}/SKILL.md: frontmatter description is empty`);
    skills.push(ent.name);
  }
  listFiles(bundleDir, "", [], problems);
  if (skills.length === 0) problems.push("no skills found in bundle");
  return { skills: skills.sort(), problems };
}

function readManifest(target) {
  try {
    const m = JSON.parse(fs.readFileSync(path.join(target, MANIFEST), "utf8"));
    return m && typeof m.files === "object" && m.files ? m : { files: {} };
  } catch {
    return { files: {} };
  }
}

function sameDir(a, b) {
  try { return fs.realpathSync(a) === fs.realpathSync(b); } catch { return false; }
}

function copyAtomic(src, dest) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  const tmp = `${dest}.tmp-${process.pid}`;
  fs.copyFileSync(src, tmp);
  fs.chmodSync(tmp, fs.statSync(src).mode & 0o777);
  fs.renameSync(tmp, dest);
}

function install({ bundleDir = BUNDLE_DIR, target, dryRun = false, force = false, log = console.log }) {
  const result = { added: [], updated: [], unchanged: [], kept: [], forced: [], retired: [] };
  if (sameDir(bundleDir, target)) {
    log(`  Target is the bundle itself (${target}); nothing to copy.`);
    return result;
  }
  const manifest = readManifest(target);
  const files = listFiles(bundleDir).sort();
  const newHashes = Object.assign({}, manifest.files);
  const stamp = new Date().toISOString().replace(/[:.]/g, "").slice(0, 15);

  for (const rel of files) {
    const src = path.join(bundleDir, rel);
    const dest = path.join(target, rel);
    const srcHash = sha256(src);
    if (!fs.existsSync(dest)) {
      result.added.push(rel);
      if (!dryRun) copyAtomic(src, dest);
      newHashes[rel] = srcHash;
      continue;
    }
    const destHash = sha256(dest);
    if (destHash === srcHash) { result.unchanged.push(rel); newHashes[rel] = srcHash; continue; }
    if (manifest.files[rel] && destHash === manifest.files[rel]) {
      result.updated.push(rel);
      if (!dryRun) copyAtomic(src, dest);
      newHashes[rel] = srcHash;
      continue;
    }
    // Locally edited (or a pre-existing file this script never installed).
    if (force) {
      result.forced.push(rel);
      if (!dryRun) {
        fs.copyFileSync(dest, `${dest}.local-${stamp}`);
        copyAtomic(src, dest);
      }
      newHashes[rel] = srcHash;
    } else {
      result.kept.push(rel);
    }
  }

  const bundled = new Set(files);
  for (const rel of Object.keys(manifest.files)) {
    if (!bundled.has(rel) && fs.existsSync(path.join(target, rel))) result.retired.push(rel);
  }

  if (!dryRun) {
    fs.mkdirSync(target, { recursive: true });
    const out = {
      note: "Managed by OpenVoiceUI install-skills.js. Files here are replaced on update " +
            "unless you edited them. Put your own skills in ../<name>/ or <workspace>/skills/, not here.",
      version: 1,
      installed_at: new Date().toISOString(),
      files: newHashes,
    };
    fs.writeFileSync(path.join(target, MANIFEST), JSON.stringify(out, null, 2) + "\n");
  }
  return result;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const log = opts.quiet ? () => {} : console.log;
  if (opts.help || opts.bad) {
    if (opts.bad) console.error(`Unknown argument: ${opts.bad}`);
    console.log("Usage: node install-skills.js [--target DIR] [--dry-run] [--force] [--quiet]\n" +
                "       node install-skills.js --check [--mountpoint DIR]");
    process.exit(opts.bad ? 64 : 0);
  }

  const { skills, problems } = validateBundle(BUNDLE_DIR);
  if (problems.length) {
    console.error("  Bundled skills are invalid:");
    for (const p of problems) console.error(`    - ${p}`);
    process.exit(2);
  }

  if (opts.check) {
    log(`  Bundled OpenVoiceUI skills OK (${skills.length}): ${skills.join(", ")}`);
    if (opts.mountpoint) {
      fs.mkdirSync(path.resolve(opts.mountpoint), { recursive: true });
      log(`  Mountpoint ready: ${opts.mountpoint}`);
    }
    log("  Docker installs mount them read-only at /root/.openclaw/skills/openvoiceui");
    return;
  }

  const target = path.resolve(opts.target ? expandHome(opts.target, os.homedir()) : defaultTarget(process.env));
  log(`  ${opts.dryRun ? "[dry run] " : ""}Installing ${skills.length} OpenVoiceUI skills into ${target}`);
  let r;
  try {
    r = install({ target, dryRun: opts.dryRun, force: opts.force, log });
  } catch (e) {
    console.error(`  Skill install failed: ${e.message}`);
    process.exit(1);
  }
  for (const f of r.added) log(`    + ${f}`);
  for (const f of r.updated) log(`    ~ ${f}`);
  for (const f of r.forced) log(`    ! ${f} (overwritten; your copy saved as ${path.basename(f)}.local-*)`);
  for (const f of r.kept) console.warn(`    = ${f} (kept: edited locally; re-run with --force to replace it)`);
  for (const f of r.retired) console.warn(`    - ${f} (no longer bundled; left in place)`);
  log(`  Done: ${r.added.length} added, ${r.updated.length} updated, ${r.unchanged.length} unchanged, ` +
      `${r.kept.length} kept, ${r.forced.length} forced, ${r.retired.length} retired.`);
  if (!opts.dryRun) log("  OpenClaw picks up skill changes automatically (skills watcher), or on its next restart.");
}

if (require.main === module) main();

module.exports = { install, validateBundle, resolveStateDir, defaultTarget, BUNDLE_DIR, SUBDIR, MANIFEST };
