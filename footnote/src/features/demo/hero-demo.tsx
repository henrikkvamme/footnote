import { useAtomSet, useAtomValue } from "@effect/atom-react";
import { AsyncResult } from "effect/reactivity";
import { useEffect, useRef } from "react";

import type { DemoPhase } from "#/domain/demo.machine.ts";
import { Ref } from "#/features/page/in-view.tsx";
import { playRefused } from "#/lib/utils.ts";

import { demo } from "./demo.atoms.ts";

type Mark = "next" | "sources" | "video";

const markOf: Readonly<Record<DemoPhase, Mark>> = {
  arrived: "next",
  checking: "video",
  checkingOpen: "video",
  held: "next",
  listening: "video",
  listeningOpen: "video",
  noted: "next",
  sources: "sources",
};

const islandOf: Readonly<Record<DemoPhase, "checking" | "listening" | "result">> = {
  arrived: "result",
  checking: "checking",
  checkingOpen: "checking",
  held: "result",
  listening: "listening",
  listeningOpen: "listening",
  noted: "result",
  sources: "listening",
};

const openPhases: ReadonlySet<DemoPhase> = new Set([
  "arrived",
  "checkingOpen",
  "held",
  "listeningOpen",
]);

const marks = [
  { event: { type: "SHOW_VIDEO" }, label: "The video", mark: "video" },
  { event: { type: "SHOW_NEXT" }, label: "The next video", mark: "next" },
  { event: { type: "SHOW_SOURCES" }, label: "The sources", mark: "sources" },
] as const;

const claim = "Zelenskyy owns a $20 million mansion in Florida";

const report = "The house was still for sale. No county record names Zelenskyy.";

const petals = [0, 60, 120, 180, 240, 300] as const;

const Star = ({ className }: { readonly className: string }) => (
  <svg aria-hidden="true" className={className} viewBox="638 164 308 308">
    {petals.map((angle) => (
      <path
        d="M792 318 C778 272 756 226 756 200 A36 36 0 0 1 828 200 C828 226 806 272 792 318Z"
        key={angle}
        transform={`rotate(${angle} 792 318)`}
      />
    ))}
  </svg>
);

export const Island = ({
  kind,
  onHover,
  onTap,
  open,
  phase,
}: {
  readonly kind: "checking" | "listening" | "result";
  readonly onHover?: (inside: boolean) => void;
  readonly onTap: () => void;
  readonly open: boolean;
  readonly phase: DemoPhase;
}) => (
  <button
    aria-expanded={open}
    aria-label="Dynamic Island of Footnote"
    className="island"
    data-kind={kind}
    data-noted={phase === "noted"}
    data-open={open}
    onBlur={() => {
      onHover?.(false);
    }}
    onClick={onTap}
    onMouseEnter={() => {
      onHover?.(true);
    }}
    onMouseLeave={() => {
      onHover?.(false);
    }}
    type="button"
  >
    <span className="island-compact">
      <Star className="island-star" />
      <span className="island-short">Contradicted</span>
    </span>
    <span className="island-body" data-show={open && kind === "listening"}>
      <span className="island-promise">
        Checks the facts in what you watch. A claim that sources contradict shows here.
      </span>
      <span className="island-row">
        <span>@davejoneswoke, TikTok</span>
      </span>
    </span>
    <span className="island-body" data-show={open && kind === "checking"}>
      <Star className="island-star big-star" />
    </span>
    <span className="island-body" data-show={open && kind === "result"}>
      <span className="island-claim">
        <u>{claim}</u>
        <b>*</b>
      </span>
      <span className="island-note">
        <b className="y">*</b> <b>Sources contradict this.</b> <span>{report}</span>
      </span>
      <span className="island-row">
        <span>Earlier video: @davejoneswoke</span>
        <span>France 24, Reuters</span>
      </span>
    </span>
  </button>
);

const Sheet = ({ onClose, open }: { readonly onClose: () => void; readonly open: boolean }) => (
  <div aria-hidden={!open} className="sheet-wrap" data-open={open}>
    <span className="sheet-clock">9.41</span>
    <div className="sheet">
      <button
        aria-label="Back to the video"
        className="sheet-grab"
        onClick={onClose}
        tabIndex={open ? 0 : -1}
        type="button"
      >
        <span />
      </button>
      <p className="sheet-meta">@davejoneswoke, TikTok</p>
      <p className="sheet-title">Sources contradict this.</p>
      <p className="sheet-claim">
        <span className="mk" data-on={open}>
          {claim}
        </span>
      </p>
      <p className="sheet-meta gap">What sources report</p>
      <p className="sheet-text">{report}</p>
      <p className="sheet-link">France 24, 18 Mar 2025</p>
      <p className="sheet-link">Reuters, 2 Jan 2024</p>
      <p className="sheet-meta gap">In the video</p>
      <p className="sheet-quote">
        &ldquo;This is the house of Ukrainian President Zelenskyy.&rdquo;
      </p>
      <span className="rule" />
      <p className="sheet-end">A footnote is not a verdict. Read the sources.</p>
    </div>
  </div>
);

