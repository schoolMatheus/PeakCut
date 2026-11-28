# Remotion video

## PeakCut renderer

`PeakCutVertical` renders the visual part of PeakCut timelines. The example
compositions remain available in the Studio. FFmpeg prepares the source files
before rendering and adds the finished audio afterward.

Install from this folder once:

```console
npm ci
npm exec remotion browser ensure
```

The backend calls `node my-video/worker.mjs JOB.json`. The job is a strict JSON
object with `timeline`, `media`, `fontUrl`, `output`, `height`, and `bundleDir`.
The timeline uses schema `2.0`; `src/peakcut/schema.mjs` validates the same
fields and limits as `studio/creation_timeline.py`. `media` maps clip IDs to
prepared media URLs, with asset IDs accepted as a fallback. Prepared videos
start at zero. All media and the packaged Poppins font must use the same
HTTP loopback origin. `output` and `bundleDir` are absolute private paths.
Heights are 960 or 1920; 320 is available for a quick technical check.

The worker prints one JSON object per line: `progress` with a value from zero
to one, `done` with the output path, or `error` with a code and message.
Validation errors also include JSON pointers in `issues`. Output is H.264
at 30 fps without audio. The worker refuses software downloads while rendering
and blocks browser requests outside its bundle and prepared media origins.

Run `npm test`, `npm run lint`, and `npm run build` to check this project.
The worker passes bundler and renderer options explicitly; CLI configuration
in `remotion.config.ts` does not configure the Node API.

<p align="center">
  <a href="https://github.com/remotion-dev/logo">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://github.com/remotion-dev/logo/raw/main/animated-logo-banner-dark.apng">
      <img alt="Animated Remotion Logo" src="https://github.com/remotion-dev/logo/raw/main/animated-logo-banner-light.gif">
    </picture>
  </a>
</p>

Welcome to your Remotion project!

## Commands

**Install Dependencies**

```console
npm i --loglevel=error
```

**Start Preview**

```console
npm run dev
```

**Render video**

```console
npx remotion render
```

**Upgrade Remotion**

```console
npx remotion upgrade
```

## Docs

Get started with Remotion by reading the [fundamentals page](https://www.remotion.dev/docs/the-fundamentals).

## Help

We provide help on our [Discord server](https://discord.gg/6VzzNDwUwV).

## Issues

Found an issue with Remotion? [File an issue here](https://github.com/remotion-dev/remotion/issues/new).

## License

Note that for some entities a company license is needed. [Read the terms here](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md).
