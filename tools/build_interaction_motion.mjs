/** Build editable Lottie scenes and portable alpha videos with the reference's Skottie renderer.
 * node tools/build_interaction_motion.mjs <diffusionstudio/lottie checkout> <ffmpeg path>
 * Authoring dependencies belong to that temporary checkout, never to the PeakCut server.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const player = path.resolve(process.argv[2] || "");
const require = createRequire(path.join(player, "package.json"));
const CanvasKitInit = require("canvaskit-wasm/full");
const ck = await CanvasKitInit({ locateFile: name => path.join(path.dirname(require.resolve("canvaskit-wasm/full")), name) });
const output = path.join(root, "studio/assets/interactions");
const font = fs.readFileSync(path.join(root, "studio/fonts/Poppins-ExtraBold.ttf"));
const RED = [1, 0, 0, 1], WHITE = [1, 1, 1, 1], BLACK = [.059, .059, .059, 1];
const W = 800, H = 240, FPS = 30, FRAMES = 120;
const stat = k => ({ a: 0, k });
function animated(frames) {
  return { a: 1, k: frames.map(([t, value], i) => ({ t, s: Array.isArray(value) ? value : [value],
    ...(i < frames.length - 1 ? { o: { x: [.2], y: [.75] }, i: { x: [.34], y: [.94] } } : {}) })) };
}
const transform = (extra = {}) => ({ o: stat(100), r: stat(0), p: stat([0, 0, 0]), a: stat([0, 0, 0]), s: stat([100, 100, 100]), ...extra });
const fill = color => ({ ty: "fl", c: stat(color), o: stat(100), r: 1 });
const stroke = (color, width = 4) => ({ ty: "st", c: stat(color), o: stat(100), w: stat(width), lc: 2, lj: 2 });
const rect = (x, y, w, h, radius = 18) => ({ ty: "rc", p: stat([x, y]), s: stat([w, h]), r: stat(radius), d: 1 });
const ellipse = (x, y, size) => ({ ty: "el", p: stat([x, y]), s: stat([size, size]), d: 1 });
const polygon = (points, closed = true) => ({ ty: "sh", ks: stat({ v: points, i: points.map(() => [0, 0]), o: points.map(() => [0, 0]), c: closed }) });
const copy = {
  "pt-BR": { subscribe: ["INSCREVA-SE", "INSCRITO"], like: ["Deixe seu like", "Like ativado"],
    comment: ["Deixe seu comentário", "Gostei deste vídeo!", "Comentário enviado"],
    share: ["Compartilhe o vídeo", "Compartilhado"], save: ["Salve este vídeo", "Vídeo salvo"] },
  en: { subscribe: ["SUBSCRIBE", "SUBSCRIBED"], like: ["Like this video", "Like added"],
    comment: ["Leave a comment", "Loved this video!", "Comment sent"],
    share: ["Share this video", "Shared"], save: ["Save this video", "Video saved"] },
};

function scene(action, language) {
  const layers = []; let next = 2;
  const text = copy[language][action];
  const click = action === "comment" ? 88 : 48;
  const add = (name, shapes, ip = 0, op = FRAMES, ks = {}) => {
    layers.push({ ddd: 0, ind: next++, ty: 4, nm: name, parent: 1, sr: 1, ks: transform(ks), ao: 0,
      shapes, ip, op, st: 0, bm: 0 });
  };
  function label(name, value, x, y, size = 40, color = BLACK, ip = 0, op = FRAMES) {
    const document = t => ({ t, f: "Poppins", s: size, j: 0, tr: 0, lh: size * 1.25, ls: 0, fc: color.slice(0, 3) });
    layers.push({ ddd: 0, ind: next++, ty: 5, nm: name, parent: 1, sr: 1,
      ks: transform({ p: stat([x, y, 0]) }), ao: 0, ip, op, st: 0, bm: 0,
      t: { d: { k: Array.isArray(value) ? value.map(([t, value]) => ({ t, s: document(value) })) : [{ t: 0, s: document(value) }] },
        m: { g: 1, a: stat([0, 0]) }, a: [] } });
  }
  const check = (x, y, ip) => add("Confirmation tick", [polygon([[x - 13, y], [x - 3, y + 11], [x + 17, y - 13]], false), stroke(RED, 6)], ip);
  const play = (color, ip, op) => add("Play symbol", [polygon([[135, 101], [135, 140], [168, 120]]), fill(color)], ip, op);
  if (action === "subscribe") {
    add("Subscribe button", [rect(400, 120, 640, 124, 24), fill(RED)], 0, click + 5);
    play(WHITE, 0, click + 5);
    label("Subscribe invitation", text[0], 215, 137, 43, WHITE, 0, click + 5);
    add("Subscribed state", [rect(400, 120, 640, 124, 24), fill(WHITE)], click + 5);
    add("YouTube red badge", [rect(150, 120, 78, 64, 18), fill(RED)], click + 5);
    play(WHITE, click + 5, FRAMES);
    label("Subscribed confirmation", text[1], 235, 137, 43, BLACK, click + 5);
    check(642, 120, click + 5);
  } else {
    add("Readable white surface", [rect(400, 120, 676, action === "comment" ? 148 : 124, 24), fill(WHITE)]);
    if (action === "like") {
      const thumb = [[124, 141], [124, 108], [137, 108], [150, 80], [161, 80], [166, 88], [160, 109], [186, 109], [194, 118], [181, 157], [148, 157], [137, 148], [124, 148]];
      add("Thumb outline", [polygon(thumb), stroke(RED, 5)], 0, click + 5);
      add("Thumb activated", [polygon(thumb), fill(RED)], click + 5);
      add("Thumb cuff", [rect(113, 130, 13, 47, 3), fill(RED)]);
    } else if (action === "save") {
      const bookmark = [[127, 86], [181, 86], [181, 158], [154, 141], [127, 158]];
      add("Bookmark outline", [polygon(bookmark), stroke(RED, 5)], 0, click + 5);
      add("Bookmark saved", [polygon(bookmark), fill(RED)], click + 5);
    } else if (action === "share") {
      add("Share arrow", [polygon([[119, 150], [128, 119], [158, 107], [158, 84], [198, 121], [158, 157], [158, 134], [138, 136]]), fill(RED)], 0, click + 9);
      add("Share arrow travel", [polygon([[119, 150], [128, 119], [158, 107], [158, 84], [198, 121], [158, 157], [158, 134], [138, 136]]), fill(RED)], click + 1, click + 16,
        { p: animated([[click + 1, [0, 0, 0]], [click + 16, [55, -24, 0]]]), o: animated([[click + 1, 100], [click + 16, 0]]) });
      check(154, 120, click + 16);
    } else {
      add("Comment bubble", [polygon([[117, 86], [186, 86], [186, 138], [151, 138], [128, 160], [128, 138], [117, 138]]), stroke(RED, 5)]);
      add("Comment text lines", [polygon([[132, 103], [171, 103]], false), polygon([[132, 119], [159, 119]], false), stroke(RED, 4)]);
    }
    if (action === "comment") {
      label("Comment invitation", text[0], 230, 94, 25, BLACK, 0, click + 5);
      const typing = [[0, ""]];
      for (let i = 0; i <= text[1].length; i++) typing.push([28 + i * 48 / text[1].length, text[1].slice(0, i) + "|"]);
      typing.push([81, text[1]]);
      label("Typed comment", typing, 230, 144, 31, BLACK, 0, click + 5);
      add("Send button", [ellipse(663, 124, 46), fill(RED)], 0, click + 5);
      add("Send symbol", [polygon([[654, 111], [676, 124], [654, 137], [658, 124]]), fill(WHITE)], 0, click + 5);
      label("Comment confirmation", text[2], 230, 137, 34, BLACK, click + 5);
      check(663, 122, click + 5);
    } else {
      label("Invitation", text[0], 230, 135, action === "share" ? 34 : 39, BLACK, 0, click + 5);
      label("Confirmation", text[1], 230, 135, 38, BLACK, click + 5);
      check(654, 120, click + 5);
    }
  }
  // The ring is a response to a click, rather than a looping decoration.
  const target = action === "comment" ? [663, 124] : action === "subscribe" ? [588, 132] : [164, 128];
  add("Click response", [ellipse(0, 0, 36), stroke(RED, 3)], click, click + 15,
    { p: stat([...target, 0]), s: animated([[click, [60, 60, 100]], [click + 15, [190, 190, 100]]]),
      o: animated([[click, 70], [click + 15, 0]]) });
  const pointer = [[0, 0], [0, 46], [12, 35], [23, 56], [33, 51], [22, 30], [39, 28]];
  add("Pointer press and release", [polygon(pointer), fill(WHITE), stroke(BLACK, 3)], 17, 104,
    { p: animated([[17, [738, 205, 0]], [click - 8, [...target, 0]], [click + 16, [...target, 0]], [104, [732, 207, 0]]]),
      s: animated([[17, [100, 100, 100]], [click - 2, [100, 100, 100]], [click + 2, [82, 82, 100]], [click + 8, [100, 100, 100]]]),
      o: animated([[17, 0], [22, 100], [96, 100], [104, 0]]) });
  layers.push({ ddd: 0, ind: 1, ty: 3, nm: "Entrance, settle, hold, exit", sr: 1, ao: 0, ip: 0, op: FRAMES, st: 0,
    ks: transform({ a: stat([400, 120, 0]),
      p: animated([[0, [400, 156, 0]], [16, [400, 120, 0]], [104, [400, 120, 0]], [120, [420, 100, 0]]]),
      o: animated([[0, 0], [7, 100], [105, 100], [120, 0]]) }) });
  return { v: "5.12.2", fr: FPS, ip: 0, op: FRAMES, w: W, h: H, nm: `YouTube / ${action} / ${language}`,
    assets: [], fonts: { list: [{ fName: "Poppins", fFamily: "Poppins ExtraBold", fStyle: "ExtraBold", ascent: 75 }] }, layers: layers.reverse() };
}

fs.mkdirSync(output, { recursive: true });
for (const language of Object.keys(copy)) {
  for (const action of Object.keys(copy[language])) {
    const doc = scene(action, language);
    const json = JSON.stringify(doc);
    const directory = path.join(output, language);
    fs.mkdirSync(directory, { recursive: true });
    fs.writeFileSync(path.join(directory, `${action}.json`), json + "\n");
    const index = Object.keys(copy[language]).indexOf(action) + 1;
    const sceneDirectory = path.join(player, `public/projects/peakcut-${language.toLowerCase()}/scene-${index}`);
    fs.mkdirSync(sceneDirectory, { recursive: true });
    fs.writeFileSync(path.join(sceneDirectory, "lottie.json"), json);
    fs.writeFileSync(path.join(sceneDirectory, "Poppins-ExtraBold.ttf"), font);
    const anim = ck.MakeManagedAnimation(json, { "Poppins-ExtraBold.ttf": font });
    if (!anim) throw new Error(`Skottie rejected ${action}/${language}`);
    const surface = ck.MakeSurface(W, H);
    const canvas = surface.getCanvas();
    const frames = fs.mkdtempSync(path.join(os.tmpdir(), "pk-motion-"));
    try {
      for (let frame = 0; frame < FRAMES; frame++) {
        canvas.clear(ck.TRANSPARENT);
        anim.seekFrame(frame);
        anim.render(canvas, ck.LTRBRect(0, 0, W, H));
        surface.flush();
        const image = surface.makeImageSnapshot();
        fs.writeFileSync(path.join(frames, `${String(frame).padStart(4, "0")}.png`), image.encodeToBytes());
        image.delete();
      }
      const encoded = spawnSync(process.argv[3] || "ffmpeg", ["-y", "-v", "error", "-framerate", String(FPS),
        "-i", path.join(frames, "%04d.png"), "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0",
        "-crf", "22", "-deadline", "good", "-cpu-used", "4", "-auto-alt-ref", "0", "-an", path.join(directory, `${action}.webm`)],
        { windowsHide: true, encoding: "utf8" });
      if (encoded.status !== 0) throw new Error(encoded.stderr || "ffmpeg did not run");
    } finally {
      anim.delete(); surface.delete();
      // This is the exact directory created above, containing only generated frames.
      if (path.dirname(path.resolve(frames)) !== path.resolve(os.tmpdir()) || !path.basename(frames).startsWith("pk-motion-")) {
        throw new Error("Generated frame folder escaped the temporary directory");
      }
      fs.rmSync(frames, { recursive: true });
    }
    console.log(`Built ${language}/${action}`);
  }
}
const sounds = ["ribhavagrawal-woosh-230554.mp3", "universfield-computer-mouse-click-352734.mp3", "son_duquotidient-son-clavier-iphone-478600.mp3"];
fs.mkdirSync(path.join(output, "sounds"), { recursive: true });
for (const sound of sounds) fs.copyFileSync(path.join(root, "docs/efeitos-sonoros", sound), path.join(output, "sounds", sound));
