/* global URL */
import { z } from "zod";

export const CAPABILITIES = Object.freeze({
  templates: ["title_pop_v1", "lower_third_v1", "cta_v1", "comparison_v1"],
  captions: ["segment_v1", "karaoke_v1"],
  transitions: ["crossfade", "slide_left"],
  motions: [
    "none",
    "zoom_in",
    "zoom_out",
    "pan_left",
    "pan_right",
    "ken_burns_v1",
  ],
});
const id = z
  .string()
  .min(1)
  .max(80)
  .regex(/^[A-Za-z0-9_-]+$/);
const frame = z.number().int().min(0).max(216000);
const duration = z.number().int().min(1).max(216000);
const normalized = z.number().finite().min(0).max(1);
const point = z.strictObject({
  x: normalized.default(0.5),
  y: normalized.default(0.5),
});
const relativeFile = z
  .string()
  .min(1)
  .max(240)
  .refine(
    (value) =>
      !/^(?:[\\/]|[a-z]:|.*:)/i.test(value) &&
      !value.split(/[\\/]/).some((part) => part === ".." || part === "."),
    "Use a relative asset path without parent folders",
  );
const word = z.strictObject({
  text: z.string().min(1).max(100),
  startFrame: frame,
  endFrame: duration,
});
const clipSchema = z.strictObject({
  id,
  sceneId: z.string().default(""),
  assetId: z.string().default(""),
  startFrame: frame,
  durationInFrames: duration,
  sourceInSeconds: z.number().finite().min(0).max(86400).default(0),
  sourceOutSeconds: z
    .number()
    .finite()
    .min(0)
    .max(86400)
    .nullable()
    .default(null),
  audioStartSeconds: z
    .number()
    .finite()
    .min(0)
    .max(86400)
    .nullable()
    .default(null),
  audioDurationSeconds: z
    .number()
    .finite()
    .min(0)
    .max(86400)
    .nullable()
    .default(null),
  fit: z.enum(["cover", "contain"]).default("cover"),
  focus: point.default({ x: 0.5, y: 0.5 }),
  motion: z
    .strictObject({
      id: z.enum(CAPABILITIES.motions).default("none"),
      scaleFrom: z.number().finite().min(1).max(2).default(1),
      scaleTo: z.number().finite().min(1).max(2).default(1.08),
    })
    .default({ id: "none", scaleFrom: 1, scaleTo: 1.08 }),
  templateId: z.enum(CAPABILITIES.templates).default("title_pop_v1"),
  text: z.string().max(1200).default(""),
  position: point.default({ x: 0.5, y: 0.2 }),
  styleId: z.enum(CAPABILITIES.captions).default("segment_v1"),
  words: z.array(word).max(300).default([]),
  gainDb: z.number().finite().min(-80).max(12).default(0),
  fadeInFrames: frame.default(0),
  fadeOutFrames: frame.default(0),
  loop: z.boolean().default(false),
  opacity: normalized.default(1),
  fadeVisualFrames: frame.default(0),
});
export const timelineSchema = z
  .strictObject({
    schemaVersion: z.literal("2.0"),
    format: z.strictObject({
      width: z.literal(1080).default(1080),
      height: z.literal(1920).default(1920),
      fps: z.literal(30).default(30),
      durationInFrames: duration,
      safeAreaProfile: z
        .literal("shorts-default-v1")
        .default("shorts-default-v1"),
    }),
    assets: z
      .array(
        z.strictObject({
          id,
          kind: z.enum(["video", "image", "audio"]),
          file: relativeFile,
        }),
      )
      .min(1)
      .max(1000),
    tracks: z
      .array(
        z.strictObject({
          id,
          type: z.enum(["video", "text", "captions", "audio"]),
          layer: z.number().int().min(0).max(20).default(0),
          role: z
            .enum(["narration", "music", "sfx", "original"])
            .nullable()
            .default(null),
          duckUnder: z.string().nullable().default(null),
          clips: z.array(clipSchema).min(1).max(1000),
        }),
      )
      .min(1)
      .max(32),
    transitions: z
      .array(
        z.strictObject({
          id,
          fromClipId: id,
          toClipId: id,
          type: z.enum(CAPABILITIES.transitions),
          startFrame: frame,
          durationInFrames: duration.max(60),
        }),
      )
      .max(300)
      .default([]),
  })
  .superRefine((timeline, ctx) => {
    const issue = (path, message) =>
      ctx.addIssue({ code: "custom", path, message });
    const allIds = new Set();
    const register = (value, path) => {
      const key = (path.includes("clips") ? "clips" : path[0]) + ":" + value;
      if (allIds.has(key)) issue(path, "IDs must be unique");
      allIds.add(key);
    };
    const assets = new Map(
      timeline.assets.map((asset, index) => {
        register(asset.id, ["assets", index, "id"]);
        return [asset.id, asset];
      }),
    );
    const clips = new Map();
    const visuals = [];
    timeline.tracks.forEach((track, ti) => {
      register(track.id, ["tracks", ti, "id"]);
      track.clips.forEach((clip, ci) => {
        const path = ["tracks", ti, "clips", ci];
        register(clip.id, [...path, "id"]);
        clips.set(clip.id, { clip, track, path });
        if (
          track.type !== "audio" &&
          (clip.audioStartSeconds !== null ||
            clip.audioDurationSeconds !== null)
        )
          issue(path, "Precise audio times belong to audio tracks");
        if (track.type === "audio") {
          const audioStart = clip.audioStartSeconds ?? clip.startFrame / 30;
          const audioDuration =
            clip.audioDurationSeconds ?? clip.durationInFrames / 30;
          if (
            audioDuration <= 0 ||
            audioStart + audioDuration >
              timeline.format.durationInFrames / 30 + 1e-8
          )
            issue(path, "Audio exceeds the timeline duration");
        }
        if (
          clip.startFrame + clip.durationInFrames >
          timeline.format.durationInFrames
        )
          issue(path, "Clip runs past the timeline");
        if (
          Math.max(
            clip.fadeInFrames,
            clip.fadeOutFrames,
            clip.fadeVisualFrames,
          ) > clip.durationInFrames
        )
          issue(path, "Fades must fit inside the clip");
        if (
          clip.sourceOutSeconds != null &&
          clip.sourceOutSeconds <= clip.sourceInSeconds
        )
          issue(
            [...path, "sourceOutSeconds"],
            "Source end must follow source start",
          );
        if (track.type === "video" || track.type === "audio") {
          const asset = assets.get(clip.assetId);
          if (
            !asset ||
            (track.type === "video"
              ? !["video", "image"].includes(asset.kind)
              : asset.kind !== "audio" && asset.kind !== "video")
          )
            issue([...path, "assetId"], "Asset does not match the track");
        }
        if (track.type === "video") visuals.push({ clip, track, path });
        if (
          track.type === "text" &&
          !CAPABILITIES.templates.includes(clip.templateId)
        )
          issue([...path, "templateId"], "Choose an installed text template");
        if (
          track.type === "captions" &&
          !CAPABILITIES.captions.includes(clip.styleId)
        )
          issue([...path, "styleId"], "Choose an installed caption style");
        if (
          track.type === "captions" &&
          clip.styleId === "karaoke_v1" &&
          !clip.words.length
        )
          issue([...path, "words"], "Karaoke needs aligned words");
        let previous = clip.startFrame;
        clip.words.forEach((item, wi) => {
          if (
            item.endFrame <= item.startFrame ||
            item.startFrame < previous ||
            item.endFrame > clip.startFrame + clip.durationInFrames
          )
            issue([...path, "words", wi], "Word times must be inside the clip");
          previous = item.endFrame;
        });
      });
      if (
        track.duckUnder &&
        (track.type !== "audio" ||
          track.role !== "music" ||
          !timeline.tracks.some(
            (other) =>
              other.id === track.duckUnder &&
              other.type === "audio" &&
              other.role === "narration",
          ))
      )
        issue(
          ["tracks", ti, "duckUnder"],
          "Ducking target must be an audio track",
        );
    });
    const transitionPairs = new Set();
    timeline.transitions.forEach((transition, index) => {
      const path = ["transitions", index];
      const pair = transition.fromClipId + ":" + transition.toClipId;
      if (transitionPairs.has(pair))
        issue(path, "Only one transition is allowed between a pair of clips");
      transitionPairs.add(pair);
      register(transition.id, [...path, "id"]);
      const from = clips.get(transition.fromClipId),
        to = clips.get(transition.toClipId);
      if (
        !from ||
        !to ||
        from.track.type !== "video" ||
        to.track.type !== "video" ||
        from.track.id !== to.track.id
      ) {
        issue(path, "Transition needs two visual clips on the same track");
        return;
      }
      const end = transition.startFrame + transition.durationInFrames;
      if (
        transition.fromClipId === transition.toClipId ||
        transition.startFrame !== to.clip.startFrame ||
        end !== from.clip.startFrame + from.clip.durationInFrames ||
        transition.durationInFrames >=
          Math.min(from.clip.durationInFrames, to.clip.durationInFrames) ||
        from.clip.startFrame >= to.clip.startFrame
      )
        issue(path, "Transition must exactly match the overlap of its clips");
    });
    if (clips.size > 2000) issue(["tracks"], "Timeline exceeds 2000 clips");
    const sorted = visuals
      .filter((item) => item.track.layer === 0)
      .sort((a, b) => a.clip.startFrame - b.clip.startFrame);
    let covered = 0;
    for (const { clip, path } of sorted) {
      if (clip.startFrame > covered)
        issue(path, "Timeline has an uncovered visual gap");
      covered = Math.max(covered, clip.startFrame + clip.durationInFrames);
    }
    if (covered < timeline.format.durationInFrames)
      issue(["tracks"], "Visuals must cover the whole timeline");
    visuals.forEach((left, index) =>
      visuals.slice(index + 1).forEach((right) => {
        if (left.track.id !== right.track.id) return;
        if (
          Math.max(left.clip.startFrame, right.clip.startFrame) <
            Math.min(
              left.clip.startFrame + left.clip.durationInFrames,
              right.clip.startFrame + right.clip.durationInFrames,
            ) &&
          !timeline.transitions.some(
            (t) =>
              [t.fromClipId, t.toClipId].includes(left.clip.id) &&
              [t.fromClipId, t.toClipId].includes(right.clip.id),
          )
        )
          issue(right.path, "Overlapping clips on a track need a transition");
      }),
    );
  });

