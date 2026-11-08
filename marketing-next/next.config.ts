import type { NextConfig } from "next";

// Static export: the marketing site builds to plain files, hostable anywhere.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  devIndicators: false,
};

export default nextConfig;
