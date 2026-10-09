import React from 'react';
import {Composition, continueRender, delayRender, staticFile} from 'remotion';
import {Batch, totalFrames} from './Batch';
import type {BProps} from './Batch';

const fontHandle = delayRender('fonts');
Promise.all(
  [
    new FontFace('Montserrat', `url(${staticFile('fonts/Montserrat-Bold.otf')})`, {weight: '700'}),
    new FontFace('Montserrat', `url(${staticFile('fonts/Montserrat-normal-900.ttf')})`, {weight: '900'}),
    new FontFace('Bebas', `url(${staticFile('fonts/BebasNeue-normal-400.ttf')})`),
    new FontFace('Poppins', `url(${staticFile('fonts/Poppins-normal-600.ttf')})`, {weight: '600'}),
    new FontFace('Playfair', `url(${staticFile('fonts/PlayfairDisplay-italic-700.ttf')})`, {weight: '700', style: 'italic'}),
  ].map((ff) => ff.load().then((l) => document.fonts.add(l))),
).then(() => continueRender(fontHandle));

const EMPTY: BProps = {file: '', dur: 5, base: {s: 1, tx: 0, ty: 0, ox: 540, oy: 1150}, cam: [[0, 1, 1]], sweeps: [], chunks: [], events: [], outro: null, sfx: []};
export const Root: React.FC = () => (
  <Composition id="Batch" component={Batch as unknown as React.FC<Record<string, unknown>>} width={1080} height={1920} fps={30} durationInFrames={150} defaultProps={EMPTY as unknown as Record<string, unknown>} calculateMetadata={({props}) => ({durationInFrames: totalFrames(props as unknown as BProps)})} />
);
