import { useAtomSet, useAtomValue } from "@effect/atom-react";
import { AsyncResult } from "effect/reactivity";
import { useEffect } from "react";

import { InView } from "#/features/page/in-view.tsx";

import { checkout } from "./checkout.atoms.ts";

export const Pricing = ({ cancelled }: { readonly cancelled: boolean }) => {
  const state = useAtomValue(checkout.state);
  const send = useAtomSet(checkout.send);

  const flow = AsyncResult.match(state, {
    onFailure: () => null,
    onInitial: () => null,
    onSuccess: ({ value }) => value,
  });

  const tag = flow?.value ?? "idle";
  const url = flow?.context.url ?? null;
  const busy = tag === "starting" || tag === "redirecting";

  useEffect(() => {
    if (url !== null) {
      window.location.assign(url);
    }
  }, [url]);

  return (
    <section className="section" id="pricing">
      <InView className="pricing">
        <p className="meta">Subscription</p>
        <h2 className="h2">
          20 USD <span className="mk">each month</span>
        </h2>
        <p className="pricing-line">A subscription to use Footnote. You pay with Stripe.</p>
        <button
          className={busy ? "btn wait" : "btn"}
          disabled={busy}
          onClick={() => {
            send({ type: "START" });
          }}
          type="button"
        >
          <span aria-hidden="true" className={busy ? "star spin" : "star"}>
            *
          </span>
          {busy ? "Opening checkout" : "Subscribe"}
        </button>
        <div aria-live="polite" className="pricing-out">
          {tag === "failed" && (
            <p className="meta" role="alert">
              Checkout did not open. Try again in a minute.
            </p>
          )}
          {tag === "idle" && cancelled && (
            <p className="meta">Checkout was cancelled. You paid nothing.</p>
          )}
        </div>
      </InView>
    </section>
  );
};
