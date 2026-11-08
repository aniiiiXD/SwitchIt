#!/usr/bin/env node
/**
 * switchit-skill installer — copies the $switchit agent skill into the
 * user's ~/.agents/skills directory so any compatible agent can invoke it.
 *
 * Zero dependencies. Node 18+.
 */

const fs = require("fs");
const os = require("os");
const path = require("path");

const source = path.join(__dirname, "..", "skills", "switchit", "SKILL.md");
const targetDir = path.join(os.homedir(), ".agents", "skills", "switchit");
const target = path.join(targetDir, "SKILL.md");

try {
  if (!fs.existsSync(source)) {
    console.error("error: SKILL.md missing from the package (broken install?)");
    process.exit(1);
  }
  fs.mkdirSync(targetDir, { recursive: true });

  if (fs.existsSync(target) && fs.readFileSync(target, "utf8") === fs.readFileSync(source, "utf8")) {
    console.log(`The switchit skill is already installed and up to date:`);
    console.log(`  ${target}`);
    console.log(`Invoke it from any project with $switchit.`);
    process.exit(0);
  }

  fs.copyFileSync(source, target);
  console.log(`Installed the switchit skill:`);
  console.log(`  ${target}`);
  console.log(`Invoke it from any project with $switchit.`);
  console.log("");
  console.log("Optional: install the Python harness for the full pipeline:");
  console.log("  pip install switchit-harness");
  console.log("  switchit run --demo");
} catch (error) {
  console.error(`error: ${error.message}`);
  process.exit(1);
}
