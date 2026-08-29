import { api } from "../api";
import { Card, CardHead, Loading, Problem, Stat } from "../components/ui";
import { useApi } from "../useApi";

/**
 * The second half of the track title, without a forecasting agent.
 *
 * Four numbers that fall out of data reconciliation already produced. The
 * interesting one is Uncertain: money the business cannot currently account
 * for, which no spreadsheet shows.
 */
export function Cash() {
  const { data, error } = useApi(() => api.cash());

  if (error) return <Problem message={error} />;
  if (!data) return <Loading what="the cash position" />;

  const worst = Math.max(...data.aging.map((b) => b.value.paise), 1);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">Cash position</h1>
        <p className="mt-1 text-sm text-slate-600">As of {data.as_of}, straight from the ledger.</p>
      </div>

      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Confirmed in" value={data.confirmed_in.display} note="matched and signed off" tone="good" />
        <Stat label="Still owed" value={data.still_owed.display} note="invoices with nothing against them" />
        <Stat label="In flight" value={data.in_flight.display} note="gateway sent, bank has not shown it" />
        <Stat label="Uncertain" value={data.uncertain.display} note={data.uncertain_note} tone="warn" />
      </dl>

      <Card>
        <CardHead title="How old the unpaid money is" note="on what is still owed" />
        <div className="space-y-4 px-5 py-5">
          {data.aging.map((bucket) => (
            <div key={bucket.label}>
              <div className="flex items-baseline justify-between text-sm">
                <span className="text-slate-700">{bucket.label}</span>
                <span className="tabular-nums text-slate-500">
                  {bucket.count} {bucket.count === 1 ? "invoice" : "invoices"} ·{" "}
                  <span className="font-medium text-slate-900">{bucket.value.display}</span>
                </span>
              </div>
              <div className="mt-1.5 h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
                <div
                  className={`h-full rounded-full ${
                    bucket.label === "90+ days"
                      ? "bg-rose-500"
                      : bucket.label === "61-90 days"
                        ? "bg-amber-500"
                        : "bg-slate-600"
                  }`}
                  style={{ width: `${(bucket.value.paise / worst) * 100}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
