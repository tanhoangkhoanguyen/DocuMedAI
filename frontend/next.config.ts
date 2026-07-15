// Webpack customized behavior in development
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Turbopack is the default builder in Next.js 16. An empty config opts in
  // explicitly and silences the "webpack config with no turbopack config" error.
  turbopack: {},
  // Only used when running with `--webpack` (e.g. `next dev --webpack`).
  // Enables polling-based file watching, which is needed inside Docker.
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
