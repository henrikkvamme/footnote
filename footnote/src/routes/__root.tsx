import { HeadContent, Scripts, createRootRoute } from "@tanstack/react-router";
import type { ReactNode } from "react";

import appCss from "../styles.css?url";

const RootDocument = ({ children }: { readonly children: ReactNode }) => (
  <html lang="en">
    <head>
      <style id="ui-sdk-layer-order">@layer theme, base, ui-sdk, components, utilities;</style>
      <HeadContent />
    </head>
    <body>
      {children}
      <Scripts />
    </body>
  </html>
);

export const Route = createRootRoute({
  head: () => ({
    links: [
      { href: appCss, rel: "stylesheet" },
      { href: "/favicon.svg", rel: "icon", type: "image/svg+xml" },
      { href: "/apple-touch-icon.png", rel: "apple-touch-icon" },
      {
        as: "font",
        crossOrigin: "anonymous",
        href: "/fonts/instrument-serif.woff2",
        rel: "preload",
        type: "font/woff2",
      },
    ],
    meta: [
      { charSet: "utf-8" },
      { content: "width=device-width, initial-scale=1", name: "viewport" },
      { title: "Footnote - Footnotes where it matters" },
      {
        content:
          "Footnote listens to what you watch. When a claim can be checked, it checks it, and shows you the sources.",
        name: "description",
      },
      { content: "#f3eee2", name: "theme-color" },
      { content: "Footnote - Footnotes where it matters", property: "og:title" },
      { content: "/film-poster.jpg", property: "og:image" },
    ],
  }),
  shellComponent: RootDocument,
});
