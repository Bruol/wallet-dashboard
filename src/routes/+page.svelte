<script>
  import { onDestroy, onMount } from 'svelte';

  const example = JSON.stringify(
    {
      merchant: 'Example Grocery',
      amount: 12.4,
      currency: 'CHF',
      date: new Date().toISOString(),
      category: 'Groceries',
      card: 'Apple Wallet'
    },
    null,
    2
  );

  let status = 'connecting…';
  let summary = null;
  let transactions = [];
  let source = null;

  const fmt = (n, currency = 'CHF') =>
    new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(Number(n || 0));

  const fmtMulti = (totals) =>
    Object.entries(totals || {})
      .map(([currency, amount]) => fmt(amount, currency))
      .join(' + ') || '—';

  const fmtChfTotal = (s) =>
    Number.isFinite(Number(s?.total_chf)) ? fmt(s.total_chf, s.display_currency || 'CHF') : fmtMulti(s?.totals_by_currency);

  const fmtMonth = (s, ym) =>
    Number.isFinite(Number((s?.by_month_chf || {})[ym]))
      ? fmt(Math.abs(s.by_month_chf[ym]), s.display_currency || 'CHF')
      : fmtMulti(
          Object.fromEntries(
            Object.entries((s?.by_month_currency || {})[ym] || {}).map(([currency, cents]) => [
              currency,
              Math.abs(cents / 100)
            ])
          )
        );

  const dateFmt = (s) => (s ? new Date(s).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'No date');
  const escapeHtml = (s) =>
    String(s).replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[c]);

  async function load() {
    const [summaryRes, txRes] = await Promise.all([fetch('/api/summary'), fetch('/api/transactions')]);
    summary = await summaryRes.json();
    const txPayload = await txRes.json();
    transactions = txPayload.transactions || [];
  }

  onMount(() => {
    load().catch((err) => {
      status = 'load failed';
      console.error(err);
    });

    source = new EventSource('/events');
    source.onopen = () => (status = 'live');
    source.onerror = () => (status = 'reconnecting…');
    source.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'transaction') load();
      } catch {
        // Ignore malformed keepalive-ish messages.
      }
    };
  });

  onDestroy(() => source?.close());

  $: displayCurrency = summary?.display_currency || summary?.currency || 'CHF';
  $: currentMonth = new Date().toISOString().slice(0, 7);
  $: chartEntries = Object.entries(summary?.by_month_chf || {})
    .sort(([a], [b]) => a.localeCompare(b))
    .slice(-12);
  $: chartMax = Math.max(...chartEntries.map(([, amount]) => Math.abs(amount)), 1);
  $: merchantEntries = Object.entries(summary?.by_merchant || {}).slice(0, 10);
</script>

<svelte:head>
  <title>Wallet Expenses</title>
</svelte:head>

