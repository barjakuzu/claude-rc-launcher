import type { Metadata, Viewport } from "next";
import "./globals.css";

const APP_URL = process.env.APP_URL ?? "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(APP_URL),
  title: { default: "MAPIO — 3D navigation for your in-car browser", template: "%s · MAPIO" },
  description:
    "MAPIO is a premium 3D map and navigation web app designed for in-car browsers like Tesla's: 3D city maps, chargers, send-to-car from your phone, weather and official GEL rates.",
  applicationName: "MAPIO",
  openGraph: {
    type: "website",
    siteName: "MAPIO",
    title: "MAPIO — 3D navigation for your in-car browser",
    description: "3D maps, chargers, send-to-car and more — designed for the big screen in your car.",
  },
  icons: { icon: "/brand/mapio-mark.svg" },
};

export const viewport: Viewport = {
  themeColor: "#040a17",
  width: "device-width",
  initialScale: 1,
};

// Applies theme + performance mode before first paint (keeps pages statically renderable).
const bootScript = `
(function(){try{
  var d=document.documentElement;
  var t=localStorage.getItem('mapio_theme')||'dark';
  if(t==='system'){t=matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'}
  d.dataset.theme=t;
  var q=new URLSearchParams(location.search).get('perf');
  var low=q==='low'||localStorage.getItem('mapio_perf')==='low'
    ||matchMedia('(prefers-reduced-motion: reduce)').matches
    ||(navigator.deviceMemory&&navigator.deviceMemory<=2)
    ||(navigator.hardwareConcurrency&&navigator.hardwareConcurrency<=2);
  if(low)d.dataset.perf='low';
}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: bootScript }} />
      </head>
      <body className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
