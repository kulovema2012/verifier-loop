#!/usr/bin/env node
// verifier-loop: installs the verifier-loop skill for Claude Code and Codex.
//
//   npx verifier-loop install   [--scope user|project] [--project DIR] [--only claude|codex] [--dry-run] [--home DIR]
//   npx verifier-loop verify    [--scope user|project] [--project DIR] [--only claude|codex] [--home DIR]
//   npx verifier-loop uninstall [--scope user|project] [--project DIR] [--only claude|codex] [--dry-run] [--home DIR]
//
// Claude Code reads skills from .claude/skills and Codex from .agents/skills. The skill is identical for both,
// so the same folder is copied to each. An existing copy is moved to a timestamped backup before it is replaced.
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

const TARGETS = [
  { tool: 'Claude Code', key: 'claude', dir: path.join(ROOT, '.claude', 'skills', NAME) },
  { tool: 'Codex', key: 'codex', dir: path.join(ROOT, '.agents', 'skills', NAME) },
].filter((t) => !ONLY || t.key === ONLY);

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
  const to = path.join(BACKUP_DIR, path.relative(ROOT, dest));
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

function install() {
  const files = listFiles(SRC);
  console.log(`${DRY ? '[dry run] ' : ''}Installing ${NAME} (${SCOPE} scope, ${files.length} files)`);
  for (const t of TARGETS) {
    console.log(`- ${t.tool}: ${t.dir}`);
    if (exists(t.dir)) {
      const same = fs.statSync(t.dir).isDirectory() &&
        files.every((f) => exists(path.join(t.dir, f)) && digest(path.join(t.dir, f)) === digest(path.join(SRC, f)));
      if (same) {
        console.log('  already up to date');
        continue;
      }
      backup(t.dir);
    }
    if (!DRY) fs.cpSync(SRC, t.dir, { recursive: true });
    console.log(`  ${DRY ? 'would copy' : 'copied'} skill`);
  }
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
  console.log(DRY ? '[dry run] nothing removed' : 'Removed. Backups are kept so nothing is lost.');
}

if (!fs.existsSync(path.join(SRC, 'SKILL.md'))) fail(`payload missing: ${SRC}`);
const commands = { install, verify, uninstall };
if (!Object.hasOwn(commands, command)) fail(`unknown command "${command}" (install | verify | uninstall)`);
commands[command]();
