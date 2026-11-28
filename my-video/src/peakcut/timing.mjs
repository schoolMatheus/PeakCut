const clamp = (value) => Math.max(0, Math.min(1, value));

// Timings are absolute. An overlap is never subtracted from a clip twice.
export const transitionStyle = (clipId, frame, transitions) => {
  let opacity = 1,
    translateX = 0;
  for (const transition of transitions) {
    if (transition.fromClipId !== clipId && transition.toClipId !== clipId)
      continue;
    const progress = clamp(
      (frame - transition.startFrame) / transition.durationInFrames,
    );
    if (transition.type === "crossfade")
      opacity *=
        transition.toClipId === clipId ? progress : progress < 1 ? 1 : 0;
    else
      translateX +=
        (transition.toClipId === clipId ? 1 - progress : -progress) * 100;
  }
  return { opacity, translateX };
};
export const motionStyle = (motion, frame, duration) => {
  const progress = clamp(frame / Math.max(1, duration - 1));
  if (motion.id === "none") return { scale: 1, translateX: 0 };
  const start = motion.id === "zoom_out" ? motion.scaleTo : motion.scaleFrom;
  const end = motion.id === "zoom_out" ? motion.scaleFrom : motion.scaleTo;
  const pan =
    motion.id === "pan_left"
      ? -1
      : motion.id === "pan_right"
        ? 1
        : motion.id === "ken_burns_v1"
          ? 0.5
          : 0;
  const translateX = pan * (progress - 0.5) * 8;
  return {
    scale: Math.max(
      start + (end - start) * progress,
      1 + (2 * Math.abs(translateX)) / 100,
    ),
    translateX,
  };
};
export const activeWords = (words, frame) =>
  words.map((word) => frame >= word.startFrame && frame < word.endFrame);
