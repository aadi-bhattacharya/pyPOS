# pyPOS loyalty points (India Edition)

Customer loyalty is an offline, per-shop points system added on top of the existing SQLite customer account and store-credit wallet. It does **not** contact any third-party loyalty platform.

## Enable and configure

1. **Back up the database** before upgrading. Start the updated app normally; the migration adds `customers.loyalty_points` and `loyalty_events` without resetting existing customers or store-credit balances.
2. Go to **Settings > Admin password** and configure an administrator password (required before adjusting or redeeming points). Log in as the administrator.
3. Go to **Settings > Customer loyalty programme** and configure:
   - **Enable loyalty points** (on by default, can be disabled).
   - **Eligible spend per reward (₹)**: default **100**. Only whole multiples of this amount earn points.
   - **Points earned per spend block**: default **1**.
   - **Store credit value per point (paise)**: default **100** (₹1 per point). Set an integer; 50 gives ₹0.50 per point.
4. **Save loyalty settings**. New earning/redemption transactions use the current policy. Old transactions and stored point balances are not recalculated.

### Worked example

A customer is attached to a sale of ₹549.00 and pays ₹549 via UPI. With default rules, `floor(549/100) * 1 = 5` points are earned. If the customer converts five points later, the system subtracts five points and adds **₹5.00** to their store-credit wallet. The cashier can use this wallet on their next purchase with the normal **Use store credit** checkbox. The GST-bearing invoice total stays the same; credit only offsets the amount to be collected.

If the same customer pays ₹100 on a bill with ₹20 of store credit and ₹80 of cash, the eligible spend for new points is **₹80**, so no point is earned at the default ₹100 threshold. This prevents repeated points earning on redeemed benefits.

## Edit balances as the admin

1. Go to **Customers**. Find or open the customer's details and choose **Points** / **Manage points**.
2. Choose **Add points**, **Deduct points**, **Set exact balance**, or **Convert points to store credit**.
3. Type whole points. Manual changes require an explanatory **reason**. Conversion uses the configured value and is atomic: points are deducted and store credit is added in one database transaction.
4. The customer's detail screen shows point balance and newest-first loyalty history. The **Loyalty CSV** link exports the audit ledger for administrators only.

For each point movement the ledger stores the UTC timestamp, customer, signed number of points, resulting balance, type, reason, and linked sale when relevant. This is designed for traceability, not for edits to old entries; balance corrections create new entries. A customer with any recorded loyalty activity cannot be deleted through the app, preserving the audit history.

**Admin access:** Points adjustments/conversions require an admin password **already configured** and a valid logged-in administrator session. Cashiers can view points and award them through normal sales, but cannot edit or convert them via the API. The rest of the app retains its existing permissions; this feature does not create separate employee logins or an enterprise permission system.

## Refunds, voids and negative balances

- A successful customer-linked checkout awards points within the same transaction as payment and inventory changes. Failed checkouts award nothing.
- Refunds reverse the award from the original sale **proportionally** using cumulative returned value. All returns eventually reverse every point earned on that sale.
- A void of a not-yet-refunded sale fully reverses its earned points and restores stock.
- If a customer has already converted or spent the points before an original transaction is refunded, the refund is **not blocked**. The point balance can go temporarily negative; this represents an obligation that future earned points automatically offset. Admins can set it back to zero if there is a justified correction, with a recorded reason. Redemptions and discretionary deductions cannot create a negative balance.
- Prior manual point adjustments and redemptions are not themselves undone by a product refund. The redemption wallet and the original sale's points award are separate transactions.

## Receipts, exports and compliance

Receipts show loyalty points earned or reversed when applicable. A conversion to store credit is visible in the customer's credit and loyalty ledgers; the next customer receipt prints applied store credit using the existing billing functionality.

**GST consideration:** Loyalty points and store credit are treated operationally as a means to settle payment, **not** as a reduction of the taxable selling price. This is an application policy, not a certification that all reward schemes can be treated identically for GST. Ask your GST professional to validate promotional-benefit, voucher and turnover reporting for your business. No IRN, GST credit note, GSTR filing or automatic accounting treatment of outstanding loyalty liabilities is added by this feature.

**Operational safety:** This release requires on-device testing with your real data and hardware before live usage. Existing backups include both loyalty and store-credit tables. The app does not sync points across multiple separate installations.
