import React from 'react';
import {Composition} from 'remotion';

import {HybridLesson} from './HybridLesson';
import sampleProps from './sample-props.json';

const getDurationInFrames = (props) => {
  const segments = Array.isArray(props?.segments) ? props.segments : [];
  const total = segments.reduce((sum, segment) => sum + Number(segment?.durationInFrames || 0), 0);
  return Math.max(1, total || 300);
};

export const RemotionRoot = () => {
  return (
    <Composition
      id="ManimHybridLesson"
      component={HybridLesson}
      defaultProps={sampleProps}
      durationInFrames={getDurationInFrames(sampleProps)}
      fps={sampleProps.fps || 30}
      width={sampleProps.width || 1920}
      height={sampleProps.height || 1080}
      calculateMetadata={({props}) => ({
        durationInFrames: getDurationInFrames(props),
        fps: props?.fps || 30,
        width: props?.width || 1920,
        height: props?.height || 1080,
      })}
    />
  );
};
