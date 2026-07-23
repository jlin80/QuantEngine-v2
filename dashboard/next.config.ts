import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone build so docker/dashboard.Dockerfile can ship a minimal server.
  output: "standalone",
};

export default nextConfig;
