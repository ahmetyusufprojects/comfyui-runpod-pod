import { X } from "lucide-react";
import type { ReactNode } from "react";
import "./GenerationOptions.css";

export type ProductionMode = "image" | "video";

export interface GenerationModelOption {
  id: string;
  name: string;
  family?: string;
  ready?: boolean;
  generationDefaults?: Record<string, unknown>;
}

export interface GenerationLoraOption {
  id: string;
  name: string;
  ready?: boolean;
  defaultWeight?: number;
}

export interface GenerationOptionsValue {
  mode: ProductionMode;
  imageModelId: string;
  videoModelId: string;
  width: number;
  height: number;
  resolutionPreset: string;
  aspectLocked: boolean;
  scaleToMax: boolean;
  referenceMaxSide: number;
  videoSeconds: number;
  videoFps: number;
  videoMaxSide: number;
  videoProfile: "fast" | "quality" | "normal";
  steps: number;
  sampler: string;
  seed: number;
  cfg: number;
  fluxBbox: boolean;
  kreaEditStrength: number;
  promptAssist: boolean;
  promptTranslate: boolean;
  promptEdit: boolean;
  promptRepeat: boolean;
  continuous: boolean;
  llmModelId: string;
  loraWeights: Record<string, number>;
  imageEnhance: boolean;
  imageEnhanceMethod: string;
  imageEnhanceScale: number;
  videoEnhance: boolean;
  videoEnhanceMethod: string;
  videoEnhanceScale: number;
  videoFpsMultiplier: number;
}

export interface GenerationOptionsProps {
  value: GenerationOptionsValue;
  onChange: (patch: Partial<GenerationOptionsValue>) => void;
  imageModels: GenerationModelOption[];
  videoModels: GenerationModelOption[];
  llmModels: GenerationModelOption[];
  loras: GenerationLoraOption[];
  referenceCount: number;
  open: boolean;
  onClose: () => void;
}

const RESOLUTIONS = [
  ["1024x1024", "Kare · 1024 × 1024", 1024, 1024],
  ["1536x1024", "Yatay · 1536 × 1024", 1536, 1024],
  ["1024x1536", "Dikey · 1024 × 1536", 1024, 1536],
  ["1344x768", "Geniş · 1344 × 768", 1344, 768],
  ["768x1344", "Uzun · 768 × 1344", 768, 1344],
  ["custom", "Özel ölçü", 0, 0],
] as const;

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="generation-options__field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

function Toggle({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (value: boolean) => void; hint?: string }) {
  return <label className="generation-options__toggle"><input type="checkbox" checked={checked} onChange={event => onChange(event.target.checked)} /><span><b>{label}</b>{hint && <small>{hint}</small>}</span></label>;
}

