// The pages that come in later stages: Jobs, the report and Approvals (5), Scheduled (5), Settings (7).

const PAGES: Record<string, [string, string]> = {
  jobs: ["Jobs", "Every job, running and finished, comes here next."],
  report: ["Report", "A job's report, as a page of its own, comes here next."],
  approvals: ["Approvals", "What waits for your yes comes here next. Until then, `ombro approvals` in a terminal shows it."],
  scheduled: ["Scheduled work", "Your scheduled jobs come here next."],
  settings: ["Settings", "General, Crews, Folders, Tools and Skills come here in a later stage."],
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
