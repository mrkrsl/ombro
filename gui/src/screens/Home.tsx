// Home (designs 01, 02, 17): the Lead's avatar in the wordmark's place, who you're working with, the counts, the Lead and
// each crew with its roles, and the greeting above the input. Typing starts a chat.

import { HomeAvatar } from "../components/Avatar";
import { Markdown } from "../components/Markdown";
import { opening } from "../store/conversation";
import { useStore } from "../store/store";

function n(count: number, one: string, many = one + "s") {
  return (
    <p>
      <b>{count}</b> {count === 1 ? one : many}
    </p>
  );
}

export function Home() {
  const { summary, counts } = useStore();
  if (!summary) return <div className="page" />;
  // Every new chat starts here, after /new too: the line above the input says the last chat was saved.
  const c = summary.counts;
  return (
    <div className="page">
      <div className="home">
        <div className="home-grid">
          <div className="logo-wrap">
            <HomeAvatar />
          </div>
          <h2 className="who">{summary.name ? `Working with ${summary.name}` : "Working with you"}</h2>
          <div className="facts">
            {n(c.crews_ready, "crew ready to work", "crews ready to work")}
            {n(counts?.active ?? c.active_jobs, "active job")}
            {n(counts?.scheduled ?? c.scheduled_jobs, "scheduled job")}
            {n(counts?.waiting ?? c.waiting, "item to approve", "items to approve")}
            <h3 style={{ marginTop: 36 }}>{summary.lead.title === "Lead" ? "Ombro Lead" : summary.lead.title}</h3>
            {summary.crews.map((crew) => (
              <div key={crew.name}>
                <h3>{crew.title}</h3>
                {crew.roles.length === 0 && <p className="role">No roles yet</p>}
                {crew.roles.map((r) => (
                  <p key={r.name} className="role">
                    {r.title}
                  </p>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/** The Lead's first line, above the input: the greeting, or that the last chat was saved. It is
 * the conversation's first line, so it looks like one. */
export function HomeGreeting() {
  const name = useStore((s) => s.summary?.name ?? "");
  const opener = useStore((s) => s.opener);
  const first = useStore((s) => s.chats.length === 0);
  // A fresh install: set-up's first question is the greeting (it arrives a moment after start).
  const welcome = useStore((s) => (s.welcome ? s.conversations[s.current ?? ""]?.question?.field.label ?? "" : null));
  if (welcome === "") return null;
  return (
    <div className="column">
      <Markdown className="greeting line lead" text={welcome ?? opener ?? opening(name, first)} />
    </div>
  );
}