<main>
  <header>
    <div>
      <h1>Wallet Expenses</h1>
      <div class="subtitle">Live dashboard from your iPhone Shortcut webhook</div>
    </div>
    <div id="status" class="pill">{status}</div>
  </header>

  <section class="grid">
    <div class="card stat">
      <div class="label">Total spend</div>
      <div id="total" class="value">{summary ? fmtChfTotal(summary) : '—'}</div>
    </div>
    <div class="card stat">
      <div class="label">Transactions</div>
      <div id="count" class="value">{summary?.count || 0}</div>
    </div>
    <div class="card stat">
      <div class="label">This month</div>
      <div id="month" class="value small">{summary ? fmtMonth(summary, currentMonth) : '—'}</div>
    </div>

    <div class="card wide">
      <div class="label">Monthly trend</div>
      <div id="chart" class="chart">
        {#if chartEntries.length}
          {#each chartEntries as [month, amount]}
            <div class="barwrap" title={`${month}: ${fmt(Math.abs(amount), displayCurrency)}`}>
              <div class="bar" style={`height:${Math.max(4, (Math.abs(amount) / chartMax) * 220)}px`}></div>
              <div class="barlabel">{month.slice(5) || month}</div>
            </div>
          {/each}
        {:else}
          <div class="empty">No transactions yet</div>
        {/if}
      </div>
    </div>

    <div class="card side">
      <div class="label">Top merchants</div>
      <div id="merchants">
        {#if merchantEntries.length}
          {#each merchantEntries as [merchantKey, cents]}
            {@const parts = merchantKey.split(' · ')}
            {@const currency = parts.length > 1 ? parts.at(-1) : displayCurrency}
            {@const label = parts.length > 1 ? parts.slice(0, -1).join(' · ') : merchantKey}
            <div class="row">
              <div class="merchant">{label}</div>
              <div class="amount">{fmt(Math.abs(cents / 100), currency)}</div>
            </div>
          {/each}
        {:else}
          <div class="empty">No merchants yet</div>
        {/if}
      </div>
    </div>

    <div class="card full">
      <div class="label">Latest transactions</div>
      <div id="txs">
        {#if transactions.length}
          {#each transactions.slice(0, 50) as transaction}
            <div class="row">
              <div>
                <div class="merchant">{transaction.merchant || 'Unknown merchant'}</div>
                <div class="meta">
                  {dateFmt(transaction.transaction_date || transaction.received_at)}{transaction.category ? ` · ${transaction.category}` : ''}{transaction.card ? ` · ${transaction.card}` : ''}
                </div>
              </div>
              <div class:negative={Number(transaction.amount) < 0} class="amount">
                {fmt(Math.abs(transaction.amount || 0), transaction.currency || displayCurrency)}
              </div>
            </div>
          {/each}
        {:else}
          <div class="empty">Waiting for first webhook…</div>
        {/if}
      </div>
    </div>

    <div class="card full">
      <div class="label">Webhook endpoint</div>
      <p class="meta">
        POST JSON to <code>/webhook</code>. Common Apple Wallet/Shortcut keys are auto-normalized: amount,
        merchant, date, currency, category, card.
      </p>
      <pre id="example">{example}</pre>
    </div>
  </section>
</main>

<style>
  :global(:root) {
    color-scheme: dark;
    --bg: #0b1020;
    --card: #121a2f;
    --card2: #18223d;
    --text: #eef4ff;
    --muted: #9fb0d0;
    --accent: #72e6ac;
    --warn: #ffcc66;
    --line: #293655;
  }

  :global(*) {
    box-sizing: border-box;
  }

  :global(body) {
    margin: 0;
    font-family:
      ui-sans-serif,
      system-ui,
      -apple-system,
      BlinkMacSystemFont,
      'Segoe UI',
      sans-serif;
    background: radial-gradient(circle at top left, #1b2b58 0, #0b1020 38rem);
    color: var(--text);
  }

  main {
    max-width: 1180px;
    margin: 0 auto;
    padding: 28px 18px 48px;
  }

  header {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: flex-start;
    margin-bottom: 22px;
  }

  h1 {
    margin: 0;
    font-size: clamp(28px, 5vw, 48px);
    letter-spacing: -0.04em;
  }

  .subtitle {
    color: var(--muted);
    margin-top: 6px;
  }

  .pill {
    background: rgba(114, 230, 172, 0.12);
    color: var(--accent);
    border: 1px solid rgba(114, 230, 172, 0.35);
    border-radius: 999px;
    padding: 8px 12px;
    font-size: 13px;
    white-space: nowrap;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(12, 1fr);
    gap: 14px;
  }

  .card {
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.02));
    border: 1px solid rgba(255, 255, 255, 0.08);
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.22);
    border-radius: 22px;
    padding: 18px;
    overflow: hidden;
  }

  .stat {
    grid-column: span 4;
    min-height: 130px;
  }

  .wide {
    grid-column: span 8;
  }

  .side {
    grid-column: span 4;
  }

  .full {
    grid-column: 1 / -1;
  }

  .label {
    color: var(--muted);
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
  }

  .value {
    font-size: clamp(30px, 4vw, 52px);
    font-weight: 800;
    letter-spacing: -0.05em;
    margin-top: 10px;
  }

  .value.small {
    font-size: 34px;
  }

  .chart {
    min-height: 260px;
    display: flex;
    align-items: end;
    gap: 9px;
    padding-top: 24px;
  }

  .barwrap {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 9px;
    align-items: center;
    min-width: 0;
  }

  .bar {
    width: 100%;
    border-radius: 12px 12px 4px 4px;
    min-height: 4px;
    background: linear-gradient(180deg, var(--accent), #4a92ff);
    box-shadow: 0 0 30px rgba(114, 230, 172, 0.25);
  }

  .barlabel {
    color: var(--muted);
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
  }

  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  }

  .row:last-child {
    border-bottom: 0;
  }

  .merchant {
    font-weight: 650;
  }

  .meta {
    color: var(--muted);
    font-size: 13px;
    margin-top: 3px;
  }

  .amount {
    font-variant-numeric: tabular-nums;
    font-weight: 800;
    color: var(--accent);
    white-space: nowrap;
  }

  .amount.negative {
    color: #ff8e8e;
  }

  pre {
    white-space: pre-wrap;
    overflow: auto;
    background: #07101f;
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 12px;
    color: #bdd0ff;
  }

  code {
    color: #bdd0ff;
  }

  .empty {
    color: var(--muted);
    padding: 24px 0;
  }

  @media (max-width: 780px) {
    header {
      display: block;
    }

    .pill {
      display: inline-block;
      margin-top: 14px;
    }

    .stat,
    .wide,
    .side {
      grid-column: 1 / -1;
    }

    .chart {
      min-height: 180px;
    }
  }
</style>
