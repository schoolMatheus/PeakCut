/** Render one custom interaction scene with the official Skia Skottie engine.
  * node tools/render_custom_interaction.mjs <lottie-checkout> <scene.json> <out-dir> <ffmpeg> [image] [--webm]
  *
  * Authoring dependencies live in the separate diffusionstudio/lottie checkout, never in the PeakCut server.
  * Drafts write the pinned frames used for the approval filmstrip; approved motions also get an alpha WebM.
  */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const [checkoutArg, sceneArg, outArg, ffmpegArg, ...rest] = process.argv.slice(2);
const webm = rest.includes("--webm");
const imageArg = rest.find(x => x !== "--webm") || "";
const checkout = path.resolve(checkoutArg || "");
const sceneFile = path.resolve(sceneArg || "");
const out = path.resolve(outArg || "");
const require = createRequire(path.join(checkout, "package.json"));
const CanvasKitInit = require("canvaskit-wasm/full");
const ck = await CanvasKitInit({
  locateFile: name => path.join(path.dirname(require.resolve("canvaskit-wasm/full")), name),
});

const raw = fs.readFileSync(sceneFile, "utf8");
if (raw.length > 2_000_000) throw new Error("That scene is too big to render.");
const doc = JSON.parse(raw);
if (!doc || typeof doc !== "object" || !Array.isArray(doc.layers) || !doc.layers.length) {
  throw new Error("The scene has no animation layers.");
}
const width = Number(doc.w), height = Number(doc.h), frames = Number(doc.op), fps = Number(doc.fr);
if (width !== 800 || height !== 240 || frames !== 120 || fps !== 30) {
  throw new Error("A custom interaction must be 800x240, 30 fps and 120 frames.");
}

const assets = {};
// Native Lottie text needs the font beside the scene; use the bundled caption font from PeakCut.
const fontFile = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "studio", "fonts", "Poppins-ExtraBold.ttf");
assets["Poppins-ExtraBold.ttf"] = new Uint8Array(fs.readFileSync(fontFile));
if (imageArg) {
  const image = path.resolve(imageArg);
  const name = path.basename(image);
  if (!/^[\w\-]+\.(png|jpe?g|webp)$/i.test(name)) throw new Error("The picture has an unexpected name.");
  assets[name] = new Uint8Array(fs.readFileSync(image));
  const names = doc.assets.filter(a => a.p === name).map(a => a.id);
  if (!names.length) throw new Error("The scene does not use the uploaded picture.");
}
const anim = ck.MakeManagedAnimation(raw, assets);
if (!anim) throw new Error("Skottie could not read that scene.");
const surface = ck.MakeSurface(width, height);
const canvas = surface.getCanvas();
fs.mkdirSync(out, { recursive: true });

// Spread the filmstrip over the whole motion: first pose, early beat, middle, settle and exit.
const pinned = new Set([0, 12, 24, 36, 48, 60, 72, 84, 96, 108, 119]);
const written = [];
try {
  for (let frame = 0; frame < frames; frame++) {
    const needed = webm || pinned.has(frame);
    if (!needed) continue;
    canvas.clear(ck.TRANSPARENT);
    anim.seekFrame(frame);
    anim.render(canvas, ck.LTRBRect(0, 0, width, height));
    surface.flush();
    const image = surface.makeImageSnapshot();
    const name = `frame-${String(frame).padStart(4, "0")}.png`;
    fs.writeFileSync(path.join(out, name), image.encodeToBytes());
    image.delete();
    written.push(name);
  }
  if (webm) {
    const encoded = spawnSync(ffmpegArg || "ffmpeg", ["-y", "-v", "error", "-framerate", String(fps),
      "-i", path.join(out, "frame-%04d.png"), "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0",
      "-crf", "22", "-deadline", "good", "-cpu-used", "4", "-auto-alt-ref", "0", "-an",
      path.join(out, "motion.webm")], { windowsHide: true, encoding: "utf8" });
    if (encoded.status !== 0) throw new Error(encoded.stderr || "ffmpeg did not run");
  }
} finally {
  anim.delete();
  surface.delete();
}
console.log(JSON.stringify({ frames: written, webm: webm ? "motion.webm" : "" }));
