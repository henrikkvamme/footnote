import { createFileRoute, Link } from "@tanstack/react-router";
import { Option, Schema } from "effect";

import { subscription } from "#/api/billing.ts";
import { priceLine, SessionId } from "#/domain/billing.ts";
import type { Subscription } from "#/domain/billing.ts";
import { Foot, Top } from "#/features/page/sections.tsx";

const SubscribedSearch = Schema.Struct({
  session_id: Schema.optional(Schema.String),
});

type Found =
  | { readonly kind: "found"; readonly subscription: Subscription }
  | { readonly kind: "missing" }
  | { readonly kind: "unreachable" };

const Confirmed = ({ email }: { readonly email: string | null }) => (
  <>
    <h1 className="h2">
      You are{" "}
      <span className="mk" data-on="true">
        subscribed
      </span>
    </h1>
    <p className="legal-lead">
      Stripe confirms your subscription to Footnote, {priceLine}.
      {email === null ? "" : ` You subscribed with ${email}.`}
    </p>
  </>
);

const Pending = () => (
  <>
    <h1 className="h2">
      Almost{" "}
      <span className="mk" data-on="true">
        there
      </span>
    </h1>
    <p className="legal-lead">
      Stripe has not confirmed the payment yet. Refresh this page in a minute.
    </p>
  </>
);

const Ended = () => (
  <>
    <h1 className="h2">
      Not{" "}
      <span className="mk" data-on="true">
        active
      </span>
    </h1>
    <p className="legal-lead">This subscription is no longer active.</p>
  </>
);

const pendingStatuses = new Set(["open", "incomplete"]);

const Missing = () => (
  <>
    <h1 className="h2">
      No{" "}
      <span className="mk" data-on="true">
        subscription
      </span>
    </h1>
    <p className="legal-lead">We found no checkout for this link. You paid nothing here.</p>
  </>
);

const Unreachable = () => (
  <>
    <h1 className="h2">
      One{" "}
      <span className="mk" data-on="true">
        moment
      </span>
    </h1>
    <p className="legal-lead">We could not reach Stripe. Refresh this page in a minute.</p>
  </>
);

const Subscribed = () => {
  const found = Route.useLoaderData();

  return (
    <>
      <Top />
      <main className="legal" data-testid="subscribed">
        <p className="meta">Footnote, subscription</p>
        {found.kind === "missing" && <Missing />}
        {found.kind === "unreachable" && <Unreachable />}
        {found.kind === "found" && found.subscription.active && (
          <Confirmed email={found.subscription.email} />
        )}
        {found.kind === "found" &&
          !found.subscription.active &&
          pendingStatuses.has(found.subscription.status) && <Pending />}
        {found.kind === "found" &&
          !found.subscription.active &&
          !pendingStatuses.has(found.subscription.status) && <Ended />}
        <section>
          <span className="rule" data-on="true" />
          <p className="subscribed-back">
            <Link to="/">Back to Footnote</Link>
          </p>
        </section>
      </main>
      <Foot />
    </>
  );
};

export const Route = createFileRoute("/subscribed")({
  component: Subscribed,
  head: () => ({
    meta: [{ title: "Footnote - Subscribed" }, { content: "noindex", name: "robots" }],
  }),
  loader: async ({ deps }): Promise<Found> => {
    const sessionId = Schema.decodeUnknownOption(SessionId)(deps.sessionId);

    if (Option.isNone(sessionId)) {
      return { kind: "missing" };
    }

    const result = await subscription({ data: { sessionId: sessionId.value } });

    if (result.ok) {
      return { kind: "found", subscription: result.value };
    }

    return result.error.reason === "not-found" ? { kind: "missing" } : { kind: "unreachable" };
  },
  loaderDeps: ({ search }: { readonly search: typeof SubscribedSearch.Type }) => ({
    sessionId: search.session_id,
  }),
  validateSearch: Schema.toStandardSchemaV1(SubscribedSearch),
});
