import { api, fmt } from "../api.js";
import { h, openModal, toast, toastError, confirmDialog } from "../ui.js";

let selectedId = null;
const field = (label, control) => h("div", { class: "field" }, h("label", {}, label), control);

export async function renderStocktake(main) {
  let sessions, active, products;
  try {
    [sessions, products] = await Promise.all([api.stocktakes(), api.listProducts()]);
    if (!selectedId && sessions.length) selectedId = sessions[0].id;
    if (selectedId) active = await api.stocktakeDetail(selectedId);
  } catch (e) { main.replaceChildren(h("p", {}, e.message)); return toastError(e); }
  const refresh = () => renderStocktake(main);
  const note = h("input", { class: "input", maxlength: "500", placeholder: "e.g. November aisle count / shelf 2" });
  main.replaceChildren(
    h("div", { class: "view-head" }, h("div", { class: "view-title" },
      h("h2", {}, "Physical Stocktake"), h("div", { class: "sub" },
        "Count actual units · Review differences · Post corrections deliberately")),
      h("a", { class: "btn", href: "#/inventory" }, "Inventory")),
    h("div", { class: "card", style: "padding:18px" },
      h("div", { class: "report-panel-title" }, h("h3", {}, "Stocktaking sessions")),
      h("div", { class: "form-grid" },
        field("New count note (optional)", note),
        h("div", { class: "form-grid-actions" },
          h("button", { class: "btn primary", onclick: async () => {
            try {const s = await api.startStocktake(note.value); selectedId = s.id; refresh();}
            catch(e){toastError(e);} }}, "+ Start count"))),
      h("div", { class: "chips", style: "margin-top:10px" },
        ...sessions.map(s => h("button", { class: `chip${selectedId===s.id?" active":""}`,
          onclick: () => {selectedId=s.id;refresh();} },
          `#${s.id} · ${s.status} · ${s.counted_skus} SKUs`)))),
    active ? activePanel(active, products, refresh) : h("div", { class: "empty-state card" },
      h("h3", {}, "No stocktake yet"), h("p", {}, "Start a count, then scan exact SKUs and record actual shelf quantities.")),
    h("p", { class: "report-note" }, "Only SKUs explicitly counted are adjusted. " +
      "Repeated EANs with different MRPs must be identified by their exact price-edition SKU. " +
      "Stock that changes after counting must be recounted before posting."));
}

function activePanel(s, products, refresh) {
  const bySku = new Map(products.map(p => [String(p.SKU).toLowerCase(),p]));
  const sku = h("input", { class: "input", list: "stocktake-skus", placeholder: "Exact SKU (not shared EAN)" });
  const qty = h("input", { class: "input", type: "number", step: "1", min: "0", placeholder: "Actual units counted" });
  const diff = s.lines.reduce((n,l)=>n+l.counted-l.expected,0);
  return h("div", { class: "card", style: "padding:18px" },
    h("div", { class: "report-panel-title" }, h("h3", {}, `Count #${s.id} · ${s.status}`),
      h("span", { class: "cell-sub" }, `${s.lines.length} counted SKUs · Net adjustment ${diff>0?"+":""}${diff} units`)),
    s.status==="DRAFT" ? h("div", {},
      h("datalist", { id: "stocktake-skus" }, ...products.map(p => h("option", { value:p.SKU },
        `${p.group_name || 'Product'} · ${fmt(p.price)}`))),
      h("div", { class: "form-grid" },
        field("SKU / MRP edition", sku), field("Counted units", qty),
        h("div", { class: "form-grid-actions" }, h("button", { class:"btn primary", onclick:async()=>{
          const p=bySku.get(sku.value.trim().toLowerCase());
          if(!p){toast("Choose a valid exact SKU.","warn");return;}
          try { await api.countStock(s.id,p.id,qty.value);refresh(); }
          catch(e){toastError(e);}
        }}, "Save count"))),
      h("p", { class: "form-hint" }, "Counting an SKU again updates the draft count. Zero is allowed and must be entered explicitly.")) : null,
    s.lines.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {},h("tr",{},...["SKU","Expected","Counted","Difference","Current stock"].map(t=>h("th",{},t)))),
      h("tbody",{},...s.lines.map(l=>h("tr",{},
        h("td",{},l.SKU),h("td",{class:"num"},String(l.expected)),
        h("td",{class:"num"},String(l.counted)),
        h("td",{class:"num"},`${l.counted-l.expected>0?"+":""}${l.counted-l.expected}`),
        h("td",{class:"num"},String(l.current_stock)))))))
      : h("p", {class:"form-hint"},"No items counted yet."),
    s.status==="DRAFT" && s.lines.length ? h("button", {class:"btn primary",onclick:async()=>{
      if(!await confirmDialog({title:"Post stocktake adjustments?",message:
        `This will set the ${s.lines.length} counted SKU balances to their verified physical counts. Uncounted SKUs stay unchanged.`,
        confirmLabel:"Post count"}))return;
      try{await api.postStocktake(s.id);toast("Stocktake posted and audited.","success");refresh();}
      catch(e){toastError(e);}
    }},"Approve & post adjustments") : null);
}
