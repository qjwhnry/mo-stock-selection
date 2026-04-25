import { createRequire } from "module";
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";

const require = createRequire(import.meta.url);
const skill = require("./skill-entry.js");
const entryFile = fileURLToPath(import.meta.url);

if (!process.argv[1]) {
  process.argv[1] = entryFile;
}

export const authChecker = skill.authChecker;
export const mcpClient = skill.mcpClient;
export const editWatchlist = skill.editWatchlist;
export const stockMap = skill.stockMap;
export const runCli = skill.runCli;
export default skill;

function isDirectRun() {
  const entryArg = process.argv[1] || entryFile;
  if (!entryArg) return false;
  return pathToFileURL(path.resolve(entryArg)).href === import.meta.url;
}

if (isDirectRun()) {
  Promise.resolve(runCli()).catch((error) => {
    console.error(`错误: ${error.message}`);
    process.exit(1);
  });
}
