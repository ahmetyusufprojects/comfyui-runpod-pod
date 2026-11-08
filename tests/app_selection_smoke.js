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
console.log('RunPod ignores retired Anima preferences; Windows behavior is preserved');
