import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Leaflet imports browser globals; exclude from SSR bundling
  serverExternalPackages: [],
};

export default nextConfig;
