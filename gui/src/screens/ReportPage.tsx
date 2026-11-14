// A job's report as a page of its own, from the Jobs page.

import { ArrowLeft } from "lucide-react";
import { ReportCard } from "../components/ReportCard";
import { useStore } from "../store/store";

export function ReportPage() {
  const { report, show } = useStore();
  return (
    <div className="page report-page">
      <div className="column wide-page">
        <p>
          <button className="primary back" onClick={() => show("jobs")}>
            <ArrowLeft size={16} /> Back to Jobs
          </button>
        </p>
        {report ? <ReportCard report={report} /> : <p className="muted">Opening the report…</p>}
      </div>
    </div>
  );
}
