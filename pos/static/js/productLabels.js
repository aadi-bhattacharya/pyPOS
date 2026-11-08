// Customer-facing attributes are an explicit allowlist. Source sheets, costs,
// tax review notes, import warnings and arbitrary internal attributes remain
// editable in Catalog but never appear on the register or receipt.
const PUBLIC = new Set([
  "colour", "color", "size", "fit", "fabric", "pattern", "pack", "length", "waist",
]);
const ORDER = ["colour", "color", "size", "fit", "fabric", "pattern", "pack", "length", "waist"];
export function publicAttributes(attributes = []) {
  return attributes.filter(a => PUBLIC.has(String(a.name).toLowerCase()) && a.value)
    .sort((a, b) => ORDER.indexOf(a.name.toLowerCase()) - ORDER.indexOf(b.name.toLowerCase()));
}
export function publicVariant(attributes = []) {
  return publicAttributes(attributes).map(a => `${a.name}: ${a.value}`).join(" · ");
}
export function needsBillingReview(attributes = []) {
  return attributes.some(a => ["gst review required", "price review required", "manager review required"].includes(
    String(a.name).toLowerCase()));
}
export function billingReviewLabel(attributes = []) {
  const names = new Set(attributes.map(a => String(a.name).toLowerCase()));
  return names.has("gst review required") ? "GST review required" :
    names.has("price review required") ? "Price review required" :
    names.has("manager review required") ? "Manager review needed" : "";
}