export function GenerationOptions({ value, onChange, imageModels, videoModels, llmModels, loras, referenceCount, open, onClose }: GenerationOptionsProps) {
  const patch = <K extends keyof GenerationOptionsValue>(key: K, next: GenerationOptionsValue[K]) => onChange({ [key]: next } as Pick<GenerationOptionsValue, K>);
  const preset = (name: string) => {
    const found = RESOLUTIONS.find(item => item[0] === name);
    if (!found) return;
    onChange({ resolutionPreset: name, ...(found[2] ? { width: found[2], height: found[3] } : {}) });
  };
  const imageFamily = imageModels.find(model => model.id === value.imageModelId)?.family ?? "";
  const hasBbox = imageFamily.toLowerCase().includes("flux3") || imageFamily.toLowerCase().includes("flux-3");
  const hasKrea = imageFamily.toLowerCase().includes("krea");
  return <>
    <div className={`generation-options__scrim${open ? " is-open" : ""}`} onClick={onClose} aria-hidden="true" />
    <aside className={`generation-options${open ? " is-open" : ""}`} aria-label="Üretim ayarları" aria-hidden={!open}>
      <header className="generation-options__header"><div><p className="generation-options__eyebrow">ÇALIŞMA ALANI</p><h2>Üretim ayarları</h2></div><button type="button" className="generation-options__close" onClick={onClose} aria-label="Üretim ayarlarını kapat"><X size={18}/></button></header>
      <div className="generation-options__body">
        <section className="generation-options__section" aria-labelledby="go-mode-title">
          <h3 id="go-mode-title">Tür ve model</h3>
          <div className="generation-options__segmented" role="group" aria-label="Üretim türü">
            <button type="button" aria-pressed={value.mode === "image"} onClick={() => patch("mode", "image")}>Görsel</button>
            <button type="button" aria-pressed={value.mode === "video"} onClick={() => patch("mode", "video")}>Video</button>
          </div>
          {value.mode === "image" ? <Field label="Görsel modeli"><select value={value.imageModelId} onChange={event => patch("imageModelId", event.target.value)}>{imageModels.map(model => <option key={model.id} value={model.id}>{model.name}{model.ready === false ? " · indirilmedi" : ""}</option>)}</select></Field> : <>
            <Field label="Video modeli"><select value={value.videoModelId} onChange={event => patch("videoModelId", event.target.value)}>{videoModels.map(model => <option key={model.id} value={model.id}>{model.name}{model.ready === false ? " · indirilmedi" : ""}</option>)}</select></Field>
            <div className="generation-options__grid generation-options__grid--three">
              <Field label="Süre (sn)"><input type="number" min="0.25" max="30" step="0.25" value={value.videoSeconds} onChange={event => patch("videoSeconds", Number(event.target.value))}/></Field>
              <Field label="FPS"><select value={value.videoFps} onChange={event => patch("videoFps", Number(event.target.value))}>{[8, 12, 16, 24, 30].map(fps => <option key={fps}>{fps}</option>)}</select></Field>
              <Field label="Uzun kenar"><input type="number" min="320" max="2160" step="32" value={value.videoMaxSide} onChange={event => patch("videoMaxSide", Number(event.target.value))}/></Field>
            </div>
            <Field label="Video profili"><select value={value.videoProfile} onChange={event => patch("videoProfile", event.target.value as GenerationOptionsValue["videoProfile"])}><option value="fast">Hızlı taslak · 4 adım</option><option value="quality">Kalite · turbo</option><option value="normal">Standart · turbo kapalı</option></select></Field>
          </>}
        </section>

        {value.mode === "image" && <>
          <section className="generation-options__section" aria-labelledby="go-size-title">
            <h3 id="go-size-title">Çözünürlük</h3>
            {referenceCount > 0 ? <>
              <p className="generation-options__note">Boyut referans fotoğrafından alınır. En uzun kenarı belirleyebilirsin.</p>
              <Field label="En uzun kenar (maksimum)"><input type="number" min="256" max="4096" step="16" value={value.referenceMaxSide} onChange={event => patch("referenceMaxSide", Number(event.target.value))}/></Field>
              <Toggle label="Maksimum boyuta ölçekle" checked={value.scaleToMax} onChange={next => patch("scaleToMax", next)}/>
            </> : <>
              <Field label="Ölçü hazır ayarı"><select value={value.resolutionPreset} onChange={event => preset(event.target.value)}>{RESOLUTIONS.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></Field>
              <div className="generation-options__grid">
                <Field label="Genişlik"><input type="number" min="256" max="4096" step="16" value={value.width} onChange={event => { patch("width", Number(event.target.value)); patch("resolutionPreset", "custom"); }}/></Field>
                <Field label="Yükseklik"><input type="number" min="256" max="4096" step="16" value={value.height} onChange={event => { patch("height", Number(event.target.value)); patch("resolutionPreset", "custom"); }}/></Field>
              </div>
              <Toggle label="En-boy oranını kilitle" checked={value.aspectLocked} onChange={next => patch("aspectLocked", next)} hint="Bir kenarı değiştirince diğerini aynı oranda günceller."/>
            </>}
          </section>

          <section className="generation-options__section" aria-labelledby="go-sampling-title">
            <h3 id="go-sampling-title">Örnekleme</h3>
            <div className="generation-options__grid generation-options__grid--three">
              <Field label="Adım"><input type="number" min="1" max="100" value={value.steps} onChange={event => patch("steps", Number(event.target.value))}/></Field>
              <Field label="Seed"><input type="number" min="-1" value={value.seed} onChange={event => patch("seed", Number(event.target.value))}/></Field>
              <Field label="CFG"><input type="number" min="0" max="30" step="0.1" value={value.cfg} onChange={event => patch("cfg", Number(event.target.value))}/></Field>
            </div>
            <Field label="Örnekleyici"><select value={value.sampler} onChange={event => patch("sampler", event.target.value)}><option value="dpmpp_2m_sde">DPM++ 2M SDE</option><option value="euler">Euler</option><option value="euler_ancestral">Euler ancestral</option><option value="dpmpp_2m">DPM++ 2M</option><option value="uni_pc">UniPC</option><option value="res_multistep">Res multistep</option></select></Field>
          </section>
          {hasBbox && <section className="generation-options__section"><h3>Flux 3 Klein</h3><Toggle label="BBox düzenleme modu" checked={value.fluxBbox} onChange={next => patch("fluxBbox", next)} hint="Promptta kutu koordinatlarıyla bölgesel yerleşim tarif et."/></section>}
          {hasKrea && referenceCount > 0 && <section className="generation-options__section"><h3>Krea düzenleme</h3><Field label={`Düzenleme gücü · ${value.kreaEditStrength.toFixed(2)}`}><input type="range" min="0.1" max="1" step="0.05" value={value.kreaEditStrength} onChange={event => patch("kreaEditStrength", Number(event.target.value))}/></Field><p className="generation-options__note">Düşük değer kaynağı daha çok korur.</p></section>}
          {loras.length > 0 && <section className="generation-options__section"><h3>LoRA eklentileri</h3><div className="generation-options__lora-list">{loras.map(lora => {
            const checked = Object.hasOwn(value.loraWeights, lora.id);
            return <div className="generation-options__lora" key={lora.id}><label><input type="checkbox" checked={checked} onChange={event => { const next = { ...value.loraWeights }; if (event.target.checked) next[lora.id] = lora.defaultWeight ?? 1; else delete next[lora.id]; patch("loraWeights", next); }}/><span>{lora.name}{lora.ready === false && <small> · üretim öncesi indirilir</small>}</span></label>{checked && <input aria-label={`${lora.name} ağırlığı`} type="number" min="0" max="2" step="0.05" value={value.loraWeights[lora.id]} onChange={event => patch("loraWeights", { ...value.loraWeights, [lora.id]: Number(event.target.value) })}/>}</div>;
          })}</div></section>}
        </>}

        <section className="generation-options__section" aria-labelledby="go-prompt-title">
          <h3 id="go-prompt-title">Prompt desteği</h3>
          <Toggle label="Promptu iyileştir" checked={value.promptAssist} onChange={next => patch("promptAssist", next)}/>
          <Toggle label="Mevcut promptu düzenle" checked={value.promptEdit} onChange={next => patch("promptEdit", next)}/>
          {value.promptAssist && <Toggle label="Her görselde yeniden iyileştir" checked={value.promptRepeat} onChange={next => patch("promptRepeat", next)}/>}
          <Toggle label="Türkçe promptu İngilizceye çevir" checked={value.promptTranslate} onChange={next => patch("promptTranslate", next)}/>
          <Field label="Prompt için LLM"><select value={value.llmModelId} onChange={event => patch("llmModelId", event.target.value)}>{llmModels.map(model => <option key={model.id} value={model.id}>{model.name}{model.ready === false ? " · indirilmedi" : ""}</option>)}</select></Field>
          <Toggle label="Durdurana kadar üret" checked={value.continuous} onChange={next => patch("continuous", next)} hint="Her tamamlanan görselden sonra bir iş daha kuyruğa ekler."/>
        </section>

        <section className="generation-options__section" aria-labelledby="go-enhance-title">
          <h3 id="go-enhance-title">Sonuç iyileştirme</h3>
          {value.mode === "image" ? <>
            <Toggle label="Üretimden sonra görseli iyileştir" checked={value.imageEnhance} onChange={next => patch("imageEnhance", next)} hint="Bağımsız görsel yükseltici kullanılır."/>
            {value.imageEnhance && <div className="generation-options__grid"><Field label="Yöntem"><select value={value.imageEnhanceMethod} onChange={event => patch("imageEnhanceMethod", event.target.value)}><option value="skin">Ten ayrıntısı · Skin Contrast</option><option value="photo">Doğal fotoğraf · ClearReality</option><option value="realesrgan">Genel kullanım · Real-ESRGAN</option></select></Field><Field label="Büyütme katsayısı"><select value={value.imageEnhanceScale} onChange={event => patch("imageEnhanceScale", Number(event.target.value))}>{[1, 1.5, 2, 3, 4].map(scale => <option key={scale} value={scale}>{scale}×</option>)}</select></Field></div>}
          </> : <>
            <Toggle label="Üretimden sonra videoyu iyileştir" checked={value.videoEnhance} onChange={next => patch("videoEnhance", next)} hint="Video karesi ve FPS için ayrı işlem uygulanır."/>
            {value.videoEnhance && <div className="generation-options__grid"><Field label="SeedVR2 tipi"><select value={value.videoEnhanceMethod} onChange={event => patch("videoEnhanceMethod", event.target.value)}><option value="seedvr2_fp8">SeedVR2 3B FP8 · düşük VRAM</option><option value="seedvr2">SeedVR2 3B FP16 · kalite</option></select></Field><Field label="Çözünürlük katsayısı"><select value={value.videoEnhanceScale} onChange={event => patch("videoEnhanceScale", Number(event.target.value))}>{[1, 1.5, 2, 3, 4].map(scale => <option key={scale} value={scale}>{scale}×</option>)}</select></Field><Field label="FPS katsayısı"><select value={value.videoFpsMultiplier} onChange={event => patch("videoFpsMultiplier", Number(event.target.value))}>{[1, 2, 3, 4].map(scale => <option key={scale} value={scale}>{scale}×</option>)}</select></Field></div>}
          </>}
        </section>
      </div>
    </aside>
  </>;
}
