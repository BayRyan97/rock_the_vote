import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // pg uses Node.js native bindings — keep it out of Edge/middleware bundles
  serverExternalPackages: ["pg", "pg-native"],
};

export default nextConfig;
