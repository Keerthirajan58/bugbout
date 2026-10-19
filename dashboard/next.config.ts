import type { NextConfig } from "next";

/**
 * A fully static site: every page is pre-rendered from the exported suite
 * bundles at build time, so the deployment needs no server, keys or database.
 */
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
};

export default nextConfig;
