// A job's report as a page of its own, from the Jobs page.

import { ReportCard } from "../components/ReportCard";
import { useStore } from "../store/store";

export function ReportPage() {
  const { report, show } = useStore();
  return (
    <div className="page">
      <div className="column wide-page">
        <p>
          <button className="link-button" onClick={() => show("jobs")}>
            ← Jobs
          </button>
        </p>
        {report ? <ReportCard report={report} /> : <p className="muted">Opening the report…</p>}
      </div>
    </div>
  );
}
