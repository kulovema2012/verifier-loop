#!/usr/bin/env node
// verifier-loop: installs the verifier-loop skill for Claude Code and Codex.
//
//   npx verifier-loop install   [--scope user|project] [--project DIR] [--only claude|codex] [--dry-run] [--home DIR]
//                                      [--keep-legacy] [--codex-home DIR]
//   npx verifier-loop verify    [--scope user|project] [--project DIR] [--only claude|codex] [--home DIR]
//   npx verifier-loop uninstall [--scope user|project] [--project DIR] [--only claude|codex] [--dry-run] [--home DIR]
//
// Claude Code reads skills from .claude/skills and Codex from .agents/skills. The skill is identical for both,
// so the same folder is copied to each. An existing copy is moved to a timestamped backup before it is replaced.
// Old copies in <codex home>/skills are backed up and removed too (--keep-legacy skips that), and copies synced
// from a claude.ai account are reported, since only claude.ai can update them.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const NAME = 'verifier-loop';
const BUNDLE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(BUNDLE, 'payload', NAME);
const argv = process.argv.slice(2);
const command = argv[0] === undefined || argv[0].startsWith('--') ? 'install' : argv[0];
const hasFlag = (name) => argv.includes(name);
const optionValue = (name) => {
  const i = argv.indexOf(name);
  return i === -1 ? undefined : argv[i + 1];
};

const DRY = hasFlag('--dry-run');
const ONLY = optionValue('--only');
const SCOPE = optionValue('--scope') ?? 'user';
const HOME = path.resolve(optionValue('--home') ?? os.homedir());
const ROOT = SCOPE === 'project' ? path.resolve(optionValue('--project') ?? process.cwd()) : HOME;
const BACKUP_DIR = path.join(HOME, `.${NAME}-backups`, new Date().toISOString().replace(/[:.]/g, '-'));

if (!['user', 'project'].includes(SCOPE)) fail(`--scope must be user or project, got ${SCOPE}`);
if (ONLY && !['claude', 'codex'].includes(ONLY)) fail(`--only must be claude or codex, got ${ONLY}`);

const KEEP_LEGACY = hasFlag('--keep-legacy');

const TARGETS = [
  { tool: 'Claude Code', key: 'claude', dir: path.join(ROOT, '.claude', 'skills', NAME) },
  { tool: 'Codex', key: 'codex', dir: path.join(ROOT, '.agents', 'skills', NAME) },
].filter((t) => !ONLY || t.key === ONLY);

// Older Codex versions (and some setups) read skills from <codex home>/skills. A copy left there makes Codex load
// the skill twice, old and new, so install and uninstall move it to the backup folder. Orca and other launchers
// point CODEX_HOME somewhere else, so that home is checked too.
function legacyCodexCopies() {
  if (ONLY === 'claude') return [];
  // With --home (e.g. a test sandbox) the real CODEX_HOME is ignored so nothing outside that home is touched.
  const codexHome = optionValue('--codex-home') ?? (optionValue('--home') ? undefined : process.env.CODEX_HOME);
  const homes = SCOPE === 'project'
    ? [path.join(ROOT, '.codex')]
    : [path.join(HOME, '.codex'), codexHome].filter(Boolean);
  const seen = new Set();
  return homes
    .map((h) => path.join(path.resolve(h), 'skills', NAME))
    .filter((d) => !seen.has(d.toLowerCase()) && seen.add(d.toLowerCase()) && exists(d));
}

// Skills added to a claude.ai account are synced into ~/.claude/skills/synced/<account>/<name>. Claude Code loads
// them next to local skills, and the sync restores anything deleted locally, so they can only be reported.
function syncedClaudeCopies() {
  if (ONLY === 'codex' || SCOPE === 'project') return [];
  const root = path.join(HOME, '.claude', 'skills', 'synced');
  if (!exists(root)) return [];
  return fs.readdirSync(root, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => path.join(root, e.name, NAME))
    .filter((d) => exists(path.join(d, 'SKILL.md')));
}

function warnSynced(copies) {
  for (const d of copies) {
    console.log(`! claude.ai account copy found: ${d}`);
    console.log('  Claude Code loads it alongside this install and the claude.ai sync restores it if deleted here.');
    console.log(`  To finish updating, open claude.ai > Settings > Capabilities > Skills and replace or delete "${NAME}".`);
  }
}

function fail(msg) {
  console.error(`${NAME}: ${msg}`);
  process.exit(1);
}

function listFiles(dir, base = dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === '__pycache__') continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listFiles(full, base));
    else out.push(path.relative(base, full));
  }
  return out.sort();
}

const digest = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

function exists(p) {
  try {
    fs.lstatSync(p);
    return true;
  } catch {
    return false;
  }
}

function backup(dest) {
  // Keep the backup inside BACKUP_DIR even for folders outside ROOT (e.g. a CODEX_HOME under AppData).
  let rel = path.relative(ROOT, dest);
  if (rel.startsWith('..') || path.isAbsolute(rel)) rel = path.join('_outside', dest.replace(/^[A-Za-z]:/, (d) => d[0]));
  const to = path.join(BACKUP_DIR, rel);
  if (DRY) return console.log(`  would back up ${dest} -> ${to}`);
  fs.mkdirSync(path.dirname(to), { recursive: true });
  const stat = fs.lstatSync(dest);
  if (stat.isSymbolicLink()) {
    // A link (e.g. a junction to a shared skills folder): remove only the link, never its target.
    fs.writeFileSync(`${to}.link.txt`, fs.readlinkSync(dest));
    fs.rmSync(dest, { recursive: false, force: true });
  } else {
    fs.cpSync(dest, to, { recursive: true });
    fs.rmSync(dest, { recursive: true, force: true });
  }
  console.log(`  backed up existing copy to ${to}`);
}

