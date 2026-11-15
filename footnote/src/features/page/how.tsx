import { useAtomSet, useAtomValue } from "@effect/atom-react";
import { AsyncResult } from "effect/reactivity";

import { howDelays } from "#/domain/how.machine.ts";
import type { HowPhase } from "#/domain/how.machine.ts";
import { Island } from "#/features/demo/hero-demo.tsx";

import { how } from "./how.atoms.ts";
import { InView, Ref } from "./in-view.tsx";

type Step = "check" | "footnote" | "gate" | "sentences" | "sound";

const stepOf: Readonly<Record<HowPhase, Step>> = {
  check: "check",
  footnote: "footnote",
  footnoteOpen: "footnote",
  gate: "gate",
  sentences: "sentences",
  sound: "sound",
};

const steps = [
  { event: { type: "SHOW_SOUND" }, label: "Sound", ms: howDelays.sound, step: "sound" },
  {
    event: { type: "SHOW_SENTENCES" },
    label: "Sentences",
    ms: howDelays.sentences,
    step: "sentences",
  },
  { event: { type: "SHOW_GATE" }, label: "Gate", ms: howDelays.gate, step: "gate" },
  { event: { type: "SHOW_CHECK" }, label: "Check", ms: howDelays.check, step: "check" },
  {
    event: { type: "SHOW_FOOTNOTE" },
    label: "Footnote",
    ms: howDelays.footnote + howDelays.footnoteOpen,
    step: "footnote",
  },
] as const;

const gates = [
  { label: "Public affairs?", x: "46%" },
  { label: "Checkable claim?", x: "62%" },
  { label: "Speaker means it?", x: "78%" },
] as const;

const bars = [
  0.3, 0.55, 0.8, 0.45, 0.95, 0.6, 0.35, 0.7, 1, 0.5, 0.25, 0.65, 0.9, 0.4, 0.75, 0.55, 0.3, 0.85,
  0.6, 0.45, 0.95, 0.35, 0.7, 0.5, 0.8, 0.4, 0.6, 0.3,
] as const;

const ticks = Array.from({ length: 48 }, (_, index) => index);

const sourceCards = [
  { name: null, x: 80 },
  { name: "France 24", x: 220 },
  { name: null, x: 360 },
  { name: "Reuters", x: 500 },
  { name: null, x: 640 },
] as const;

const Wave = () => (
  <span aria-hidden="true" className="wave">
    {bars.map((height, index) => (
      <i key={`bar-${index}`} style={{ "--h": height, "--k": index }} />
    ))}
  </span>
);

const SoundScene = ({ on }: { readonly on: boolean }) => (
  <div aria-hidden="true" className="scene s-sound" data-on={on}>
    <div className="phone mini">
      <div className="screen">
        <img alt="" src="/demo-poster.jpg" />
      </div>
    </div>
    <div className="lane">
      <span className="lbl">sound</span>
      <Wave />
      <span className="frames">
        <img alt="" src="/demo-poster.jpg" style={{ "--k": 0 }} />
        <img alt="" src="/demo-poster.jpg" style={{ "--k": 1 }} />
        <img alt="" src="/demo-poster.jpg" style={{ "--k": 2 }} />
      </span>
      <span className="lbl">the screen, 1 frame each second</span>
    </div>
    <div className="server">
      <svg viewBox="0 0 90 120">
        <rect className="ln" height="116" rx="8" width="86" x="2" y="2" />
        {[14, 40, 66, 92].map((y) => (
          <g key={y}>
            <rect className="ln" height="18" rx="4" width="66" x="12" y={y} />
            <circle cx="22" cy={y + 9} r="2.4" />
            <circle cx="31" cy={y + 9} r="2.4" />
          </g>
        ))}
      </svg>
      <span>
        <span className="mk" data-on={on}>
          Hostinger VPS
        </span>
      </span>
    </div>
  </div>
);