export const HeroDemo = () => {
  const state = useAtomValue(demo.state);
  const send = useAtomSet(demo.send);
  const first = useRef<HTMLVideoElement>(null);
  const second = useRef<HTMLVideoElement>(null);

  const phase: DemoPhase = AsyncResult.match(state, {
    onFailure: () => "listening",
    onInitial: () => "listening",
    onSuccess: ({ value }) => value._tag,
  });

  const mark = markOf[phase];
  const kind = islandOf[phase];
  const open = openPhases.has(phase);
  const heard = phase !== "listening" && phase !== "listeningOpen";
  const checked = mark !== "video";

  useEffect(() => {
    const claimVideo = first.current;
    const nextVideo = second.current;

    if (claimVideo === null || nextVideo === null) {
      return;
    }

    if (mark === "video") {
      nextVideo.pause();
      claimVideo.currentTime = 0;
      claimVideo.play().catch(playRefused);
    } else if (mark === "next") {
      claimVideo.pause();
      nextVideo.currentTime = 0;
      nextVideo.play().catch(playRefused);
    } else {
      claimVideo.pause();
      nextVideo.pause();
    }
  }, [mark]);

  useEffect(() => {
    const nextVideo = second.current;

    if (phase === "noted" && nextVideo !== null) {
      nextVideo.currentTime = 0;
    }
  }, [phase]);

  return (
    <div className="spread">
      <div className="leaf left" data-checking={kind === "checking"} data-heard={heard}>
        <p className="meta">
          Heard in the video
          <Ref n={2} />
        </p>
        <p className="quote">
          <span className="mk" data-on={checked}>
            &ldquo;This is the house of Ukrainian President Zelensky. The mansion is worth 20
            million dollars.&rdquo;
          </span>
          <span aria-hidden="true" className="star">
            *
          </span>
        </p>
      </div>
      <div className="stage">
        <div className="phone">
          <div className="screen" data-mark={mark}>
            <button
              aria-label="Swipe to the next video"
              className="feed"
              onClick={() => {
                send({ type: "SWIPE" });
              }}
              type="button"
            >
              <span className="reel">
                <video
                  autoPlay
                  disablePictureInPicture
                  loop
                  muted
                  playsInline
                  poster="/demo-poster.jpg"
                  preload="auto"
                  ref={first}
                  src="/demo-a.mp4"
                />
                <video
                  disablePictureInPicture
                  loop
                  muted
                  playsInline
                  poster="/demo-b-poster.jpg"
                  preload="auto"
                  ref={second}
                  src="/demo-b.mp4"
                />
              </span>
            </button>
            <Sheet
              onClose={() => {
                send({ type: "CLOSE" });
              }}
              open={mark === "sources"}
            />
            <Island
              kind={kind}
              onHover={(inside) => {
                send({ type: inside ? "HOVER_IN" : "HOVER_OUT" });
              }}
              onTap={() => {
                send({ type: "TAP_ISLAND" });
              }}
              open={open}
              phase={phase}
            />
          </div>
        </div>
      </div>
      <div className="leaf right">
        <span className="rule" data-on={checked} />
        <p className="note after" data-on={checked} style={{ "--d": "0.8s" }}>
          * <b>Sources contradict this.</b> <span>{report}</span>
        </p>
        <div className="sources meta after" data-on={checked} style={{ "--d": "1.1s" }}>
          <span>France 24, 18 Mar 2025</span>
          <span>Reuters, 2 Jan 2024</span>
        </div>
        <p className="verdictless after" data-on={mark === "sources"} style={{ "--d": "0.5s" }}>
          A footnote is not a verdict. Read the sources.
        </p>
      </div>
      <div className="under">
        <div className="steps">
          {marks.map((item) => (
            <button
              aria-current={item.mark === mark ? "step" : undefined}
              className="step"
              key={item.mark}
              onClick={() => {
                send(item.event);
              }}
              type="button"
            >
              <span className="track">
                <span className="fill" />
              </span>
              {item.label}
            </button>
          ))}
        </div>
        <p className="meta hint">
          Hover and tap the Island. Tap the video to swipe.
          <Ref n={1} />
        </p>
      </div>
    </div>
  );
};