export const loopbackUrl = z
  .string()
  .url()
  .refine((value) => {
    const url = new URL(value);
    return (
      url.protocol === "http:" &&
      ["127.0.0.1", "[::1]"].includes(url.hostname) &&
      !url.username &&
      !url.password
    );
  }, "Use an HTTP loopback asset URL");
const absolutePath = z
  .string()
  .min(1)
  .max(1024)
  .refine(
    (value) => /^(?:[a-z]:[\\/]|\/)/i.test(value),
    "Use an absolute path",
  );
export const jobSchema = z
  .strictObject({
    timeline: timelineSchema,
    media: z.record(z.string(), loopbackUrl),
    fontUrl: loopbackUrl,
    output: absolutePath,
    height: z.union([z.literal(320), z.literal(960), z.literal(1920)]),
    bundleDir: absolutePath,
  })
  .superRefine((job, ctx) => {
    const origin = new URL(job.fontUrl).origin;
    for (const [key, value] of Object.entries(job.media))
      if (new URL(value).origin !== origin)
        ctx.addIssue({
          code: "custom",
          path: ["media", key],
          message: "All assets must use the same private server origin",
        });
    for (const track of job.timeline.tracks.filter((t) => t.type === "video"))
      for (const clip of track.clips)
        if (!job.media[clip.id] && !job.media[clip.assetId])
          ctx.addIssue({
            code: "custom",
            path: ["media", clip.id],
            message: "Missing prepared visual media",
          });
  });

export const isAllowedRequest = (value, origins) => {
  try {
    const url = new URL(value);
    if (url.username || url.password) return false;
    return (
      ["data:", "blob:", "about:"].includes(url.protocol) ||
      (url.protocol === "http:" && origins.has(url.origin))
    );
  } catch {
    return false;
  }
};
