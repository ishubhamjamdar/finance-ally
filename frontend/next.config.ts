import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // PLAN.md §3: a static export served by FastAPI, so there is one origin, one
  // port and one container. No server-side rendering at request time, no API
  // routes, no image optimisation endpoint.
  output: "export",

  // Starlette's StaticFiles(html=True) resolves a directory to its index.html.
  // Exporting `/foo/index.html` rather than `/foo.html` is what makes that
  // resolution find the file, and matters the moment Checkpoint 6 or later
  // adds a second route.
  trailingSlash: true,
};

export default nextConfig;
