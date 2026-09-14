// Researcher-only account visualization, with no trade submission controls.
export function renderTradingAccount(parent, account, history, helpers) {
  const {addText, clearNode} = helpers;
  clearNode(parent);
  const fmt = (value) => value === null || value === undefined ? '未估值' : Number(value).toFixed(2);
  addText(parent, 'h3', `模拟现金账户 · ${account.currency}`);
  const metrics = document.createElement('div'); metrics.className = 'account-metrics';
  for (const [label, value] of [
    ['总资产', account.equity], ['可用现金', account.available_cash],
    ['订单预留', account.reserved_cash], ['待交收应收', account.receivable],
    ['持仓市值', account.market_value], ['累计费用', account.fees],
    ['已实现净盈亏', account.realized_pnl_net], ['未实现净盈亏', account.unrealized_pnl_net],
  ]) {
    const cell = document.createElement('div');
    addText(cell, 'span', label, 'muted'); addText(cell, 'strong', fmt(value)); metrics.appendChild(cell);
  }
  parent.appendChild(metrics);
  addText(parent, 'p', `自开局收益率：${account.return_pct === null ? '未估值' : fmt(account.return_pct) + '%'}`);
  addText(parent, 'p', '买卖均为模拟；应收款不算可用现金，未交收份额不能卖出。', 'muted');
  const points = (history || []).filter(p => p.equity !== null && Number.isFinite(p.equity));
  if (points.length) {
    addText(parent, 'h4', '资产轨迹（阶段起点至当前动作）');
    if (account.equity !== null && (!points.length || points[points.length - 1].equity !== account.equity)) {
      points.push({equity: account.equity, as_of: account.as_of});
    }
    const ns = 'http://www.w3.org/2000/svg';
    const el = (tag, attrs) => {
      const node = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
      return node;
    };
    const svg = el('svg', {viewBox: '0 0 300 116', role: 'img', 'aria-label': '该agent的模拟资产轨迹'});
    svg.style.width = '100%';
    const values = points.map(p => p.equity), min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    const xy = points.map((p, i) => [10 + i * 280 / Math.max(1, points.length - 1),
      max === min ? 54 : 88 - (p.equity - min) * 66 / span]);
    svg.appendChild(el('polyline', {points: xy.map(p => p.join(',')).join(' '), fill: 'none', stroke: '#4d8178', 'stroke-width': 2.5}));
    xy.forEach(([x, y], i) => {
      const dot = el('circle', {cx: x, cy: y, r: 3, fill: '#4d8178'});
      const title = el('title', {}); title.textContent = `${points[i].as_of}: ${fmt(points[i].equity)} ${account.currency}`;
      dot.appendChild(title); svg.appendChild(dot);
    });
    const label = el('text', {x: 10, y: 111, fill: '#6f7773', 'font-size': 10});
    label.textContent = `${fmt(min)} — ${fmt(max)} ${account.currency} · 截至所选动作，不含未来阶段`;
    svg.appendChild(label); parent.appendChild(svg);
  }
  addText(parent, 'h4', '持仓与可卖份额');
  for (const position of account.positions) {
    addText(parent, 'p', `${position.market} · ${position.instrument_id}`);
    addText(parent, 'p', `持有 ${position.units} · 可卖 ${position.available_units}`);
    addText(parent, 'p', `成本 ${fmt(position.cost_basis)} · 市值 ${fmt(position.market_value)} ${account.currency}`, 'muted');
  }
  if (!account.positions.length) addText(parent, 'p', '当前无经济持仓');
  if (account.missing_valuations.length) addText(parent, 'p', '有持仓缺少可见价格，总资产与收益率保持未知。');
  const statusNames = {submitted: '已提交 / 已预留', filled: '已成交 / 待交收', settled: '已交收', cancelled: '已撤单', rejected: '成交拒绝'};
  const kinds = {subscribe: '申购', redeem: '赎回', buy: '买入', sell: '卖出'};
  const orders = document.createElement('details');
  orders.open = true;
  addText(orders, 'summary', `自己的订单（${account.orders.length}）`);
  for (const order of account.orders) {
    const item = document.createElement('div'); item.className = 'order-row';
    addText(item, 'strong', `${order.order_id} · ${kinds[order.kind]} · ${statusNames[order.status] || order.status}`);
    addText(item, 'p', `${order.instrument_id} · ${order.side === 'buy' ? '预算 ' + fmt(order.amount) : '份额 ' + order.units}`);
    if (order.price !== undefined) addText(item, 'p', `成交价 ${order.price} · 费用 ${fmt(order.fee)} ${order.currency}`);
    addText(item, 'p', `交收安排：${order.window.settle_at}`, 'muted');
    orders.appendChild(item);
  }
  if (!account.orders.length) addText(orders, 'p', '尚未提交订单');
  parent.appendChild(orders);
  const ledger = document.createElement('details');
  addText(ledger, 'summary', `展开自己的账本（${account.ledger.length}条）`);
  for (const event of account.ledger) {
    addText(ledger, 'p', `${event.at} · ${event.order_id} · ${statusNames[event.event] || event.event}`);
    if (event.cash_delta !== undefined) addText(ledger, 'p', `现金变化 ${fmt(event.cash_delta)} ${event.currency}`);
  }
  parent.appendChild(ledger);
}
