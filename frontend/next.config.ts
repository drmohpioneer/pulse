import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // TODO: Add production-grade security headers after deployment target is chosen.
  // The dev overlay badge sits on top of the dashboard and shows up in screen capture.
  devIndicators: false,
};

export default nextConfig;

