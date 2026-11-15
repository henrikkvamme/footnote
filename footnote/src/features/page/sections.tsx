import { betaUrl } from "#/domain/beta.ts";
import { playRefused } from "#/lib/utils.ts";

import { InView } from "./in-view.tsx";

const Icon = () => (
  <svg aria-hidden="true" viewBox="0 0 1024 1024">
    <rect fill="#ffe45c" height="1024" width="1024" />
    <g fill="#211e18">
      <rect height="86" rx="43" width="452" x="188" y="563" />
      {[0, 60, 120, 180, 240, 300].map((angle) => (
        <path
          d="M792 318 C778 272 756 226 756 200 A36 36 0 0 1 828 200 C828 226 806 272 792 318Z"
          key={angle}
          transform={`rotate(${angle} 792 318)`}
        />
      ))}
    </g>
  </svg>
);

export const Top = () => (
  <header className="top">
    <a className="brand" href="/">
      <Icon />
      Footnote
    </a>
    <nav>
      <a href="/#how">How it works</a>
      <a href="/#film">The film</a>
      <a href="/#beta">Beta</a>
    </nav>
  </header>
);

const playFilm = () => {
  const film = document.querySelector<HTMLVideoElement>("#film video");

  if (film === null) {
    return;
  }

  film.scrollIntoView({ behavior: "smooth", block: "center" });
  film.play().catch(playRefused);
};

export const HeroText = () => (
  <InView className="hero-head">
    <h1>
      Footnotes where it <span className="mk">matters</span>
      <span aria-hidden="true" className="ast">
        *
      </span>
    </h1>
    <p className="hero-note">
      * Footnote listens to what you watch, in any app on your iPhone. When a claim can be checked,
      it checks it, and shows you the sources.
    </p>
    <div className="hero-actions">
      <button className="btn" onClick={playFilm} type="button">
        <span aria-hidden="true" className="star">
          *
        </span>
        Watch the film
      </button>
      <a className="btn quiet" href="#how">
        How it works
      </a>
    </div>
  </InView>
);

const apps = ["TikTok", "Instagram", "YouTube", "X", "Telegram", "Facebook", "News sites"];

export const AnyApp = () => (
  <section className="section" id="apps">
    <InView>
      <p className="statement rv">
        It works over <span className="mk">any app</span> on your iPhone.{" "}
        <span className="dim">Footnote reads the sound and the screen, not one app.</span>
      </p>
      <ul className="apps rv" style={{ "--i": 1 }}>
        {apps.map((app) => (
          <li key={app}>{app}</li>
        ))}
      </ul>
    </InView>
  </section>
);

export const Statement = () => (
  <section className="section">
    <InView>
      <p className="statement rv">
        It never says true or false. It says <span className="mk">sources contradict this</span>,
        and shows you the sources. <span className="dim">It does not tell you what to think.</span>
      </p>
    </InView>
  </section>
);

export const Hostinger = () => (
  <section className="section" id="hostinger">
    <InView className="stack">
      <h2 className="h2">
        Built on <span className="mk">Hostinger</span>
      </h2>
      <div className="rows">
        <div className="row rv" style={{ "--i": 1 }}>
          <div className="k">VPS</div>
          <div className="v">
            The Footnote server, the fact-check agent and this page run on one Hostinger VPS.
          </div>
        </div>
        <div className="row rv" style={{ "--i": 2 }}>
          <div className="k">AI Router</div>
          <div className="v">
            <div>
              The speech<span className="meta">gemini-3.1-flash-lite</span>
            </div>
            <div>
              The gate, for sentences and frames<span className="meta">Jev, jev-latest</span>
            </div>
            <div>
              The check<span className="meta">GPT-6 Luna and web search</span>
            </div>
          </div>
        </div>
        <div className="row rv" style={{ "--i": 3 }}>
          <div className="k">The phone</div>
          <div className="v">
            An iPhone app. It works over any app on the phone. The Dynamic Island is the whole
            interface over the video.
          </div>
        </div>
      </div>
    </InView>
  </section>
);

export const Film = () => (
  <section className="section" id="film">
    <InView className="film">
      <h2 className="h2">
        The <span className="mk">film</span>
      </h2>
      <video
        className="rv"
        controls
        playsInline
        poster="/film-poster.jpg"
        preload="none"
        src="/film.mp4"
      >
        <track default={false} kind="captions" label="English" src="/film.vtt" srcLang="en" />
      </video>
    </InView>
  </section>
);

export const Closing = ({ betaActive }: { readonly betaActive: boolean }) => (
  <section className="section" id="beta">
    <InView>
      <p className="close-line">It does not tell you what to think.</p>
      <p className="big">
        You <span className="mk">decide.</span>
      </p>
      <div className="beta">
        <a
          className={betaActive ? "btn" : "btn wait"}
          href={betaUrl}
          rel="noreferrer"
          target="_blank"
        >
          <span aria-hidden="true" className="star">
            *
          </span>
          Get the public beta on TestFlight
        </a>
        {betaActive ? null : (
          <p className="meta">
            The beta is in review at Apple. The link opens when the review is done.
          </p>
        )}
      </div>
    </InView>
  </section>
);

const notes = [
  "The phone shows a screen recording of Footnote on an iPhone, made in the Filming mode of the app. It shows the stored result of a real check. It is not a live run.",
  "The claim is from a TikTok video by @davejoneswoke. Lead Stories, USA TODAY, Reuters and France 24 published fact checks of it.",
  "Jev is a decision model on the Hostinger AI Router. The time is for one sentence.",
  "The 2 sentences that stop are examples, written for this page.",
  "The agent is our TypeScript harness on the Hostinger VPS. It uses GPT-6 Luna and web search through the Hostinger AI Router.",
] as const;

export const Notes = () => (
  <section className="notes">
    <span className="rule" />
    <ol>
      {notes.map((note, index) => (
        <li id={`note-${index + 1}`} key={note}>
          <span>{index + 1}</span>
          <span>{note}</span>
        </li>
      ))}
    </ol>
  </section>
);

export const Foot = () => (
  <footer className="foot">
    <p className="meta">
      Made at Junction 2026 for the Hostinger challenge, Decode the Noise. Film footage: State
      Emergency Service of Ukraine and National Police of Ukraine, CC BY 4.0, through Wikimedia
      Commons. Music: Kevin MacLeod, CC BY 4.0. <a href="/privacy">Privacy</a>
    </p>
    <span aria-hidden="true" className="word">
      Footnote
    </span>
  </footer>
);
