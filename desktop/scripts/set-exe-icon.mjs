import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import { rcedit } from "rcedit";

async function main() {
  const [, , exeArg, iconArg] = process.argv;
  const exePath = path.resolve(String(exeArg || ""));
  const iconPath = path.resolve(String(iconArg || ""));

  if (!exeArg || !iconArg) {
    throw new Error("Usage: node set-exe-icon.mjs <exe-path> <icon-path>");
  }
  if (!fs.existsSync(exePath)) {
    throw new Error(`Executable not found: ${exePath}`);
  }
  if (!fs.existsSync(iconPath)) {
    throw new Error(`Icon not found: ${iconPath}`);
  }

  await rcedit(exePath, { icon: iconPath });
  process.stdout.write(`Updated exe icon: ${exePath}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error?.message || String(error)}\n`);
  process.exit(1);
});
