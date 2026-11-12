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

assert.match(source, /option\.value === f\.image_model && !option\.disabled/,
  'saved image model selection must ignore unavailable options');
assert.match(source, /function runpodModelListesiGuncelle\(data\)/,
  'RunPod image model variants are populated from the server catalog');
assert.match(source, /function runpodLlmListesiGuncelle\(data, preferred\)/,
  'RunPod LLM variants are populated from installed model profiles');
assert.match(source, /flux3:\s*\{steps:\s*16,\s*sampler:\s*'euler',\s*cfg:\s*1\}/,
  'Flux 3 remains on its 16-step distilled profile');
const mainHtml = fs.readFileSync('webui_flux2/index.html', 'utf8');
const imageSelector = mainHtml.match(/<select id="runpodImageModel">([\s\S]*?)<\/select>/);
assert.ok(imageSelector, 'RunPod image model selector is present');
assert.doesNotMatch(imageSelector[1], /<option[^>]*value="flux3"/,
  'disabled Flux 3 is not offered in the production selector');
assert.match(source, /illustrious:\s*\{steps:\s*28,\s*sampler:\s*'euler_ancestral',\s*cfg:\s*5\}/,
  'Illustrious uses the checkpoint-specific 28-step, CFG 5 profile');
assert.match(source, /\/api\/arsiv\/sil-coklu/,
  'Archive selection uses the protected bulk-delete endpoint');
assert.match(source, /k\.silinebilir\s*\?\s*'<div class="satir"><div><button id="arsivSil"/,
  'Archive delete action is only shown for imported copies');
assert.match(mainHtml, /id="arsivSecimBar"/,
  'Archive exposes its multi-select controls');
console.log('RunPod model selection defers Flux 3 and ignores retired Anima preferences');
