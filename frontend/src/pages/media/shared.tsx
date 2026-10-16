import { useQuery } from "@tanstack/react-query";
import { Box, FileText, Film, Image as ImageIcon, Play } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { Alert } from "../../components/ui";

export type Asset = {
  id: string; kind: "image" | "gif" | "video" | "audio" | "document" | "model3d"; mime: string; width: number | null; height: number | null;
  duration_s: number | null; size_bytes: number; source: string; prompt: string | null; alt_text: string | null;
  created_at: string; url: string; thumb_url: string | null; mp4_url?: string; public_url?: string | null;
  meta: Record<string, any>;
};
export type AssetPage = { items: Asset[]; total: number; page: number; page_size: number };

export type TemplateField = { name: string; label: string; type: "text" | "textarea" | "list" | "number" };
export type Template = {
  name: string; label: string; description: string; kinds: string[]; theme: string; photo: boolean; animated: boolean;
  group: string; themes: string[]; three_d: boolean; fields: TemplateField[];
  preview_url: string; preview_urls: Record<string, string>;
};
export type PlatformFormat = {
  id: string; label: string; image_size: [number, number]; video_size: [number, number]; max_images: number;
  post_types: string[]; needs_public_media: boolean;
};
export type TemplatesResp = {
  templates: Template[]; kinds: Record<string, string>; platforms: PlatformFormat[]; sizes: { id: string; label: string }[];
  scenes3d: { id: string; label: string; description: string }[]; occasions: { id: string; label: string }[]; preview_sizes: string[];
};
export type Caps = {
  image: boolean; stock: string[]; tts: string[]; video: boolean; media_host: boolean; renderer: string; music: string[];
  webgl: boolean | null;
};
export type Voice = { provider: string; id: string; locale: string; gender: string; label: string };

export function useTemplates(size = "1080x1350") {
  return useQuery({
    queryKey: ["media-templates", size], queryFn: () => api<TemplatesResp>(`/media/templates?size=${size}`),
    staleTime: 30 * 60_000, placeholderData: (prev) => prev,
  });
}

export const PREVIEW_SIZE_LABEL: Record<string, string> = {
  "1080x1080": "Square 1:1", "1080x1350": "Portrait 4:5", "1080x1920": "Story / Reel 9:16", "1920x1080": "Landscape 16:9",
  "1000x1500": "Pinterest 2:3",
};

export function useCaps() {
  return useQuery({ queryKey: ["media-caps"], queryFn: () => api<Caps>("/media/capabilities"), staleTime: 60_000 });
}

export const KIND_LABEL: Record<string, string> = {
  image: "Image", gif: "GIF", video: "Video", audio: "Audio", document: "PDF", model3d: "3D model",
};

export const SOURCE_LABEL = (s: string) => {
  if (s === "upload") return "Uploaded";
  if (s.startsWith("ai:")) return "AI image";
  if (s.startsWith("stock:")) return `Stock · ${s.slice(6)}`;
  if (s.startsWith("template:")) return "Branded template";
  return s;
};

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** Thumbnail / inline preview that works for every asset kind. */
export function AssetPreview({ asset, controls = false, className = "" }: { asset: Asset; controls?: boolean; className?: string }) {
  const alt = asset.alt_text ?? "";
  if (asset.kind === "video") {
    return controls ? (
      <video src={asset.url} poster={asset.thumb_url ?? undefined} controls playsInline preload="metadata"
        className={`h-full w-full bg-slate-900 object-contain ${className}`} />
    ) : (
      <div className={`relative h-full w-full bg-slate-900 ${className}`}>
        {asset.thumb_url
          ? <img src={asset.thumb_url} alt={alt} loading="lazy" className="h-full w-full object-cover" />
          : <div className="grid h-full place-items-center text-slate-400"><Film size={28} /></div>}
        <span className="absolute inset-0 grid place-items-center">
          <span className="grid h-10 w-10 place-items-center rounded-full bg-slate-900/60 text-white"><Play size={18} /></span>
        </span>
      </div>
    );
  }
  if (asset.kind === "document") {
    return asset.thumb_url
      ? <img src={asset.thumb_url} alt={alt} loading="lazy" className={`h-full w-full object-cover ${className}`} />
      : <div className={`grid h-full w-full place-items-center bg-slate-100 text-muted ${className}`}><FileText size={28} /></div>;
  }
  if (asset.kind === "model3d") {
    return asset.thumb_url
      ? <img src={asset.thumb_url} alt={alt} loading="lazy" className={`h-full w-full object-cover ${className}`} />
      : <div className={`grid h-full w-full place-items-center bg-slate-900 text-slate-400 ${className}`}><Box size={28} /></div>;
  }
  if (asset.kind === "audio") {
    return <audio src={asset.url} controls className="w-full" />;
  }
  return asset.url
    ? <img src={asset.url} alt={alt} loading="lazy" className={`h-full w-full ${controls ? "object-contain" : "object-cover"} ${className}`} />
    : <div className="grid h-full place-items-center text-muted"><ImageIcon size={28} /></div>;
}

export function ProviderHint({ what }: { what: "image" | "stock" }) {
  return (
    <Alert tone="info">
      {what === "image"
        ? <>No AI image provider is set up yet. Add one (a free Hugging Face token, Gemini, or local ComfyUI) in{" "}</>
        : <>No stock media provider is set up yet. Add a free Pexels, Pixabay or Unsplash key in{" "}</>}
      <Link to="/settings/integrations" className="font-medium underline">Settings → Integrations</Link>.{" "}
      Branded templates work without any provider.
    </Alert>
  );
}

export function listToText(v: unknown): string {
  return Array.isArray(v) ? v.join("\n") : typeof v === "string" ? v : "";
}

export function textToList(v: string): string[] {
  return v.split("\n").map((x) => x.trim()).filter(Boolean);
}
