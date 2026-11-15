import { useAtomSet, useAtomValue } from "@effect/atom-react";
import { Field, FieldLabel } from "@ui-sdk/react/field";
import { Input } from "@ui-sdk/react/input";
import { AsyncResult } from "effect/reactivity";
import { useState } from "react";

import { stanceLine } from "#/domain/check.ts";
import type { CheckOutput } from "#/domain/check.ts";
import { InView } from "#/features/page/in-view.tsx";

import { checkFlow } from "./check-flow.atoms.ts";

const examples = [
  "Zelenskyy owns a $20 million mansion in Florida.",
  "The Great Wall of China is visible from space.",
  "Finland joined NATO in 2023.",
];

const Answer = ({ answer, text }: { readonly answer: CheckOutput; readonly text: string }) => (
  <div className="check-answer" data-testid="check-answer">
    <p className="quote">
      <span className="mk" data-on="true">
        &ldquo;{text}&rdquo;
      </span>
      <span aria-hidden="true" className="star">
        *
      </span>
    </p>
    <span className="rule" data-on="true" />
    <p className="note">
      * <b>{stanceLine[answer.stance]}</b> <span>{answer.line}</span>
    </p>
    <div className="sources meta">
      {answer.sources.map((source) => (
        <a href={source.url} key={source.url} rel="noreferrer" target="_blank">
          {source.site}
          {source.date === undefined ? "" : `, ${source.date}`}: {source.title}
        </a>
      ))}
    </div>
    <p className="meta took">Checked live in {(answer.ms / 1000).toFixed(1)} s.</p>
  </div>
);

export const CheckPanel = () => {
  const state = useAtomValue(checkFlow.state);
  const send = useAtomSet(checkFlow.send);
  const [text, setText] = useState("");

  const flow = AsyncResult.match(state, {
    onFailure: () => null,
    onInitial: () => null,
    onSuccess: ({ value }) => value,
  });

  const tag = flow?.value ?? "editing";

  const submit = (claim: string) => {
    const clean = claim.trim();

    if (clean.length >= 8 && tag !== "checking") {
      send({ text: clean.slice(0, 300), type: "SUBMIT" });
    }
  };

  return (
    <section className="section" id="check">
      <InView className="check">
        <h2 className="h2">
          Check a <span className="mk">claim</span>
        </h2>
        <p className="check-lead">
          Type a sentence. The agent that answers the phone answers you, live.
        </p>
        <form
          className="check-form"
          onSubmit={(event) => {
            event.preventDefault();
            submit(text);
          }}
        >
          <Field className="check-field" name="claim">
            <FieldLabel className="sr-only">A claim</FieldLabel>
            <Input
              className="check-input"
              maxLength={300}
              onValueChange={setText}
              placeholder="A claim you heard"
              value={text}
            />
          </Field>
          <button className="btn" disabled={tag === "checking"} type="submit">
            <span aria-hidden="true" className={tag === "checking" ? "star spin" : "star"}>
              *
            </span>
            {tag === "checking" ? "Checking" : "Check"}
          </button>
        </form>
        <div className="chips">
          {examples.map((example) => (
            <button
              className="chip"
              key={example}
              onClick={() => {
                setText(example);
                submit(example);
              }}
              type="button"
            >
              {example}
            </button>
          ))}
        </div>
        <div aria-live="polite" className="check-out">
          {tag === "checking" && (
            <p className="meta">Searching the sources. This takes about 8 s.</p>
          )}
          {tag === "failed" && (
            <p className="meta" role="alert">
              The agent did not answer. Try again in a minute.
            </p>
          )}
          {flow !== null && flow.context.answer !== null && (
            <Answer answer={flow.context.answer} text={flow.context.text} />
          )}
        </div>
      </InView>
    </section>
  );
};
