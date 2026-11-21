// Writes src/schema.d.ts from openapi.json (made by `python -m orchestrator.openapi`); with --check,
// fails instead when src/schema.d.ts is not what openapi.json gives (npm test runs it).
import { readFileSync, writeFileSync } from "node:fs";
import openapiTS, { astToString } from "openapi-typescript";

const here = (name) => new URL(`../${name}`, import.meta.url);
const header = "// Generated from openapi.json by scripts/api-types.mjs (npm run api-types): do not edit.\n";
const types = header + astToString(await openapiTS(JSON.parse(readFileSync(here("openapi.json"), "utf8"))));

if (!process.argv.includes("--check")) {
  writeFileSync(here("src/schema.d.ts"), types);
} else if (readFileSync(here("src/schema.d.ts"), "utf8") !== types) {
  console.error("src/schema.d.ts is not what openapi.json gives: run npm run api-types");
  process.exit(1);
}
