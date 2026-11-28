import { Composition, Folder } from "remotion";
import { HelloWorld } from "./HelloWorld";
import { Logo } from "./HelloWorld/Logo";
import { Title } from "./HelloWorld/Title";
import { PeakCutVertical } from "./peakcut/PeakCutVertical";

// Each <Composition> is an entry in the sidebar!

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="PeakCutVertical"
        component={PeakCutVertical}
        fps={30}
        width={1080}
        height={1920}
        durationInFrames={30}
        defaultProps={{
          timeline: {
            schemaVersion: "2.0",
            format: {
              width: 1080,
              height: 1920,
              fps: 30,
              durationInFrames: 30,
              safeAreaProfile: "shorts-default-v1",
            },
            assets: [],
            tracks: [],
            transitions: [],
          },
          media: {},
          fontUrl: "",
          height: 1920,
        }}
        calculateMetadata={({ props }) => ({
          durationInFrames: props.timeline.format.durationInFrames,
          fps: 30,
          width: (props.height * 9) / 16,
          height: props.height,
        })}
      />
      <Folder name="Elements">
        <Composition
          id="Logo"
          component={Logo}
          durationInFrames={150}
          fps={30}
          width={1920}
          height={1080}
          defaultProps={{
            logoColor1: "#91EAE4",
            logoColor2: "#86A8E7",
          }}
        />
        <Composition
          id="Title"
          component={Title}
          durationInFrames={115}
          fps={30}
          width={1920}
          height={1080}
          defaultProps={{
            titleText: "Welcome to Remotion",
            titleColor: "#000000",
          }}
        />
      </Folder>
      <Composition
        // You can take the "id" to render a video:
        // bunx remotion render HelloWorld
        id="HelloWorld"
        component={HelloWorld}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        // You can override these props for each render:
        // https://www.remotion.dev/docs/parametrized-rendering
        defaultProps={{
          titleText: "Welcome to Remotion",
          titleColor: "#000000",
        }}
      />
    </>
  );
};
