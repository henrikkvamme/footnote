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
    ],
    meta: [
      { charSet: "utf-8" },
      { content: "width=device-width, initial-scale=1", name: "viewport" },
      { title: "footnote-agent" },
    ],
  }),
  shellComponent: RootDocument,
});
