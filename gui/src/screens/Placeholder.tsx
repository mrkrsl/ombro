// A page not built yet: its title and a line on what comes there.

const PAGES: Record<string, [string, string]> = {
  jobs: ["Jobs", "Every job, running and finished, comes here next."],
  report: ["Handover", "A job's handover, as a page of its own, comes here next."],
  approvals: ["Approvals", "What waits for your yes comes here next. Until then, `ombro approvals` in a terminal shows it."],
  scheduled: ["Scheduled work", "Your scheduled jobs come here next."],
};

export function Placeholder({ screen, args }: { screen: string; args: string }) {
  const [title, text] = PAGES[screen] ?? [screen, ""];
  return (
    <div className="page">
      <div className="column placeholder">
        <h1>{title}</h1>
        <p>{text}</p>
        {args && <p>({args})</p>}
      </div>
    </div>
  );
}
