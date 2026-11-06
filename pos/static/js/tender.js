// The same non-cash-first tender allocation policy used by pos.sales._normalize_payments.
// Integer paise avoid inconsistent floating-point comparisons in the register.
const cents = (n) => {
  const v = Number(n);
  return Number.isFinite(v) && v >= 0 ? Math.round(v * 100) : NaN;
};
export function tenderPreview(due, payments) {
  const dueCents = cents(due);
  if (!Number.isSafeInteger(dueCents)) return { valid: false, error: "Invalid amount due" };
  let noncash = 0, cash = 0;
  for (const p of payments) {
    const amount = cents(p.amount);
    if (!Number.isSafeInteger(amount) || !["CASH", "CARD", "UPI"].includes(p.method))
      return { valid: false, error: "Invalid payment row" };
    if (p.method === "CASH") cash += amount;
    else noncash += amount;
  }
  if (noncash > dueCents) return {
    valid: false, error: "Card/UPI exceeds the bill. Only cash can give change.",
    remaining: 0, change: 0,
  };
  const remaining = Math.max(0, dueCents - noncash - cash);
  const change = Math.max(0, noncash + cash - dueCents);
  return { valid: true, covered: remaining === 0, remaining: remaining / 100,
    change: change / 100, noncash: noncash / 100, cash: cash / 100 };
}
