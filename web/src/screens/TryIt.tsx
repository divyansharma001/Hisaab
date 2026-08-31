import { useEffect, useState } from "react";
import { api } from "../api";
import {
  Button,
  Card,
  CardHead,
  Empty,
  Label,
  Problem,
  ReasonChip,
  StatusPill,
} from "../components/ui";
import type { SandboxContents, SandboxResult } from "../types";
import { reasonText } from "../words";

/**
 * Try the matcher on your own figures.
 *
 * Reading that we handle TDS is one thing; watching it explain a Rs 3,000 gap
 * on a number you chose is another. Type in a couple of invoices and the bank
 * lines that paid them, and see what it decides.
 *
 * Nothing here is scored. These rows have no right answer, so the screen
 * reports decisions and reasons and never an accuracy figure.
 */
export function TryIt() {
  const [data, setData] = useState<SandboxContents | null>(null);
  const [result, setResult] = useState<SandboxResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.sandbox
      .read()
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  async function run<T>(work: () => Promise<T>, after: (value: T) => void) {
    setBusy(true);
    setError(null);
    try {
      after(await work());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  if (error && !data) return <Problem message={error} />;

  const nothingYet = data && !data.invoices.length && !data.payments.length;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="display text-2xl">Try it on your own figures</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-700">
          Add an invoice and the bank line that paid it, then match them. Nothing you add here
          touches the batch or the accuracy numbers - it is a scratch pad, and you can clear it at
          any time.
        </p>
      </div>

      {error && (
        <Card className="border-bad-500/30 bg-bad-50 px-5 py-3">
          <p className="text-sm text-bad-700">{error}</p>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <AddInvoice
          busy={busy}
          onAdd={(body) => run(() => api.sandbox.addInvoice(body), setData)}
        />
        <AddPayment
          busy={busy}
          onAdd={(body) => run(() => api.sandbox.addPayment(body), setData)}
        />
      </div>

      {nothingYet ? (
        <Empty
          title="Nothing added yet"
          body="Try an invoice for Rs 1,50,000 and a payment of Rs 1,47,000 with a narration like NEFT/YOURCUSTOMER/HDFC0001234. It should work out that 2% TDS explains the difference."
        />
      ) : (
        data && (
          <Card>
            <CardHead
              title="What you have added"
              note={`${data.invoices.length} invoices, ${data.payments.length} payments · room for ${data.room_left} more`}
            />
            <div className="grid divide-y divide-ink-200 md:grid-cols-2 md:divide-x md:divide-y-0">
              <div className="divide-y divide-ink-100">
                {data.invoices.map((row) => (
                  <div key={row.id} className="flex items-baseline justify-between gap-3 px-5 py-2.5">
                    <span className="text-sm text-ink-1200">{row.customer}</span>
                    <span className="tnum whitespace-nowrap text-sm font-medium">
                      {row.amount.display}
                    </span>
                  </div>
                ))}
              </div>
              <div className="divide-y divide-ink-100">
                {data.payments.map((row) => (
                  <div key={row.id} className="flex items-baseline justify-between gap-3 px-5 py-2.5">
                    <span className="truncate font-mono text-xs text-ink-700">{row.bank_text}</span>
                    <span className="tnum whitespace-nowrap text-sm font-medium">
                      {row.amount.display}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-ink-200 px-5 py-3">
              <Button
                onClick={() => run(() => api.sandbox.match(), setResult)}
                disabled={busy}
              >
                {busy ? "Matching…" : "Match them"}
              </Button>
              <Button
                variant="quiet"
                disabled={busy}
                onClick={() =>
                  run(() => api.sandbox.clear(), (d) => {
                    setData(d);
                    setResult(null);
                  })
                }
              >
                Clear everything
              </Button>
            </div>
          </Card>
        )
      )}

      {result && <Results result={result} />}
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <label className="block">
      <Label>{label}</Label>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1.5 w-full rounded-md border border-ink-200 bg-ink-0 px-3 py-1.5 text-sm text-ink-1200 placeholder:text-ink-500 focus:border-brand-500"
      />
    </label>
  );
}

function AddInvoice({
  busy,
  onAdd,
}: {
  busy: boolean;
  onAdd: (body: Record<string, string>) => void;
}) {
  const [customer, setCustomer] = useState("");
  const [amount, setAmount] = useState("");
  const [due, setDue] = useState("");

  function submit() {
    if (!customer.trim() || !amount.trim()) return;
    onAdd({ customer, amount, due_date: due });
    setCustomer("");
    setAmount("");
    setDue("");
  }

  return (
    <Card>
      <CardHead title="Add an invoice" note="What you billed, and to whom." />
      <div className="space-y-3 px-5 py-4">
        <Field label="Customer" value={customer} onChange={setCustomer} placeholder="Bright Metals Pvt Ltd" />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Amount in rupees" value={amount} onChange={setAmount} placeholder="1,50,000" />
          <Field label="Due date (optional)" value={due} onChange={setDue} type="date" />
        </div>
        <Button onClick={submit} disabled={busy || !customer.trim() || !amount.trim()}>
          Add invoice
        </Button>
      </div>
    </Card>
  );
}

function AddPayment({
  busy,
  onAdd,
}: {
  busy: boolean;
  onAdd: (body: Record<string, string>) => void;
}) {
  const [bankText, setBankText] = useState("");
  const [amount, setAmount] = useState("");
  const [when, setWhen] = useState("");

  function submit() {
    if (!bankText.trim() || !amount.trim()) return;
    onAdd({ bank_text: bankText, amount, value_date: when });
    setBankText("");
    setAmount("");
    setWhen("");
  }

  return (
    <Card>
      <CardHead
        title="Add a payment"
        note="The line as it appears on your bank statement, mangled name and all."
      />
      <div className="space-y-3 px-5 py-4">
        <Field
          label="Bank narration"
          value={bankText}
          onChange={setBankText}
          placeholder="NEFT/BRIGHTMETALS/HDFC0001234"
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Amount received" value={amount} onChange={setAmount} placeholder="1,47,000" />
          <Field label="Date received (optional)" value={when} onChange={setWhen} type="date" />
        </div>
        <Button onClick={submit} disabled={busy || !bankText.trim() || !amount.trim()}>
          Add payment
        </Button>
      </div>
    </Card>
  );
}

function Results({ result }: { result: SandboxResult }) {
  if (!result.ran) {
    return (
      <Card className="px-5 py-4">
        <p className="text-sm text-ink-700">{result.why}</p>
      </Card>
    );
  }

  return (
    <Card>
      <CardHead
        title={`${result.settled} of ${result.records} closed on their own`}
        note={`Matched in ${result.seconds} seconds.`}
      />
      <div className="divide-y divide-ink-100">
        {result.results?.map((row) => (
          <div key={row.invoice_id} className="px-5 py-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2.5">
                <span className="text-sm font-medium text-ink-1200">{row.customer}</span>
                <StatusPill outcome={row.outcome} />
                {row.reason_code && <ReasonChip>{reasonText(row.reason_code)}</ReasonChip>}
              </div>
              <span className="tnum whitespace-nowrap text-sm font-medium">
                {row.amount.display}
              </span>
            </div>

            {row.amount_working && (
              <p className="mt-2 rounded-md bg-ink-50 px-3 py-2 text-sm text-ink-1200 ring-1 ring-inset ring-ink-200">
                {row.amount_working}
              </p>
            )}
            <p className="mt-1.5 text-xs leading-relaxed text-ink-700">{row.reason_text}</p>
            {row.matched.length > 0 && (
              <p className="mt-1 font-mono text-[11px] text-ink-600">
                {row.matched.map((m) => m.bank_text).join(" · ")}
              </p>
            )}
          </div>
        ))}
      </div>

      <div className="space-y-1.5 border-t border-ink-200 px-5 py-3">
        <p className="text-xs leading-relaxed text-ink-700">{result.assumption}</p>
        <p className="text-xs leading-relaxed text-ink-700">{result.note}</p>
      </div>
    </Card>
  );
}
