import { PAGE_SIZE } from "../api";

/** Offset pager for the admin listings. Renders nothing when everything fits on
 *  one page, so small orgs never see controls they do not need. */
export function Pager({
  offset,
  total,
  count,
  onChange,
}: {
  offset: number;
  total: number;
  count: number;
  onChange: (next: number) => void;
}) {
  if (total <= PAGE_SIZE) return null;

  const first = total === 0 ? 0 : offset + 1;
  const last = offset + count;
  const hasPrev = offset > 0;
  const hasNext = last < total;

  return (
    <div className="row" style={{ marginTop: "0.6rem", justifyContent: "space-between" }}>
      <span className="muted">
        {first} to {last} of {total}
      </span>
      <div className="row">
        <button disabled={!hasPrev} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}>
          Previous
        </button>
        <button disabled={!hasNext} onClick={() => onChange(offset + PAGE_SIZE)}>
          Next
        </button>
      </div>
    </div>
  );
}
