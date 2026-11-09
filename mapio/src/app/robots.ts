import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  const base = process.env.APP_URL ?? "http://localhost:3000";
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: ["/app", "/drive", "/account", "/admin", "/onboarding", "/api", "/pair", "/dev", "/auth"],
      },
    ],
    sitemap: `${base}/sitemap.xml`,
  };
}