const SentencesScene = ({ on }: { readonly on: boolean }) => (
  <div aria-hidden="true" className="scene s-sentences" data-on={on}>
    <Wave />
    <span className="lbl">speech to text, through the Hostinger AI Router</span>
    <p className="said" style={{ "--d": "0.7s" }}>
      &ldquo;This is the house of Ukrainian President Zelensky.&rdquo;
    </p>
    <p className="said" style={{ "--d": "2.4s" }}>
      &ldquo;The mansion is worth 20 million dollars.&rdquo;
    </p>
  </div>
);

const GateScene = ({ on }: { readonly on: boolean }) => (
  <div aria-hidden="true" className="scene s-gate" data-on={on}>
    <div className="jev">
      <b>Jev</b>
      <span>
        a decision model on the{" "}
        <span className="mk" data-on={on}>
          Hostinger AI Router
        </span>
      </span>
      <span className="lbl">about 0.3 s per sentence</span>
    </div>
    <div className="gates">
      {gates.map((gate) => (
        <div className="gate" key={gate.label} style={{ "--x": gate.x }}>
          <span>{gate.label}</span>
        </div>
      ))}
      <div className="card pass c1" style={{ "--d": "0.4s", "--d2": "2.1s", "--y": "24%" }}>
        &ldquo;The mansion is worth 20 million dollars.&rdquo;<b>*</b>
      </div>
      <div className="card stop c2" style={{ "--d": "0.9s", "--d2": "2.4s", "--y": "50%" }}>
        &ldquo;Things are getting worse.&rdquo;
        <small>An opinion. It stops.</small>
      </div>
      <div className="card stop c3" style={{ "--d": "1.4s", "--d2": "2.9s", "--y": "76%" }}>
        &ldquo;They say he owns a mansion. That is false.&rdquo;
        <small>The speaker refutes it. It stops.</small>
      </div>
    </div>
  </div>
);

const CheckScene = ({ on }: { readonly on: boolean }) => (
  <div aria-hidden="true" className="scene s-check" data-on={on}>
    <svg className="fig" viewBox="0 0 720 336">
      <rect className="pill" height="40" rx="20" width="320" x="200" y="6" />
      <text fontSize="15" fontStyle="italic" textAnchor="middle" x="360" y="31">
        &ldquo;The mansion is worth 20 million dollars.&rdquo;*
      </text>
      <path className="ln draw" d="M360 46 L360 74" pathLength="1" style={{ "--d": "0.2s" }} />
      <circle className="ln ring" cx="360" cy="114" r="40" />
      <circle className="ln" cx="360" cy="114" r="32" />
      <text fontSize="15" textAnchor="middle" x="360" y="119">
        agent
      </text>
      <text className="lbl" fontSize="13" textAnchor="end" x="306" y="118">
        on the Hostinger VPS
      </text>
      <text className="lbl" fontSize="13" x="414" y="118">
        GPT-6 Luna and web search
      </text>
      {sourceCards.map(({ name, x }, index) => (
        <g key={x}>
          <path
            className="ln draw"
            d={`M360 154 C360 190, ${x} 176, ${x} 212`}
            pathLength="1"
            style={{ "--d": `${0.7 + index * 0.18}s` }}
          />
          <g
            className={name === null ? "src other" : "src named"}
            style={{ "--d": `${1.3 + index * 0.18}s` }}
          >
            <rect height="78" rx="8" width="124" x={x - 62} y="214" />
            {name === null ? null : (
              <text fontSize="16" x={x - 50} y="240">
                {name}
              </text>
            )}
            <rect className="bar" height="5" rx="2.5" width="92" x={x - 50} y="254" />
            <rect className="bar" height="5" rx="2.5" width="70" x={x - 50} y="266" />
          </g>
        </g>
      ))}
      <text
        className="pop"
        fontSize="20"
        style={{ "--d": "4.2s" }}
        textAnchor="middle"
        x="360"
        y="326"
      >
        <tspan fontWeight="700">* Sources contradict this.</tspan>
      </text>
    </svg>
  </div>
);

