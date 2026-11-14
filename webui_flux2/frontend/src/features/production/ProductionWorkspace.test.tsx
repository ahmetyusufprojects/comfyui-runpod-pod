import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProductionWorkspace } from "./ProductionWorkspace";
import type { ProductionApi } from "../../lib/api/client";

afterEach(()=>cleanup());
function fakeApi(overrides: Partial<ProductionApi> = {}): ProductionApi {
  return {
    setup: vi.fn().mockResolvedValue({ kurulu:true, runpod:true }),
    me: vi.fn().mockResolvedValue({ kullanici:"synthetic", rol:"user" }),
    vault: vi.fn().mockResolvedValue({ acik:true }),
    status: vi.fn().mockResolvedValue({ model_instances:[
      { id:"image-one", family:"z_image", name:"Z Image · hazır", engine:"comfy", ready:true },
      { id:"image-wait", family:"krea2", name:"Krea · kuruluyor", engine:"comfy", ready:false },
      { id:"wan-one", family:"wan22_video", name:"Wan video · hazır", engine:"comfy", ready:true },
    ] }),
    settings: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({ is_id:"synthetic-job" }),
    get: vi.fn().mockResolvedValue({ durum:"calisiyor", ilerleme:{ yuzde:10 } }),
    ...overrides,
  };
}
async function ready(api: ProductionApi) {
  render(<ProductionWorkspace api={api}/>);
  await screen.findByRole("heading",{name:"Üretim alanı"});
}

describe("React üretim alanı",()=>{
  it("ilk çalıştırmada ilk hesap formunu sunar",async()=>{
    const api=fakeApi({setup:vi.fn().mockResolvedValue({kurulu:false})});
    render(<ProductionWorkspace api={api}/>);
    expect(await screen.findByRole("heading",{name:"İlk hesabını oluştur"})).toBeInTheDocument();
    expect(screen.getByLabelText("Kullanıcı adı")).toBeRequired();
  });

  it("giriş ve kasa kilidi durumlarını ayırır",async()=>{
    const loginApi=fakeApi({me:vi.fn().mockRejectedValue(new Error("Sunucu HTTP 401 yanıtı verdi."))});
    const {unmount}=render(<ProductionWorkspace api={loginApi}/>);
    expect(await screen.findByRole("heading",{name:"Tekrar hoş geldin"})).toBeInTheDocument();
    unmount();
    const vaultApi=fakeApi({vault:vi.fn().mockResolvedValue({acik:false,kasa_var:true})});
    render(<ProductionWorkspace api={vaultApi}/>);
    expect(await screen.findByRole("heading",{name:"Kasayı aç"})).toBeInTheDocument();
    expect(screen.getByLabelText("Kasa parolası")).toBeInTheDocument();
  });

  it("görsel/video seçimini ve sunucu hazır durumunu yansıtır",async()=>{
    await ready(fakeApi());
    expect(screen.getByRole("tab",{name:/Görsel/})).toHaveAttribute("aria-selected","true");
    fireEvent.click(screen.getByRole("tab",{name:/Video/}));
    expect(screen.getByRole("tab",{name:/Video/})).toHaveAttribute("aria-selected","true");
    fireEvent.click(screen.getByRole("button",{name:"Ayarlar"}));
    expect(screen.getByRole("option",{name:"Wan video · hazır"})).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:/Görsel/}));
    const pendingModel = screen.getByLabelText("Görsel modeli").querySelector('option[value="image-wait"]');
    expect(pendingModel).toBeInTheDocument();
    expect(pendingModel).toHaveTextContent("indirilmedi");
  });

  it("eski istek alanlarıyla kuyruğa gönderir ve durdurma çağrısı yapar",async()=>{
    const api=fakeApi({get:()=>new Promise(()=>undefined)});
    await ready(api);
    fireEvent.change(screen.getByLabelText("İsteğini yaz"),{target:{value:"A quiet forest at dawn"}});
    fireEvent.click(screen.getByRole("button",{name:"Üret"}));
    await waitFor(()=>expect(api.post).toHaveBeenCalledWith("/api/kuyruk",expect.objectContaining({
      prompt:"A quiet forest at dawn",negative:"",model:"z_image",image_model:"z_image",model_instance:"image-one",
      width:1024,height:1024,max_side:1024,is_tipi:"goruntu-uretim",image_base64:"",image_mime:"image/jpeg",prompt_destek:false,prompt_ceviri:false,
    })));
    const stop=await screen.findByRole("button",{name:/durdur/i});
    fireEvent.click(stop);
    await waitFor(()=>expect(api.post).toHaveBeenCalledWith("/api/is/iptal",{is_id:"synthetic-job"}));
  });

  it("Prompt Stüdyosu hızlı üretim çağrısını seçili üretim ayarlarıyla kuyruğa alır",async()=>{
    const api=fakeApi({get:vi.fn().mockResolvedValue({durum:"calisiyor",ilerleme:{oran:0}})});
    let quickGenerate:((prompt:string,negative:string,files:File[])=>void)|null=null;
    render(<ProductionWorkspace api={api} embedded registerQuickGenerate={handler=>{if(handler)quickGenerate=handler;}}/>);
    await screen.findByRole("heading",{name:"Üretim alanı"});
    await waitFor(()=>expect(quickGenerate).toBeTypeOf("function"));
    act(()=>quickGenerate?.("Quick prompt","blur",[]));
    await waitFor(()=>expect(api.post).toHaveBeenCalledWith("/api/kuyruk",expect.objectContaining({
      prompt:"Quick prompt",negative:"blur",model:"z_image",image_model:"z_image",is_tipi:"goruntu-uretim",
    })));
  });

  it("üretim sonucu videosunun sunucu kapağını kullanır",async()=>{
    const api=fakeApi({
      get:vi.fn().mockResolvedValue({
        durum:"bitti", ilerleme:{oran:1},
        sonuc:{video_url:"/gecmis/test-video.mp4",video_poster_url:"/gecmis/test-video_poster.jpg"},
      }),
    });
    await ready(api);
    fireEvent.click(screen.getByRole("tab",{name:/Video/}));
    fireEvent.change(screen.getByLabelText("İsteğini yaz"),{target:{value:"A short synthetic clip"}});
    fireEvent.click(screen.getByRole("button",{name:"Üret"}));
    await waitFor(()=>expect(document.querySelector("video")).toHaveAttribute("poster","/gecmis/test-video_poster.jpg"));
  });
});
