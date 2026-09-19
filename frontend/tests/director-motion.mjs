import assert from 'node:assert/strict';
import { cameraPath, sampleCamera, patchCamera, insertionProgress, sceneTemplate } from '../src/lib/director.ts';

const a = { position: [0, 2, 8], target: [0, 1, 0], fov: 40 };
const b = { position: [8, 2, 8], target: [0, 1, 0], fov: 60 };
const old = { camera: a, end_camera: b, objects: [], duration: 8, lighting: {position:[4,8,4],intensity:3,ambient:.8,color:'#fff0d6'}, background:'#252b32', ground:'#737d7e',aspect:'16:9' };
assert.equal(cameraPath(old).length, 2);
assert.deepEqual(sampleCamera(old, 0), a);
assert.deepEqual(sampleCamera(old, 1), b);
assert.deepEqual(sampleCamera(old, .5), {position:[4,2,8],target:[0,1,0],fov:50});
assert.equal(sampleCamera(old, .25).position[0], 1.25); // Original two-camera smoothstep.
const middle = { id:'middle', at:.5, camera:{ position:[4,4,6], target:[0,1,0], fov:50 }, hold:.125, ease:'linear' };
const path = { ...old, camera_hold:.125, camera_ease:'linear',camera_keyframes:[middle] };
assert.deepEqual(sampleCamera(path, .1), a);
assert.deepEqual(sampleCamera(path, .5), middle.camera);
assert.deepEqual(sampleCamera(path, .6), middle.camera);
assert.deepEqual(sampleCamera(path, .3125), {position:[2,3,7],target:[0,1,0],fov:45});
assert.deepEqual(sampleCamera(path, .8125), {position:[6,3,7],target:[0,1,0],fov:55});
assert.deepEqual(sampleCamera({...path,duration:4}, .8125), sampleCamera(path, .8125));
const updated = patchCamera(path, 'middle', {...middle.camera, fov:55});
assert.equal(updated.camera_keyframes[0].camera.fov, 55);
assert.equal(path.camera_keyframes[0].camera.fov, 50);
assert.deepEqual(updated.camera, a);
assert.equal(insertionProgress(path,.3), .3);
assert.ok(insertionProgress(path,.05) > .125); // Never insert into a held pose.
assert.equal(insertionProgress({...path,camera_keyframes:Array.from({length:10},()=>middle)},.3),null);
const reset=sceneTemplate(path,'dialogue');
assert.equal(reset.camera_keyframes.length,0);
assert.equal(reset.camera_hold,0);
assert.equal(reset.camera_ease,'smooth');
console.log('Director motion: legacy interpolation, exact keyframes, holds, linear interpolation, duration scaling, independent edits, insertion guards and template reset passed');
