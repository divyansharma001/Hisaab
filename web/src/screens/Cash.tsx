import { api } from "../api";
import { Bar, Card, CardHead, Loading, Problem, Stat } from "../components/ui";
import { useApi } from "../useApi";

/**
 * Where the money stands, once the matching has run.
 *
 * The pair that matters is "Not paid yet" against "Paid, needs your OK".
 * Both are open invoices, but one is chased with the customer and the other
 * is cleared at this desk. An aging report lumps them together and hides
 * which is which.
 */
export function Cash() {
  const { data, error } = useApi(() => api.cash());

  if (error) return <Problem message={error} />;
  if (!data) return <Loading what="the cash position" />;

  const worst = Math.max(...data.aging.map((b) => b.value.paise), 1);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Cash</h1>
        <p className="mt-1.5 text-sm text-ink-700">
          As things stand on {data.as_of}, straight from the ledger. Nothing here is a forecast.
        </p>
      </div>

      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="In the bank"
          value={data.confirmed_in.display}
          note="Matched to an invoice and signed off"
          tone="good"
        />
        <Stat
          label="Not paid yet"
          value={data.still_owed.display}
          note="No payment turned up that covers these. Chase the customer."
          tone="bad"
        />
        <Stat
          label="Paid, needs your OK"
          value={data.uncertain.display}
          note="The money is there. We would not close it without you."
          tone="warn"
        />
        <Stat
          label="Deducted on the way"
          value={data.withheld.display}
          note="Never reached your account"
        />
      </dl>

      <Card>
        <CardHead
          title="What was deducted before the money reached you"
          note="You can claim the TDS back. The gateway fee and its GST are a cost."
        />
        <dl className="divide-y divide-ink-100 text-sm">
          {[
            ["TDS withheld by customers", data.withheld_split.tds.display, "Claim this back"],
            ["Gateway fee (MDR)", data.withheld_split.mdr.display, "A cost of collecting"],
            ["GST on the gateway fee", data.withheld_split.gst.display, "A cost of collecting"],
          ].map(([label, value, note]) => (
            <div key={label} className="flex items-baseline justify-between gap-4 px-5 py-3">
              <div>
                <dt className="text-ink-1200">{label}</dt>
                <p className="mt-0.5 text-xs text-ink-700">{note}</p>
              </div>
              <dd className="tnum whitespace-nowrap font-medium">{value}</dd>
            </div>
          ))}
        </dl>
      </Card>

      <Card>
        <CardHead
          title="How old the unpaid invoices are"
          note={`Every invoice still open, paid or not - ${data.open_total.display} in total.`}
        />
        <div className="space-y-4 px-5 py-5">
          {data.aging.map((bucket) => (
            <div key={bucket.label}>
              <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
                <span className="font-medium text-ink-1200">{bucket.label}</span>
                <span className="tnum text-ink-700">
                  {bucket.count} {bucket.count === 1 ? "invoice" : "invoices"} ·{" "}
                  <span className="font-medium text-ink-1200">{bucket.value.display}</span>
                </span>
              </div>
              <div className="mt-1.5">
                <Bar
                  value={(bucket.value.paise / worst) * 100}
                  tone={
                    bucket.label === "90+ days"
                      ? "bad"
                      : bucket.label === "61-90 days"
                        ? "warn"
                        : "neutral"
                  }
                />
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
