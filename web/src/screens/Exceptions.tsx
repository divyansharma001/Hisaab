import { api } from "../api";
import { Card, Loading, OutcomeBadge, Problem, ReasonBadge } from "../components/ui";
import { useApi } from "../useApi";

/**
 * The demo opens here, not on the headline.
 *
 * The brief warns that one cherry-picked match proves nothing, so we lead with
 * what we could not do. Every row names a reason code and the evidence behind
 * it - never "low confidence".
 */
export function Exceptions({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, error } = useApi(() => api.exceptions());

  if (error) return <Problem message={error} />;
  if (!data) return <Loading what="exceptions" />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            What we could not do
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            {data.count} records held for a human, worth{" "}
            <span className="font-medium tabular-nums text-slate-900">{data.total.display}</span>.
            Every one names a reason.
          </p>
        </div>
      </div>

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                <th className="px-5 py-3">Invoice</th>
                <th className="px-5 py-3">Counterparty</th>
                <th className="px-5 py-3 text-right">Amount</th>
                <th className="px-5 py-3">Outcome</th>
                <th className="px-5 py-3">Reason</th>
                <th className="px-5 py-3">Why</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {data.exceptions.map((row) => (
                <tr
                  key={row.invoice_id}
                  onClick={() => onOpen(row.invoice_id)}
                  className="cursor-pointer transition-colors hover:bg-slate-50"
                >
                  <td className="px-5 py-3 font-mono text-xs font-medium text-slate-900">
                    {row.invoice_id}
                  </td>
                  <td className="px-5 py-3 text-slate-700">{row.counterparty}</td>
                  <td className="px-5 py-3 text-right font-medium tabular-nums text-slate-900">
                    {row.amount.display}
                  </td>
                  <td className="px-5 py-3">
                    <OutcomeBadge outcome={row.outcome} />
                  </td>
                  <td className="px-5 py-3">
                    <ReasonBadge code={row.reason_code} />
                  </td>
                  <td className="max-w-md px-5 py-3 text-xs leading-relaxed text-slate-600">
                    {row.reason_text}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <p className="text-xs text-slate-500">Click any row to see the whole decision.</p>
    </div>
  );
}
