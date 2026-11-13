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
assert.match(imageSelector[1], /<option[^>]*value="flux3"[^>]*>Flux 3 Klein/,
  'enabled Flux 3 is offered in the production selector');
const videoSelector = mainHtml.match(/<select id="videoModel">([\s\S]*?)<\/select>/);
assert.ok(videoSelector, 'RunPod video model selector is present');
assert.match(videoSelector[1], /value="wan22"[^>]*>Wan 2\.2/,
  'large Wan 2.2 video model is selectable');
assert.match(source, /runpodModelWrap\.hidden = !RUNPOD_WEBUI \|\| videoMu\(\)/,
  'video mode hides the image-model selector');
assert.match(source, /video_model_instance: vm \? runpodVideoModelSecili\(\) : null/,
  'selected video model is sent with generation requests');
assert.doesNotMatch(imageSelector[1], /<option[^>]*value="flux3"[^>]*disabled/,
  'Flux 3 is not disabled in the production selector');
assert.match(source, /illustrious:\s*\{steps:\s*28,\s*sampler:\s*'euler_ancestral',\s*cfg:\s*5\}/,
  'Illustrious uses the checkpoint-specific 28-step, CFG 5 profile');
assert.match(source, /\/api\/arsiv\/sil-coklu/,
  'Archive selection uses the protected bulk-delete endpoint');
assert.match(source, /classList\.toggle\('arsiv-secim-modu',arsivSecimAktif\)/,
  'archive selection state enables the stylesheet selector for delete buttons');
assert.match(source, /select\.title='Arşiv görünümünden kaldır; kaynak kayıtları korunur'/,
  'all archive entries can be selected for removal without disabling the control');
assert.doesNotMatch(source, /select\.disabled=!k\.silinebilir/,
  'archive selection control is not disabled for library/history/chat photos');
assert.match(mainHtml, /id="arsivSecimBar"/,
  'Archive exposes its multi-select controls');
assert.match(mainHtml, /id="uretimPromptDuzenle"/,
  'production settings expose prompt edit mode');
const setupHtml = fs.readFileSync('webui_flux2/setup.html', 'utf8');
assert.match(setupHtml, /Tüm sistemi şifreleyip indir/,
  'installer exposes encrypted whole-system backup export');
assert.match(setupHtml, /tüm kullanıcı hesaplarını, şifreli kasaları[\s\S]*genel kurulum\/model ayarlarını/,
  'whole-system backup scope includes all accounts and global settings');
assert.match(setupHtml, /Tüm sistemi doğrula ve geri yükle/,
  'installer restores a whole-system backup');
assert.match(mainHtml, /id="psDefaultsSave"/,
  'Prompt Studio exposes editable quick-generation defaults');
assert.match(source, /psDuzenlemeBaglam/,
  'prompt edit context is carried forward between Studio edits');
assert.ok(source.includes("$('prompt').value = s.positive || ''"),
  'prompt editing updates the active production prompt');
assert.match(source, /uretim_prompt_duzenle/,
  'prompt edit mode is persisted with user generation settings');
const styles = fs.readFileSync('webui_flux2/static/app.css', 'utf8');
assert.match(styles, /#arsivIzgara\{display:grid!important;grid-template-columns:/,
  'archive cards keep a responsive grid instead of overlapping');
console.log('RunPod model selection defers Flux 3 and ignores retired Anima preferences');
