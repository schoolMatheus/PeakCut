import test from "node:test";
import assert from "node:assert/strict";
import {
  jobSchema,
  timelineSchema,
  isAllowedRequest,
} from "../src/peakcut/schema.mjs";
import {
  transitionStyle,
  motionStyle,
  activeWords,
} from "../src/peakcut/timing.mjs";

const timeline = () => ({
  schemaVersion: "2.0",
  format: {
    width: 1080,
    height: 1920,
    fps: 30,
    durationInFrames: 300,
    safeAreaProfile: "shorts-default-v1",
  },
  assets: [
    { id: "a", kind: "image", file: "assets/a.png" },
    { id: "b", kind: "image", file: "assets/b.png" },
  ],
  tracks: [
    {
      id: "visual",
      type: "video",
      clips: [
        { id: "first", assetId: "a", startFrame: 0, durationInFrames: 180 },
        { id: "second", assetId: "b", startFrame: 171, durationInFrames: 129 },
      ],
    },
  ],
  transitions: [
    {
      id: "fade",
      fromClipId: "first",
      toClipId: "second",
      type: "crossfade",
      startFrame: 171,
      durationInFrames: 9,
    },
  ],
});

test("canonical overlap retains 300 absolute frames and fills clip defaults", () => {
  const parsed = timelineSchema.parse(timeline());
  assert.equal(parsed.format.durationInFrames, 300);
  assert.equal(parsed.tracks[0].clips[1].startFrame, 171);
  assert.deepEqual(parsed.tracks[0].clips[0].focus, { x: 0.5, y: 0.5 });
});
test("rejects unknown executable input, invalid references, gaps and undeclared overlaps", () => {
  for (const change of [
    (t) => (t.code = "process.exit()"),
    (t) => (t.tracks[0].clips[0].assetId = "missing"),
    (t) => (t.tracks[0].clips[1].startFrame = 181),
    (t) => (t.transitions = []),
    (t) => t.transitions.push({ ...t.transitions[0], id: "duplicate" }),
    (t) => (t.tracks[0].clips[0].audioStartSeconds = 0),
  ]) {
    const t = timeline();
    change(t);
    assert.equal(timelineSchema.safeParse(t).success, false);
  }
});
test("precise audio timing retains seconds and rejects overflow", () => {
  const t = timeline();
  t.assets.push({ id: "voice", kind: "audio", file: "audio.wav" });
  t.tracks.push({
    id: "narration",
    type: "audio",
    role: "narration",
    clips: [
      {
        id: "speech",
        assetId: "voice",
        startFrame: 0,
        durationInFrames: 300,
        audioStartSeconds: 0.001,
        audioDurationSeconds: 9.999,
      },
    ],
  });
  assert.equal(
    timelineSchema.parse(t).tracks[1].clips[0].audioStartSeconds,
    0.001,
  );
  t.tracks[1].clips[0].audioDurationSeconds = 10;
  assert.equal(timelineSchema.safeParse(t).success, false);
});
test("job only permits one loopback asset origin, fixed heights and relative assets", () => {
  const job = {
    timeline: timeline(),
    media: { a: "http://127.0.0.1:1234/a", b: "http://127.0.0.1:1234/b" },
    fontUrl: "http://127.0.0.1:1234/font.ttf",
    height: 320,
    output: process.cwd() + "/out.mp4",
    bundleDir: process.cwd() + "/cache",
  };
  assert.equal(jobSchema.safeParse(job).success, true);
  for (const change of [
    (j) => (j.media.a = "https://example.com/a"),
    (j) => (j.media.a = "http://127.0.0.1:9999/a"),
    (j) => (j.height = 1080),
    (j) => (j.timeline.assets[0].file = "../secret"),
  ]) {
    const j = structuredClone(job);
    change(j);
    assert.equal(jobSchema.safeParse(j).success, false);
  }
});
test("request policy blocks foreign hosts, ports, file URLs and credential URLs", () => {
  const origins = new Set(["http://127.0.0.1:1234"]);
  assert.equal(isAllowedRequest("http://127.0.0.1:1234/a", origins), true);
  for (const url of [
    "https://example.com/a",
    "http://127.0.0.1:80/a",
    "file:///secret",
    "http://user:pass@127.0.0.1:1234/a",
  ])
    assert.equal(isAllowedRequest(url, origins), false);
});
test("crossfade and slide use declared frames exactly once", () => {
  const t = timeline().transitions;
  assert.deepEqual(transitionStyle("second", 171, t), {
    opacity: 0,
    translateX: 0,
  });
  assert.equal(transitionStyle("second", 175.5, t).opacity, 0.5);
  // Incoming alpha already fades the outgoing picture; fading it again makes a black dip.
  assert.equal(transitionStyle("first", 175.5, t).opacity, 1);
  assert.equal(transitionStyle("first", 180, t).opacity, 0);
  const slide = [{ ...t[0], type: "slide_left" }];
  assert.equal(transitionStyle("second", 175.5, slide).translateX, 50);
  assert.equal(transitionStyle("first", 175.5, slide).translateX, -50);
});
test("motion and karaoke are frame based, end exclusive", () => {
  assert.equal(
    motionStyle({ id: "zoom_in", scaleFrom: 1, scaleTo: 1.2 }, 0, 31).scale,
    1,
  );
  assert.equal(
    motionStyle({ id: "zoom_in", scaleFrom: 1, scaleTo: 1.2 }, 30, 31).scale,
    1.2,
  );
  const words = [
    { text: "A", startFrame: 10, endFrame: 20 },
    { text: "B", startFrame: 20, endFrame: 30 },
  ];
  assert.deepEqual(activeWords(words, 20), [false, true]);
  const pan = motionStyle(
    { id: "pan_left", scaleFrom: 1, scaleTo: 1.08 },
    0,
    31,
  );
  assert.ok(pan.scale >= 1 + (2 * Math.abs(pan.translateX)) / 100);
});
