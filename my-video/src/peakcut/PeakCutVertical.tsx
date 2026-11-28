import React, { useEffect, useState } from "react";
import {
  AbsoluteFill,
  Sequence,
  Img,
  Html5Video,
  useCurrentFrame,
  useVideoConfig,
  delayRender,
  continueRender,
  cancelRender,
} from "remotion";
import { z } from "zod";
import { timelineSchema } from "./schema.mjs";
import { activeWords, motionStyle, transitionStyle } from "./timing.mjs";

export type Timeline = z.infer<typeof timelineSchema>;
type Clip = Timeline["tracks"][number]["clips"][number];
export type PeakCutProps = {
  timeline: Timeline;
  media: Record<string, string>;
  fontUrl: string;
  height: number;
};

const Font: React.FC<{ url: string }> = ({ url }) => {
  const [handle] = useState(() =>
    delayRender("Loading the local caption font"),
  );
  useEffect(() => {
    if (!url) {
      continueRender(handle);
      return;
    }
    const face = new FontFace(
      "PeakCut Poppins",
      `url(${JSON.stringify(url)})`,
      { weight: "800" },
    );
    face
      .load()
      .then((font) => {
        document.fonts.add(font);
        continueRender(handle);
      })
      .catch(cancelRender);
  }, [url, handle]);
  return null;
};

const Visual: React.FC<{ clip: Clip; src: string }> = ({ clip, src }) => {
  const frame = useCurrentFrame();
  const motion = motionStyle(clip.motion, frame, clip.durationInFrames);
  const style: React.CSSProperties = {
    width: "100%",
    height: "100%",
    objectFit: clip.fit,
    objectPosition: `${clip.focus.x * 100}% ${clip.focus.y * 100}%`,
    scale: motion.scale,
    translate: `${motion.translateX}% 0`,
    transformOrigin: "center",
  };
  return (
    <AbsoluteFill style={{ overflow: "hidden" }}>
      {src && <Img src={src} style={style} />}
    </AbsoluteFill>
  );
};

const Text: React.FC<{ clip: Clip; caption: boolean }> = ({
  clip,
  caption,
}) => {
  const frame = useCurrentFrame();
  const { width } = useVideoConfig();
  const scale = width / 1080;
  const lower = clip.templateId === "lower_third_v1";
  const cta = clip.templateId === "cta_v1";
  const comparison = clip.templateId === "comparison_v1";
  const pop =
    !caption && clip.templateId === "title_pop_v1" ? Math.min(1, frame / 7) : 1;
  const highlighted = activeWords(clip.words, frame + clip.startFrame);
  return (
    <div
      style={{
        position: "absolute",
        left: `${clip.position.x * 100}%`,
        top: `${clip.position.y * 100}%`,
        translate: "-50% -50%",
        scale: pop,
        width: "82%",
        maxHeight: "70%",
        overflow: "hidden",
        fontSize: (caption ? 57 : lower ? 49 : cta ? 62 : 76) * scale,
        lineHeight: 1.18,
        fontWeight: 800,
        textAlign: lower ? "left" : "center",
        color: "#ffffff",
        whiteSpace: "pre-wrap",
        overflowWrap: "anywhere",
        padding: `${(caption ? 18 : 30) * scale}px`,
        borderRadius: cta ? 22 * scale : 0,
        background: cta
          ? "#dfff64"
          : caption
            ? "rgba(0,0,0,.78)"
            : lower
              ? "rgba(0,0,0,.86)"
              : "transparent",
        ...(cta ? { color: "#151515" } : {}),
        ...(comparison
          ? {
              borderTop: `${5 * scale}px solid #dfff64`,
              borderBottom: `${5 * scale}px solid #dfff64`,
            }
          : {}),
      }}
    >
      {caption && clip.styleId === "karaoke_v1"
        ? clip.words.map((word, index) => (
            <React.Fragment key={index}>
              <span
                style={{ color: highlighted[index] ? "#dfff64" : "#ffffff" }}
              >
                {word.text}
              </span>
              {index < clip.words.length - 1 ? " " : ""}
            </React.Fragment>
          ))
        : clip.text || clip.words.map((word) => word.text).join(" ")}
    </div>
  );
};

export const PeakCutVertical: React.FC<PeakCutProps> = ({
  timeline,
  media,
  fontUrl,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const tracks = [...timeline.tracks]
    .filter((track) => track.type !== "audio")
    .sort((a, b) => a.layer - b.layer);
  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0e0e0e",
        fontFamily: "PeakCut Poppins, sans-serif",
        overflow: "hidden",
      }}
    >
      <Font url={fontUrl} />
      {tracks.map((track) => (
        <React.Fragment key={track.id}>
          {[...track.clips]
            .sort((a, b) => a.startFrame - b.startFrame)
            .map((clip) => {
              const transition = transitionStyle(
                clip.id,
                frame,
                timeline.transitions,
              );
              const local = frame - clip.startFrame;
              const fade = clip.fadeVisualFrames
                ? Math.max(
                    0,
                    Math.min(
                      1,
                      local / clip.fadeVisualFrames,
                      (clip.durationInFrames - local) / clip.fadeVisualFrames,
                    ),
                  )
                : 1;
              const src = media[clip.id] || media[clip.assetId];
              const asset = timeline.assets.find(
                (item) => item.id === clip.assetId,
              );
              return (
                <Sequence
                  key={clip.id}
                  from={clip.startFrame}
                  durationInFrames={clip.durationInFrames}
                  premountFor={fps}
                  name={clip.id}
                >
                  <AbsoluteFill
                    style={{
                      opacity: clip.opacity * transition.opacity * fade,
                      translate: `${transition.translateX}% 0`,
                      overflow: "hidden",
                    }}
                  >
                    {track.type === "video" &&
                      (asset?.kind === "video" ? (
                        <VideoClip clip={clip} src={src} />
                      ) : (
                        <Visual clip={clip} src={src} />
                      ))}
                    {(track.type === "text" || track.type === "captions") && (
                      <Text clip={clip} caption={track.type === "captions"} />
                    )}
                  </AbsoluteFill>
                </Sequence>
              );
            })}
        </React.Fragment>
      ))}
    </AbsoluteFill>
  );
};

const VideoClip: React.FC<{ clip: Clip; src: string }> = ({ clip, src }) => {
  const frame = useCurrentFrame();
  const motion = motionStyle(clip.motion, frame, clip.durationInFrames);
  return (
    <Html5Video
      src={src}
      muted
      volume={0}
      pauseWhenBuffering
      style={{
        width: "100%",
        height: "100%",
        objectFit: clip.fit,
        objectPosition: `${clip.focus.x * 100}% ${clip.focus.y * 100}%`,
        scale: motion.scale,
        translate: `${motion.translateX}% 0`,
        transformOrigin: "center",
      }}
    />
  );
};
