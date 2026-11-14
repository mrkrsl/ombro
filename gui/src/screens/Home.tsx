// Home (designs 01, 02, 17): the wordmark, who you're working with, the counts, the Lead and
// each crew with its roles, and the greeting above the input. Typing starts a chat.

import { Logo } from "../components/Logo";
import { greeting } from "../store/conversation";
import { useStore } from "../store/store";

function n(count: number, one: string, many = one + "s") {
  return (
    <p>
      <b>{count}</b> {count === 1 ? one : many}
    </p>
  );
}

export function Home() {
  const { summary, counts, opener } = useStore();
  if (!summary || opener) return <div className="page" />; // after New from a used chat: an empty page
  const c = summary.counts;
  return (
    <div className="page">
      <div className="home">
        <div className="home-grid">
          <div className="logo-wrap">
            <Logo size={62} />
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
  return (
    <div className="column">
      <div className="greeting line lead">{opener ?? greeting(name)}</div>
    </div>
  );
}
