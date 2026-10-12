import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { setMapTiles } from "@/components/map/FenceMap";

export type Branding = {
  site_name: string; site_tagline: string; company_name: string; primary_color: string;
  logo_url: string; logo_dark_url: string; favicon_url: string; login_image_url?: string;
  login_headline: string; login_subtext: string; footer_text: string; support_email: string; support_phone: string;
  maintenance_mode: boolean; maintenance_message: string; announcement_banner: string;
  /** which tiles the map screens draw on — set by the platform admin, English by default */
  map?: { url: string; attribution: string; source: string };
  /** the platform's own branding, even when a tenant's white-label is applied on top */
  platform?: Omit<Branding, "platform">;
};

/** Public branding (no login needed): site name, logo, favicon, colours — set by the platform admin. */
export function useBranding() {
  const q = useQuery<Branding>({ queryKey: ["branding"], queryFn: () => api.get("/api/v1/branding"), staleTime: 300_000, retry: false });
  const tenant = useAuth((s) => s.me?.tenant);
  const wl = tenant?.white_label ? tenant.branding || {} : null;
  // `platform` always keeps the product's own branding, so "powered by …" never shows the tenant's own name
  const d = q.data ? { platform: q.data, ...q.data, ...(wl ? { site_name: wl.app_name || tenant?.display_name || q.data.site_name, logo_url: wl.logo || q.data.logo_url, favicon_url: wl.favicon || q.data.favicon_url, primary_color: wl.primary_color || q.data.primary_color, footer_text: wl.footer_text || q.data.footer_text, site_tagline: "" } : {}) } : undefined;
  useEffect(() => {
    if (!d) return;
    document.title = d.site_name ? `${d.site_name}${d.site_tagline ? " — " + d.site_tagline : ""}` : document.title;
    if (d.favicon_url) {
      let link = document.querySelector<HTMLLinkElement>("link[rel~='icon']");
      if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
      link.href = d.favicon_url;
    }
    if (d.primary_color) document.documentElement.style.setProperty("--dms-primary", d.primary_color);
    setMapTiles(d.platform?.map || d.map);
  }, [d]);
  return d;
}
