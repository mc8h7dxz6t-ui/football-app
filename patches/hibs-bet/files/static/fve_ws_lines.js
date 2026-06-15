/**
 * FVE WebSocket lines client — merge mode=delta changed_markets into local state.
 *
 * Usage:
 *   const session = createFveLinesSession((msg) => renderLines(msg.lines));
 *   const ws = new WebSocket('wss://fve.example/ws/lines/Arsenal%20v%20Chelsea');
 *   ws.onmessage = (ev) => session.onMessage(JSON.parse(ev.data));
 */
(function (root) {
  function mergeChangedMarkets(shopped, changed) {
    const base = shopped && typeof shopped === 'object' ? JSON.parse(JSON.stringify(shopped)) : {};
    if (!changed || typeof changed !== 'object') return base;
    for (const [market, channels] of Object.entries(changed)) {
      if (!channels || typeof channels !== 'object') continue;
      base[market] = base[market] && typeof base[market] === 'object' ? base[market] : {};
      for (const [channel, quote] of Object.entries(channels)) {
        if (quote && typeof quote === 'object') base[market][channel] = { ...quote };
      }
    }
    return base;
  }

  function applyLineUpdate(lines, message) {
    const out = lines && typeof lines === 'object' ? { ...lines } : {};
    const mode = (message.mode || '').toLowerCase();
    if (mode === 'full' && message.lines && typeof message.lines === 'object') {
      return { ...out, ...message.lines };
    }
    if (mode === 'delta' || message.changed_markets) {
      out.shopped = mergeChangedMarkets(out.shopped, message.changed_markets);
      if (message.tick_count != null) out.tick_count = message.tick_count;
      if (message.sharp_fair_probs != null) out.sharp_fair_probs = message.sharp_fair_probs;
      if (!out.fixture_key && message.fixture_key) out.fixture_key = message.fixture_key;
      return out;
    }
    if (message.lines && typeof message.lines === 'object') return { ...out, ...message.lines };
    return out;
  }

  function createFveLinesSession(onUpdate) {
    const state = { fixtureKey: '', lines: {}, bundle: {} };
    return {
      onMessage(message) {
        const type = message && message.type;
        if (type === 'snapshot') {
          state.fixtureKey = message.fixture_key || '';
          state.bundle = message;
          state.lines = message.lines && typeof message.lines === 'object' ? { ...message.lines } : {};
          if (typeof onUpdate === 'function') onUpdate({ ...message, lines: state.lines });
          return state.lines;
        }
        if (type === 'line_update') {
          state.lines = applyLineUpdate(state.lines, message);
          const merged = { ...message, lines: state.lines };
          if (typeof onUpdate === 'function') onUpdate(merged);
          return state.lines;
        }
        if (typeof onUpdate === 'function') onUpdate(message);
        return state.lines;
      },
      getLines() {
        return state.lines;
      },
    };
  }

  root.createFveLinesSession = createFveLinesSession;
  root.applyFveLineUpdate = applyLineUpdate;
})(typeof globalThis !== 'undefined' ? globalThis : typeof window !== 'undefined' ? window : this);
