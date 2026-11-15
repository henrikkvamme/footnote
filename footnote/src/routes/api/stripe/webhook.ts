import { createFileRoute } from "@tanstack/react-router";

import { stripeWebhook } from "#/api/stripe-webhook.ts";

export const Route = createFileRoute("/api/stripe/webhook")({
  server: {
    handlers: {
      POST: ({ request }) => stripeWebhook(request),
    },
  },
});
