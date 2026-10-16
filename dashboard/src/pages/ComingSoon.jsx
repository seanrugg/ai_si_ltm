import { EmptyState, PageHeader } from "../components/ui.jsx";

// Placeholder for P4 pages. Replace with the real page; the API already exists.
export default function ComingSoon({ title, endpoint }) {
  return (
    <>
      <PageHeader title={title} />
      <EmptyState title="This page isn't built yet">
        The server side is ready (<code className="hash">{endpoint}</code>). It's on the P4 list.
      </EmptyState>
    </>
  );
}