const FootnoteScene = ({
  on,
  onTap,
  open,
}: {
  readonly on: boolean;
  readonly onTap: () => void;
  readonly open: boolean;
}) => (
  <div className="scene s-footnote" data-on={on}>
    <p className="side result">
      * <b>Sources contradict this.</b>
      <span>France 24, Reuters.</span>
    </p>
    <div className="phone">
      <div className="screen">
        <img alt="" src="/demo-b-poster.jpg" />
        <Island kind="result" onTap={onTap} open={open} phase="arrived" />
      </div>
    </div>
    <p className="side">Tap the Island.</p>
  </div>
);

const captions: Readonly<Record<Step, string>> = {
  check:
    "Only the claim that passed goes to the fact-check agent. It needs 2 independent, established sources that contradict the claim. About 8 s.",
  footnote:
    "The result shows as soon as the check is done. That can be in the next video. A tap opens the claim and the sources.",
  gate: "Jev reads each sentence and each frame, and answers yes or no. Most videos stop here.",
  sentences: "The server turns the sound into sentences.",
  sound: "The phone sends the sound and 1 frame each second to our server on a Hostinger VPS.",
};

const Meter = () => (
  <InView className="panel wide">
    <div className="meter">
      <div className="meter-row">
        <span>Jev reads</span>
        <span aria-hidden="true" className="meter-bar all">
          {ticks.map((tick) => (
            <i key={tick} style={{ "--k": tick }} />
          ))}
        </span>
      </div>
      <div className="meter-row">
        <span>The large model reads</span>
        <span aria-hidden="true" className="meter-bar few">
          {ticks.map((tick) => (
            <i className={tick === 17 ? "on" : undefined} key={tick} style={{ "--k": tick }} />
          ))}
        </span>
      </div>
    </div>
    <p className="panel-line">
      Jev is very cheap, so Footnote can listen to everything. The large model starts only for the
      few claims that pass. <span className="meta">The bars are a drawing, not a measurement.</span>
    </p>
  </InView>
);

export const How = () => {
  const state = useAtomValue(how.state);
  const send = useAtomSet(how.send);

  const phase: HowPhase = AsyncResult.match(state, {
    onFailure: () => "sound",
    onInitial: () => "sound",
    onSuccess: ({ value }) => value._tag,
  });

  const step = stepOf[phase];

  const restart = () => {
    send({ type: "SHOW_SOUND" });
  };

  return (
    <section className="section" id="how">
      <div className="how">
        <InView>
          <h2 className="h2">
            How it <span className="mk">works</span>
          </h2>
        </InView>
        <InView className="flow" onSeen={restart}>
          <div className="steps five">
            {steps.map((item) => (
              <button
                aria-current={item.step === step ? "step" : undefined}
                className="step"
                key={item.step}
                onClick={() => {
                  send(item.event);
                }}
                style={{ "--dur": `${item.ms}ms` }}
                type="button"
              >
                <span className="track">
                  <span className="fill" />
                </span>
                {item.label}
              </button>
            ))}
          </div>
          <div className="scenes">
            <SoundScene on={step === "sound"} />
            <SentencesScene on={step === "sentences"} />
            <GateScene on={step === "gate"} />
            <CheckScene on={step === "check"} />
            <FootnoteScene
              on={step === "footnote"}
              onTap={() => {
                send({ type: "TAP_ISLAND" });
              }}
              open={phase === "footnoteOpen"}
            />
          </div>
          <p className="flow-line" key={step}>
            {captions[step]}
            {step === "gate" ? <Ref n={3} /> : null}
            {step === "gate" ? <Ref n={4} /> : null}
            {step === "check" ? <Ref n={5} /> : null}
          </p>
        </InView>
        <div className="how-grid">
          <Meter />
        </div>
      </div>
    </section>
  );
};
