import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  serverExternalPackages: ["pg", "pg-native"],
  webpack: (config, { isServer }) => {
    if (isServer) {
      // pg uses __dirname (CJS global) which isn't defined in Next.js's ESM server bundle
      config.node = { __dirname: true, __filename: true };
    }
    return config;
  },
};

export default nextConfig;
