// Webpack customized behavior in development
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  webpack: (config, { dev }) => {
    if (dev) {
      config.watchOptions = {
        poll: 1000,                        // Track changes every 1000ms
        aggregateTimeout: 300,             // Delay 300ms before rebuilding after detecting a change
      };
    }
    return config;
  },
};

export default nextConfig;
