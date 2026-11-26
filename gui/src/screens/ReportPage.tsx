// A job's handover as a page of its own, from Jobs › View handover: the same parts as the side
// panel shows when a job is delivered.

import { ArrowLeft } from "lucide-react";
import { Deliverables, HandoverText } from "../components/Handover";
import { useStore } from "../store/store";

export function ReportPage() {
  const { report, show, chats } = useStore();
  const chat = report?.chat ? chats.find((c) => c.id === report.chat) : undefined;
  return (
    <div className="page report-page handover-page">
      <div className="column wide-page">
        <p>
          <button className="primary back" onClick={() => show("jobs")}>
            <ArrowLeft size={16} /> Back to Jobs
          </button>
        </p>
        {report ? (
          <>
            <HandoverText report={report} title={chat?.title || report.name} />
            <Deliverables report={report} />
          </>
        ) : (
          <p className="muted">Opening the handover…</p>
        )}
      </div>
    </div>
  );
}
