// EAN-13 barcodes are rendered without a remote library or third-party fonts.
import { h } from "./ui.js";
import { state, fmt } from "./api.js";
import { publicVariant } from "./productLabels.js";
const L = ["0001101","0011001","0010011","0111101","0100011","0110001","0101111","0111011","0110111","0001011"];
const G = ["0100111","0110011","0011011","0100001","0011101","0111001","0000101","0010001","0001001","0010111"];
const R = ["1110010","1100110","1101100","1000010","1011100","1001110","1010000","1000100","1001000","1110100"];
const PARITY = ["LLLLLL","LLGLGG","LLGGLG","LLGGGL","LGLLGG","LGGLLG","LGGGLL","LGLGLG","LGLGGL","LGGLGL"];
export function ean13Bits(ean) {
  const code = String(ean || "");
  if (!/^\d{13}$/.test(code)) throw new Error("EAN-13 labels require the exact 13-digit barcode");
  const d = [...code].map(Number);
  const check = (10 - d.slice(0,12).reduce((n,x,i)=>n+x*(i%2?3:1),0)%10)%10;
  if (check!==d[12]) throw new Error("EAN-13 check digit does not match; correct the barcode first");
  let bits="101";
  for (let i=1;i<=6;i++) bits += (PARITY[d[0]][i-1]==="L"?L:G)[d[i]];
  bits += "01010";
  for (let i=7;i<=12;i++) bits += R[d[i]];
  return bits+"101";
}
export function labelElement(product) {
  const code = product.barcode;
  const bits = ean13Bits(code);
  const svg = document.createElementNS("http://www.w3.org/2000/svg","svg");
  svg.setAttribute("viewBox","0 0 95 52");
  svg.setAttribute("aria-label",`EAN ${code}`);
  svg.setAttribute("role","img");
  svg.style.width="100%";
  svg.style.display="block";
  for (let i=0;i<bits.length;i++) if(bits[i]==="1") {
    const bar=document.createElementNS("http://www.w3.org/2000/svg","rect");
    bar.setAttribute("x",String(i));bar.setAttribute("y","0");
    bar.setAttribute("width","1");bar.setAttribute("height",(i<3 || (i>=45&&i<50)||i>=92)?"46":"40");
    bar.setAttribute("fill","#000");svg.append(bar);
  }
  return h("div",{class:"barcode-label"},
    h("div",{class:"barcode-store"},state.settings.pos_display_name || state.settings.store_name || "Store"),
    h("div",{class:"barcode-title"},String(product.group_name || product.SKU).slice(0,60)),
    h("div",{class:"barcode-sub"},`${publicVariant(product.attributes||[])} · ${product.SKU}`.slice(0,76)),
    svg,h("div",{class:"barcode-digits"},code),
    h("div",{class:"barcode-price"},`MRP ${fmt(product.price)}`));
}
export function printProductLabels(product,qty=1) {
  const n=Number(qty);
  if(!Number.isSafeInteger(n)||n<1||n>100)throw new Error("Print 1–100 labels at once");
  const root=document.getElementById("print-root");
  root.dataset.paper="labels";
  root.replaceChildren(h("div",{class:"label-sheet"},...Array.from({length:n},()=>labelElement(product))));
  window.print();
}