function sameAsPackage(dir, files) {
  try {
    return fs.statSync(dir).isDirectory() &&
      files.every((f) => exists(path.join(dir, f)) && digest(path.join(dir, f)) === digest(path.join(SRC, f)));
  } catch {
    return false; // e.g. a dangling link
  }
}

function realpathOrNull(p) {
  try {
    return fs.realpathSync(p).toLowerCase();
  } catch {
    return null;
  }
}

// The other install target this one is a link to, if any. Every target is checked, not only the ones --only
// selected, so an --only claude install never replaces a link into the Codex folder.
function linkedTarget(t) {
  if (!exists(t.dir) || !fs.lstatSync(t.dir).isSymbolicLink()) return null;
  const real = realpathOrNull(t.dir);
  return [
    { tool: 'Claude Code', dir: path.join(ROOT, '.claude', 'skills', NAME) },
    { tool: 'Codex', dir: path.join(ROOT, '.agents', 'skills', NAME) },
  ].find((o) => o.dir !== t.dir && real && realpathOrNull(o.dir) === real && !fs.lstatSync(o.dir).isSymbolicLink()) ?? null;
}

function install() {
  const files = listFiles(SRC);
  console.log(`${DRY ? '[dry run] ' : ''}Installing ${NAME} (${SCOPE} scope, ${files.length} files)`);
  for (const target of TARGETS) {
    let t = target;
    console.log(`- ${t.tool}: ${t.dir}`);
    // A link from one target to another (e.g. ~/.claude/skills/x -> ~/.agents/skills/x, a single-source setup)
    // is kept as is: updating the folder it points to updates both tools.
    const other = linkedTarget(t);
    if (other && TARGETS.some((o) => o.dir === other.dir)) {
      console.log(`  kept: it links to the ${other.tool} copy, which is updated below`);
      continue;
    }
    if (other) {
      console.log(`  kept the link; updating the folder it points to: ${other.dir}`);
      t = other;
    }
    if (exists(t.dir)) {
      if (sameAsPackage(t.dir, files)) {
        console.log('  already up to date');
        continue;
      }
      backup(t.dir);
      if (!DRY) fs.cpSync(SRC, t.dir, { recursive: true });
      console.log(`  ${DRY ? 'would update' : 'updated'} (the previous copy is in the backup above)`);
      continue;
    }
    if (!DRY) fs.cpSync(SRC, t.dir, { recursive: true });
    console.log(`  ${DRY ? 'would install' : 'installed'}`);
  }
  for (const d of legacyCodexCopies()) {
    if (KEEP_LEGACY) {
      console.log(`! old Codex copy left in place (--keep-legacy): ${d}`);
      continue;
    }
    console.log(`- Old Codex copy: ${d}`);
    backup(d);
    console.log(`  ${DRY ? 'would remove' : 'removed'} so Codex loads only the new copy`);
  }
  warnSynced(syncedClaudeCopies());
  if (!DRY) console.log(`Done. Restart Claude Code / Codex, then ask an agent to "grind until it matches" or run \`${NAME} verify\`.`);
  console.log('The bundled scripts need Python 3; pixel_diff.py also needs `pip install pillow numpy`.');
}

function verify() {
  const files = listFiles(SRC);
  let ok = true;
  for (const t of TARGETS) {
    const missing = files.filter((f) => !exists(path.join(t.dir, f)));
    const changed = files.filter((f) => !missing.includes(f) && digest(path.join(t.dir, f)) !== digest(path.join(SRC, f)));
    const good = missing.length === 0 && changed.length === 0;
    ok &&= good;
    console.log(`${good ? 'OK  ' : 'FAIL'} ${t.tool}: ${t.dir}`);
    for (const f of missing) console.log(`       missing: ${f}`);
    for (const f of changed) console.log(`       differs from package: ${f}`);
  }
  for (const d of legacyCodexCopies()) {
    ok = false;
    console.log(`FAIL old Codex copy still present (Codex loads it too): ${d} (run \`${NAME} install\` to remove it)`);
  }
  const synced = syncedClaudeCopies();
  for (const d of synced) {
    const current = sameAsPackage(d, listFiles(SRC));
    if (!current) ok = false;
    console.log(`${current ? 'OK  ' : 'FAIL'} claude.ai account copy ${current ? 'matches the package' : 'is an older version'}: ${d}`);
  }
  if (synced.some((d) => !sameAsPackage(d, listFiles(SRC)))) warnSynced(synced);
  process.exit(ok ? 0 : 1);
}

function uninstall() {
  for (const t of TARGETS) {
    if (!exists(t.dir)) {
      console.log(`- ${t.tool}: not installed`);
      continue;
    }
    console.log(`- ${t.tool}: ${t.dir}`);
    backup(t.dir);
  }
  if (!KEEP_LEGACY) {
    for (const d of legacyCodexCopies()) {
      console.log(`- Old Codex copy: ${d}`);
      backup(d);
    }
  }
  warnSynced(syncedClaudeCopies());
  console.log(DRY ? '[dry run] nothing removed' : 'Removed. Backups are kept so nothing is lost.');
}

if (!fs.existsSync(path.join(SRC, 'SKILL.md'))) fail(`payload missing: ${SRC}`);
const commands = { install, verify, uninstall };
if (!Object.hasOwn(commands, command)) fail(`unknown command "${command}" (install | verify | uninstall)`);
commands[command]();
