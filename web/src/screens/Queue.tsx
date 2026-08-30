import { api } from "../api";
import {
  Card,
  Empty,
  Label,
  Loading,
  Problem,
  ReasonChip,
  StatusPill,
} from "../components/ui";
import { useApi } from "../useApi";
import { reasonText } from "../words";

/**
 * The work queue. The app opens here.
 *
 * Sorted by value, biggest first, because that is the order a finance person
 * works a pile in. Every row says what is wrong in a sentence, so nobody has
 * to open a record to find out whether it is worth opening.
 */
export function Queue({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, error } = useApi(() => api.exceptions());

  if (error) return <Problem message={error} />;
  if (!data) return <Loading what="this batch" />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Waiting for you</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-700">
          {data.count} invoices we could not close on our own, worth{" "}
          <span className="tnum font-medium text-ink-1200">{data.total.display}</span> together.
          Biggest first. Open any row to see why.
        </p>
      </div>

      {data.count === 0 ? (
        <Empty
          title="Nothing to review"
          body="Every invoice in this batch was matched to a payment and closed. Anything we could not account for would appear here, with the reason."
        />
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-ink-200 bg-ink-50 text-left">
                  <th className="whitespace-nowrap px-5 py-2.5">
                    <Label>Invoice</Label>
                  </th>
                  <th className="px-5 py-2.5">
                    <Label>Customer</Label>
                  </th>
                  <th className="whitespace-nowrap px-5 py-2.5 text-right">
                    <Label>Amount</Label>
                  </th>
                  <th className="px-5 py-2.5">
                    <Label>Status</Label>
                  </th>
                  <th className="px-5 py-2.5">
                    <Label>What is wrong</Label>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {data.exceptions.map((row) => (
                  <tr
                    key={row.invoice_id}
                    tabIndex={0}
                    role="button"
                    onClick={() => onOpen(row.invoice_id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onOpen(row.invoice_id);
                      }
                    }}
                    className="cursor-pointer transition-colors duration-150 hover:bg-brand-50/60"
                  >
                    <td className="whitespace-nowrap px-5 py-3 font-mono text-xs font-medium">
                      {row.invoice_id}
                    </td>
                    <td className="px-5 py-3 text-ink-1200">{row.counterparty}</td>
                    <td className="tnum whitespace-nowrap px-5 py-3 text-right font-medium">
                      {row.amount.display}
                    </td>
                    <td className="px-5 py-3">
                      <StatusPill outcome={row.outcome} />
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex max-w-lg items-start gap-2">
                        <ReasonChip>{reasonText(row.reason_code)}</ReasonChip>
                      </div>
                      <p className="mt-1 max-w-lg text-xs leading-relaxed text-ink-700">
                        {row.reason_text}
                      </p>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Assisted onOpen={onOpen} />
    </div>
  );
}

/**
 * The handful of invoices where the assistant was asked for an opinion.
 *
 * These are not problems - they are the ones it helped close - so they sit
 * below the queue rather than inside it. Without this panel there is no way
 * to reach them at all: they are settled, so they never appear above.
 */
function Assisted({ onOpen }: { onOpen: (id: string) => void }) {
  const { data } = useApi(() => api.adjudicated());

  if (!data || !data.count) return null;

  return (
    <Card>
      <div className="border-b border-ink-200 px-5 py-3.5">
        <h2 className="text-sm font-semibold text-ink-1200">
          The assistant helped on {data.count} of {data.of_total}
        </h2>
        <p className="mt-1 text-xs leading-relaxed text-ink-700">
          Everything else was worked out by arithmetic alone. On these the payment was not obvious,
          so we asked for a second opinion - and the same checks still had the final say.
        </p>
      </div>

      <div className="divide-y divide-ink-100">
        {data.records.map((row) => (
          <div
            key={row.invoice_id}
            tabIndex={0}
            role="button"
            onClick={() => onOpen(row.invoice_id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onOpen(row.invoice_id);
              }
            }}
            className="cursor-pointer px-5 py-3.5 transition-colors duration-150 hover:bg-brand-50/60"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2.5">
                <span className="whitespace-nowrap font-mono text-xs font-medium">
                  {row.invoice_id}
                </span>
                <StatusPill outcome={row.outcome} />
                <span className="text-sm text-ink-1200">{row.counterparty}</span>
              </div>
              <span className="tnum whitespace-nowrap text-sm font-medium">
                {row.amount.display}
              </span>
            </div>
            {row.reasoning && (
              <p className="mt-1.5 max-w-3xl text-xs leading-relaxed text-ink-700">
                {row.reasoning}
              </p>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}
