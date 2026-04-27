import path from 'node:path';
import process from 'node:process';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';

import {bundle} from '@remotion/bundler';
import {getCompositions, renderMedia} from '@remotion/renderer';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, '..');

const parseArgs = () => {
  const args = process.argv.slice(2);
  const parsed = {composition: 'ManimHybridLesson', props: '', out: path.join(projectRoot, 'out', 'hybrid.mp4')};
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === '--composition') parsed.composition = args[index + 1];
    if (arg === '--props') parsed.props = args[index + 1];
    if (arg === '--out') parsed.out = args[index + 1];
  }
  return parsed;
};

const main = async () => {
  const options = parseArgs();
  const propsPath = options.props || path.join(projectRoot, 'src', 'sample-props.json');
  console.log('[render] Loading props from', propsPath);
  const inputProps = JSON.parse(await fs.readFile(propsPath, 'utf8'));

  const totalFrames = (inputProps.segments || []).reduce(
    (s, seg) => s + Number(seg?.durationInFrames || 0), 0
  );
  console.log(`[render] ${inputProps.segments?.length} segment(s), ${totalFrames} total frames`);

  const entryPoint = path.join(projectRoot, 'src', 'index.jsx');
  console.log('[render] Bundling …');
  const bundled = await bundle({entryPoint});
  console.log('[render] Bundle done:', bundled);

  console.log('[render] Getting compositions …');
  const compositions = await getCompositions(bundled, {inputProps});
  const composition = compositions.find((item) => item.id === options.composition);
  if (!composition) {
    throw new Error(`Composition not found: ${options.composition}`);
  }
  console.log(`[render] Composition "${composition.id}" — ${composition.durationInFrames} frames @ ${composition.fps} fps`);

  await fs.mkdir(path.dirname(options.out), {recursive: true});
  console.log('[render] Rendering to', options.out);
  await renderMedia({
    composition,
    serveUrl: bundled,
    codec: 'h264',
    outputLocation: options.out,
    inputProps,
    onProgress: ({progress}) => {
      if (Math.round(progress * 100) % 10 === 0) {
        process.stdout.write(`\r[render] Progress: ${Math.round(progress * 100)}%`);
      }
    },
  });
  console.log(`\n[render] Done! Rendered ${options.out}`);
};

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
