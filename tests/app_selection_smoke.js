'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('webui_flux2/static/app.js', 'utf8');
const match = source.match(/function animaSecili\(\)\s*\{([^}]*)\}/);
assert.ok(match, 'Anima preference helper is present');

function animaSelected({runpod, checked, video = false, references = 0}) {
  const context = {
    RUNPOD_WEBUI: runpod,
    videoMu: () => video,
    refSayisi: () => references,
    $: () => ({checked}),
  };
  return vm.runInNewContext(`(function(){${match[1]}\n})`, context)();
}

assert.equal(animaSelected({runpod: true, checked: true}), false,
  'old saved Anima preferences must not override the RunPod model choice');
assert.equal(animaSelected({runpod: false, checked: true}), true,
  'Windows Anima preferences remain available');
assert.equal(animaSelected({runpod: false, checked: true, video: true}), false);
assert.equal(animaSelected({runpod: false, checked: true, references: 1}), false);

assert.doesNotMatch(source,
  /value\s*===\s*['"]flux3['"]\)\s*modelSelector\.value\s*=\s*['"]auto['"]/,
  'RunPod must preserve an explicit Flux 3 model choice');
assert.match(source, /flux3:\s*\{steps:\s*16,\s*sampler:\s*'euler'\}/,
  'Flux 3 remains on its 16-step distilled profile');
const mainHtml = fs.readFileSync('webui_flux2/index.html', 'utf8');
const flux3Option = mainHtml.match(/<option value="flux3"[^>]*>[^<]*<\/option>/);
assert.ok(flux3Option && !/\bdisabled\b/.test(flux3Option[0]),
  'Flux 3 is available in RunPod image settings');
console.log('RunPod model selection keeps Flux 3 and ignores retired Anima preferences');
