import { createFileRoute } from "@tanstack/react-router";

import { health } from "#/api/http.ts";

export const Route = createFileRoute("/api/health")({
  server: {
    handlers: {
      GET: () => health(),
    },
  },
});
