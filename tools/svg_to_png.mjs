/** Turn an SVG drawing from the AI into a normal transparent PNG.
  * node tools/svg_to_png.mjs <lottie-checkout> <drawing.svg> <picture.png>
  * Resvg lives in the authoring checkout, so the PeakCut server needs no new dependency.
  */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const [checkoutArg, inputArg, outputArg] = process.argv.slice(2);
const checkout = path.resolve(checkoutArg || "");
const require = createRequire(path.join(checkout, "package.json"));
let Resvg;
try {
  ({ Resvg } = require("@resvg/resvg-js"));
} catch {
  console.error("The Lottie checkout needs Resvg: run npm install @resvg/resvg-js there.");
  process.exit(2);
}
const svg = fs.readFileSync(path.resolve(inputArg || ""), "utf8");
const rendered = new Resvg(svg, { fitTo: { mode: "width", value: 220 } }).render();
fs.writeFileSync(path.resolve(outputArg || ""), rendered.asPng());
console.log("ok");
