import { createFileRoute } from "@tanstack/react-router";

import { check } from "#/api/http.ts";

export const Route = createFileRoute("/api/check")({
  server: {
    handlers: {
      POST: ({ request }) => check(request),
    },
  },
});
