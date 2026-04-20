import { Composition } from "remotion";
import { SubtitleVideo, defaultProps } from "./SubtitleVideo";

export const RemotionRoot = () => (
  <Composition
    id="SubtitleVideo"
    component={SubtitleVideo}
    durationInFrames={defaultProps.durationInFrames}
    fps={defaultProps.fps}
    width={defaultProps.width}
    height={defaultProps.height}
    defaultProps={defaultProps}
    calculateMetadata={({ props }) => ({
      durationInFrames: props.durationInFrames,
      fps:              props.fps,
      width:            props.width,
      height:           props.height,
    })}
  />
);
